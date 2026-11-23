"""The supervisor graph, run end to end with the scripted model. No test calls the OpenAI API."""

import pytest
from test_agents import ANALYSIS, ScriptedModel, calls, plan_with

from engineering_agents import cli
from engineering_agents.analysis import INSTRUCTIONS as ANALYSIS_INSTRUCTIONS
from engineering_agents.orchestration import Context, build_graph, initial_state, validator
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


def run(model, max_planning_rounds=3):
    context = Context(model=model, max_planning_rounds=max_planning_rounds)
    return build_graph().invoke(initial_state(), context=context)


def test_rejected_plan_goes_back_to_planning_and_is_handed_over_after_revision():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_C))
    state = run(model)

    assert state["status"] == "handed_over"
    assert state["plan"] == PLAN_C
    assert state["violations"] == []
    assert state["planning_rounds"] == 2
    assert [entry["node"] for entry in state["trace"]] == [
        "supervisor", "analysis",
        "supervisor", "planning",
        "supervisor", "validator",  # LT-101 with type B in zone 1: rejected
        "supervisor", "planning",  # revision with the violation as feedback
        "supervisor", "validator",  # conforms
        "supervisor", "hand_over",
    ]  # fmt: skip
    assert "does not conform: 1 violation(s)" in state["trace"][5]["note"]

    # The second planning run was given the rejected plan and the violation.
    revision_task = model.conversations[4][1].content
    assert "Your previous plan:" in revision_task
    assert "LT-101 at T-101 Solvent storage tank (Zone 1)" in revision_task
    assert "not approved for Zone 1" in revision_task


def test_supervisor_stops_with_needs_human_after_max_planning_rounds():
    model = ScriptedModel(*analysis_replies(), *planning_replies(PLAN_B), *planning_replies(PLAN_B))
    state = run(model, max_planning_rounds=2)

    assert state["status"] == "needs_human"
    assert state["planning_rounds"] == 2
    assert [v.device for v in state["violations"]] == ["ex:LT-101"]
    assert [entry["node"] for entry in state["trace"]] == [
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
    run(model)
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
    cli.main(["run"])
    out = capsys.readouterr().out

    assert "[supervisor] -> analysis: no analysis yet" in out
    assert 'tool call: find_installation_points({"device_type": "LevelTransmitterA"})' in out
    assert "[analysis] found 4 device(s): ex:LT-101 (Zone 1)" in out
    assert "[planning] proposed a plan (round 1)" in out
    assert "[validator] does not conform: 1 violation(s)" in out
    assert "    - LT-101 at T-101 Solvent storage tank (Zone 1)" in out
    assert "[planning] revised the plan (round 2)" in out
    assert "[validator] conforms" in out
    assert "[hand_over] plan with 4 entries handed over" in out
    assert out.rstrip().endswith("Plan: Replace type A.")
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
