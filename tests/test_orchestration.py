"""The supervisor graph, run end to end with the scripted model. No test calls the OpenAI API."""

import pytest
from langgraph.types import Command
from test_agents import ANALYSIS, ScriptedModel, calls, plan_with

from engineering_agents import cli
from engineering_agents.analysis import INSTRUCTIONS as ANALYSIS_INSTRUCTIONS
from engineering_agents.orchestration import (
    Context,
    build_graph,
    initial_state,
    memory_checkpointer,
    validator,
)
from engineering_agents.planning import INSTRUCTIONS as PLANNING_INSTRUCTIONS

PLAN_B = plan_with("ex:LevelTransmitterB")  # breaks the zone 1 rule at LT-101
PLAN_C = plan_with("ex:LevelTransmitterC")  # conforms


def analysis_replies():
    return [
        calls(("find_installation_points", {"device_type": "LevelTransmitterA"})),
        calls(("Analysis", ANALYSIS.model_dump())),
    ]


def planning_replies(plan):
    return [
        calls(("list_type_approvals", {"device_type": "LevelTransmitterB"})),
        calls(("ReplacementPlan", plan.model_dump())),
    ]


CONFIG = {"configurable": {"thread_id": "test"}}


class Run:
    """One run of the graph on one thread, which can pause at the approval and be resumed."""

    def __init__(self, model, max_planning_rounds=3):
        self.graph = build_graph(checkpointer=memory_checkpointer())
        self.context = Context(model=model, max_planning_rounds=max_planning_rounds)

    def start(self):
        return self.graph.invoke(initial_state(), CONFIG, context=self.context)

    def resume(self, decision):
        return self.graph.invoke(Command(resume=decision), CONFIG, context=self.context)


def nodes(state):
    return [entry["node"] for entry in state["trace"]]


def test_rejected_plan_goes_back_to_planning_and_pauses_for_approval_after_revision():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    state = Run(model).start()

    assert state["status"] == "conforms"
    assert state["plan"] == PLAN_C
    assert state["violations"] == []
    assert state["planning_rounds"] == 2
    assert nodes(state) == [
        "supervisor", "analysis",
        "supervisor", "planning",
        "supervisor", "validator",  # LT-101 with type B in zone 1: rejected
        "supervisor", "planning",  # revision with the violation as feedback
        "supervisor", "validator",  # conforms
        "supervisor",  # -> approval, which pauses before it writes anything
    ]  # fmt: skip
    assert "does not conform: 1 violation(s)" in state["trace"][5]["note"]
    assert state["trace"][-1]["note"].startswith("-> approval:")

    # The second planning run was given the rejected plan and the violation.
    revision_task = model.conversations[4][1].content
    assert "Your previous plan:" in revision_task
    assert "LT-101 at T-101 Solvent storage tank (Zone 1)" in revision_task
    assert "not approved for Zone 1" in revision_task


def test_the_run_pauses_at_the_approval_interrupt_with_a_readable_summary():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    run = Run(model)
    state = run.start()

    [pause] = state["__interrupt__"]
    assert pause.value == (
        "Approval needed: replacement plan, round 2, 4 device(s)\n"
        "  ex:LT-101  Zone 1         ex:LevelTransmitterA -> ex:LevelTransmitterC\n"
        "  ex:LT-102  Non-hazardous  ex:LevelTransmitterA -> ex:LevelTransmitterB\n"
        "  ex:LT-103  Zone 2         ex:LevelTransmitterA -> ex:LevelTransmitterB\n"
        "  ex:LT-301  Non-hazardous  ex:LevelTransmitterA -> ex:LevelTransmitterB\n"
        "Summary: Replace type A.\n"
        "History:\n"
        "  round 1: rejected by the SHACL validator\n"
        "    - LT-101 at T-101 Solvent storage tank (Zone 1): "
        "type Level transmitter type B is not approved for Zone 1.\n"
        "  round 2: conforms to the SHACL rules"
    )
    # The checkpointer saved the paused run: the approval node is next.
    snapshot = run.graph.get_state(CONFIG)
    assert snapshot.next == ("approval",)
    assert snapshot.interrupts[0].value == pause.value


def test_approval_resumes_the_run_to_hand_over():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    run = Run(model)
    run.start()
    state = run.resume({"approved": True})

    assert state["status"] == "handed_over"
    assert "__interrupt__" not in state
    assert [(e["node"], e["note"]) for e in state["trace"][-4:]] == [
        ("supervisor", "-> approval: the plan conforms to all rules; a person must approve it"),
        ("approval", "approved by the reviewer"),
        ("supervisor", "-> hand_over: the reviewer approved the plan"),
        ("hand_over", "plan with 4 entries handed over"),
    ]
    assert state["history"][-1] == "round 2: approved by the reviewer"
    assert len(model.conversations) == 6  # resuming made no extra model call


def test_rejection_sends_the_plan_back_to_planning_with_the_comment():
    model = ScriptedModel(
        *analysis_replies(),
        *planning_replies(PLAN_B),
        *planning_replies(PLAN_C),
        *planning_replies(PLAN_C),  # the revision after the reviewer's comment
    )
    run = Run(model)
    run.start()
    state = run.resume({"approved": False, "comment": "Order LT-103 with a longer probe."})

    # Back to planning (round 3), through the validator, and paused for approval again.
    assert state["review_comment"] == "Order LT-103 with a longer probe."
    assert state["planning_rounds"] == 3
    assert state["status"] == "conforms"
    assert "__interrupt__" in state
    assert [(e["node"], e["note"]) for e in state["trace"][-6:]] == [
        ("approval", "rejected by the reviewer: Order LT-103 with a longer probe."),
        ("supervisor", "-> planning: the reviewer rejected the plan; send it back with why"),
        ("planning", "revised the plan (round 3)"),
        ("supervisor", "-> validator: a new plan must be checked against the rules"),
        ("validator", "conforms"),
        ("supervisor", "-> approval: the plan conforms to all rules; a person must approve it"),
    ]
    assert "round 2: rejected by the reviewer: Order LT-103" in state["__interrupt__"][0].value

    # The planning agent got the comment in its task.
    task = model.conversations[6][1].content
    assert "A reviewer rejected an earlier plan with this comment:\n" in task
    assert "Order LT-103 with a longer probe." in task


def test_a_rejection_counts_as_a_planning_round():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    run = Run(model, max_planning_rounds=2)
    run.start()
    state = run.resume({"approved": False, "comment": "No."})

    assert state["status"] == "needs_human"
    assert state["trace"][-1]["note"] == "stop: 2 plans rejected, a person must decide"


def test_supervisor_stops_with_needs_human_after_max_planning_rounds():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_B))
    state = Run(model, max_planning_rounds=2).start()

    assert state["status"] == "needs_human"
    assert state["planning_rounds"] == 2
    assert [v.device for v in state["violations"]] == ["ex:LT-101"]
    assert nodes(state) == [
        "supervisor", "analysis",
        "supervisor", "planning",
        "supervisor", "validator",
        "supervisor", "planning",
        "supervisor", "validator",
        "supervisor",  # stops instead of a third planning round; no hand-over
    ]  # fmt: skip
    assert state["trace"][-1]["note"] == "stop: 2 plans rejected, a person must decide"


def test_validator_is_plain_code_without_a_model():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    Run(model).start()
    # Six model calls: two for the analysis agent, two for each planning round.
    # Every one of them came from an agent, none from the validator.
    systems = [conversation[0].content for conversation in model.conversations]
    assert systems == [ANALYSIS_INSTRUCTIONS] * 2 + [PLANNING_INSTRUCTIONS] * 4

    # The validator node is an ordinary function of the state alone.
    update = validator({"plan": PLAN_B})
    assert update["status"] == "rejected"
    assert [v.device for v in update["violations"]] == ["ex:LT-101"]
    assert validator({"plan": PLAN_C})["status"] == "conforms"


def test_validator_rejects_a_plan_with_an_unknown_type():
    update = validator({"plan": plan_with("ex:LevelTransmitterZ")})
    assert update["status"] == "rejected"
    assert "unknown device type" in update["violations"][0].message


def test_cli_run_streams_each_node(monkeypatch, capsys):
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    monkeypatch.setattr(cli, "make_model", lambda: model)
    cli.main(["run", "--auto-approve"])
    out = capsys.readouterr().out

    assert "[supervisor] -> analysis: no analysis yet" in out
    assert 'tool call: find_installation_points({"device_type": "LevelTransmitterA"})' in out
    assert "[analysis] found 4 device(s): ex:LT-101 (Zone 1)" in out
    assert "[planning] proposed a plan (round 1)" in out
    assert "[validator] does not conform: 1 violation(s)" in out
    assert "    - LT-101 at T-101 Solvent storage tank (Zone 1)" in out
    assert "[planning] revised the plan (round 2)" in out
    assert "[validator] conforms" in out
    assert "Approval needed: replacement plan, round 2, 4 device(s)" in out
    assert "--auto-approve: approved without asking" in out
    assert "[approval] approved by the reviewer" in out
    assert "[hand_over] plan with 4 entries handed over" in out
    assert out.rstrip().endswith("Plan: Replace type A.")
    assert "Status: handed_over" in out


def test_cli_run_asks_in_the_terminal_and_sends_a_rejection_back(monkeypatch, capsys):
    model = ScriptedModel(
        *analysis_replies(),
        *planning_replies(PLAN_B),
        *planning_replies(PLAN_C),
        *planning_replies(PLAN_C),
    )
    monkeypatch.setattr(cli, "make_model", lambda: model)
    answers = iter(["maybe", "r", "Check the probe length.", "a"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    cli.main(["run"])
    out = capsys.readouterr().out

    assert out.count("Approval needed:") == 2
    assert "[approval] rejected by the reviewer: Check the probe length." in out
    assert "[planning] revised the plan (round 3)" in out
    assert "[approval] approved by the reviewer" in out
    assert "Status: handed_over" in out


def test_cli_run_without_a_terminal_stops_at_the_approval(monkeypatch, capsys):
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    monkeypatch.setattr(cli, "make_model", lambda: model)

    def no_terminal(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_terminal)
    with pytest.raises(SystemExit, match="Use --auto-approve"):
        cli.main(["run"])
    assert "[hand_over]" not in capsys.readouterr().out


def test_cli_demo_falls_back_to_the_offline_model_without_a_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    cli.main(["demo", "--auto-approve"])
    out = capsys.readouterr().out

    assert "Mode: offline - a scripted stand-in plays the language model " in out
    assert "(OPENAI_API_KEY is not set)" in out
    steps = [line for line in out.splitlines() if line.startswith("== Step")]
    assert [step.split(" - ")[1].split(":")[0] for step in steps] == [
        "Analysis agent",
        "Planning agent",  # plan with type B everywhere
        "SHACL validator",  # LT-101 in zone 1: rejected
        "Planning agent",  # revision with type C at LT-101
        "SHACL validator",  # conforms
        "Human approval",
        "Hand-over",
    ]
    assert "type Level transmitter type B is not approved for Zone 1." in out
    assert "  ex:LT-101  Zone 1         ex:LevelTransmitterA -> ex:LevelTransmitterC" in out
    assert "Status: handed_over" in out


def test_cli_demo_offline_flag_never_asks_for_a_model(monkeypatch, capsys):
    def no_model():
        raise AssertionError("--offline must not create the OpenAI model")

    monkeypatch.setattr(cli, "make_model", no_model)
    cli.main(["demo", "--offline", "--auto-approve"])
    out = capsys.readouterr().out
    assert "(--offline)" in out
    assert "Status: handed_over" in out


def test_cli_graph_prints_and_writes_the_mermaid_diagram(monkeypatch, tmp_path, capsys):
    target = tmp_path / "graph.mmd"
    monkeypatch.setattr(cli, "GRAPH_DIAGRAM", target)
    cli.main(["graph"])
    out = capsys.readouterr().out
    assert target.read_text() == out.removesuffix("\n")
    assert "supervisor -.-> planning;" in out
    assert "validator --> supervisor;" in out


def test_cli_run_without_api_key_is_a_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["run"])
    assert exit_info.value.code == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err
