"""The agents' tool plumbing and structured output, tested offline.

A scripted model stands in for the LLM: it returns predefined tool calls in
order and records what it was sent. The tools themselves are real and run
against the demo plant, so these tests check everything except the model's
own choices. No test calls the OpenAI API.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from engineering_agents import cli
from engineering_agents.agent import AgentError, make_model, run_agent
from engineering_agents.analysis import AffectedDevice, Analysis, run_analysis
from engineering_agents.planning import PlanEntry, ReplacementPlan, run_planning
from engineering_agents.tools import check_replacement_plan, find_installation_points
from engineering_agents.validation import Violation


class ScriptedModel:
    """Replays one AIMessage per model call and records every conversation it receives."""

    model_name = "scripted"

    def __init__(self, *replies: AIMessage):
        self.replies = list(replies)
        self.conversations: list[list] = []
        self.bound_tools: list = []

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        return self

    def invoke(self, messages):
        self.conversations.append(list(messages))
        return self.replies.pop(0)


def calls(*tool_calls: tuple[str, dict]) -> AIMessage:
    """An assistant message that requests the given tool calls."""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{i}"}
            for i, (name, args) in enumerate(tool_calls)
        ],
    )


ANALYSIS = Analysis(
    device_type="ex:LevelTransmitterA",
    affected_devices=[
        AffectedDevice(
            device="ex:LT-101", location="T-101 Solvent storage tank", zone="Zone 1", ex_zone=True
        ),
        AffectedDevice(
            device="ex:LT-102",
            location="T-102 Process water tank",
            zone="Non-hazardous",
            ex_zone=False,
        ),
        AffectedDevice(
            device="ex:LT-103", location="T-103 Product tank", zone="Zone 2", ex_zone=True
        ),
        AffectedDevice(
            device="ex:LT-301",
            location="B-301 Cooling water basin",
            zone="Non-hazardous",
            ex_zone=False,
        ),
    ],
    ex_zone_note="LT-101 is in Zone 1 and LT-103 in Zone 2; the new type needs approvals there.",
)


def plan_with(type_for_lt_101: str) -> ReplacementPlan:
    entries = [
        PlanEntry(
            device=device.device,
            current_type="ex:LevelTransmitterA",
            proposed_type=type_for_lt_101
            if device.device == "ex:LT-101"
            else "ex:LevelTransmitterB",
            reason="requested type",
        )
        for device in ANALYSIS.affected_devices
    ]
    return ReplacementPlan(entries=entries, summary="Replace type A.")


def test_tool_schema_comes_from_signature_and_docstring():
    schema = convert_to_openai_tool(find_installation_points)["function"]
    assert schema["name"] == "find_installation_points"
    assert schema["description"].startswith("Find every installed device of a device type")
    assert schema["parameters"]["required"] == ["device_type"]
    argument = schema["parameters"]["properties"]["device_type"]
    assert argument["type"] == "string"
    assert "LevelTransmitterA" in argument["description"]


def test_check_replacement_plan_tool_reports_the_zone_1_violation():
    result = check_replacement_plan.invoke({"replacements": {"LT-101": "LevelTransmitterB"}})
    assert result["conforms"] is False
    assert [v["device"] for v in result["violations"]] == ["ex:LT-101"]


def test_analysis_runs_tools_and_returns_structured_output():
    model = ScriptedModel(
        calls(("find_installation_points", {"device_type": "LevelTransmitterA"})),
        calls(("Analysis", ANALYSIS.model_dump())),
    )
    seen = []
    result = run_analysis(model, on_tool_call=seen.append)

    assert result.output == ANALYSIS
    assert [call.name for call in result.tool_calls] == ["find_installation_points"]
    assert seen == result.tool_calls
    # The real query ran: its rows went back to the model as a tool message.
    rows = json.loads(result.tool_calls[0].result)
    assert [row["device"] for row in rows] == ["ex:LT-101", "ex:LT-102", "ex:LT-103", "ex:LT-301"]

    first, second = model.conversations
    assert isinstance(first[0], SystemMessage) and isinstance(first[1], HumanMessage)
    assert "Level transmitter type A is discontinued" in first[1].content
    tool_message = second[-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call_0"
    assert tool_message.content == result.tool_calls[0].result
    # The answer schema is offered to the model as one more tool.
    assert Analysis in model.bound_tools


def test_parallel_tool_calls_each_get_a_tool_message():
    model = ScriptedModel(
        calls(
            ("get_device_details", {"device": "LT-101"}),
            ("get_device_details", {"device": "LT-103"}),
        ),
        calls(("Analysis", ANALYSIS.model_dump())),
    )
    run_analysis(model)
    tool_messages = model.conversations[1][-2:]
    assert [m.tool_call_id for m in tool_messages] == ["call_0", "call_1"]
    assert '"zone": "Zone 1"' in tool_messages[0].content
    assert '"zone": "Zone 2"' in tool_messages[1].content


def test_tool_errors_and_invalid_answers_go_back_to_the_model():
    model = ScriptedModel(
        calls(("get_device_details", {"device": "XX-999"}), ("no_such_tool", {})),
        calls(("Analysis", {"device_type": "ex:LevelTransmitterA"})),  # fields missing
        calls(("Analysis", ANALYSIS.model_dump())),
    )
    result = run_analysis(model)
    assert result.output == ANALYSIS

    unknown_device, unknown_tool = model.conversations[1][-2:]
    assert unknown_device.content == "Error: unknown device: XX-999"
    assert unknown_tool.content == "Error: there is no tool named 'no_such_tool'"
    invalid_answer = model.conversations[2][-1]
    assert invalid_answer.content.startswith("Error: the Analysis does not match its schema")
    assert "affected_devices" in invalid_answer.content


def test_agent_gives_up_after_max_turns():
    model = ScriptedModel(*[calls(("get_device_details", {"device": "LT-101"}))] * 2)
    with pytest.raises(AgentError, match="no Analysis after 2 model calls"):
        run_agent(model, "instructions", "task", [], Analysis, max_turns=2)


def test_planning_receives_the_analysis_and_can_validate_its_draft():
    draft = plan_with("ex:LevelTransmitterB")
    model = ScriptedModel(
        calls(("check_replacement_plan", {"replacements": draft.replacements()})),
        calls(("ReplacementPlan", plan_with("ex:LevelTransmitterC").model_dump())),
    )
    result = run_planning(model, ANALYSIS, "LevelTransmitterB")

    task = model.conversations[0][1].content
    assert task.startswith("Requested replacement type: LevelTransmitterB")
    assert '"device": "ex:LT-101"' in task
    assert "rejected" not in task
    assert json.loads(result.tool_calls[0].result)["conforms"] is False
    assert result.output.replacements()["ex:LT-101"] == "ex:LevelTransmitterC"


def test_planning_revises_a_plan_after_violations():
    rejected = plan_with("ex:LevelTransmitterB")
    violation = Violation(
        device="ex:LT-101",
        location="ex:T-101",
        zone="plant:Zone1",
        type="ex:LevelTransmitterB",
        message="LT-101 at T-101 Solvent storage tank (Zone 1): "
        "type Level transmitter type B is not approved for Zone 1.",
    )
    revised = plan_with("ex:LevelTransmitterC")
    model = ScriptedModel(
        calls(("find_replacement_candidates", {"device": "LT-101"})),
        calls(("ReplacementPlan", revised.model_dump())),
    )
    result = run_planning(
        model, ANALYSIS, "LevelTransmitterB", previous_plan=rejected, violations=[violation]
    )

    task = model.conversations[0][1].content
    assert "Your previous plan:" in task
    assert "rejected because it breaks these rules:\n- " + violation.message in task
    assert "LevelTransmitterC" in result.tool_calls[0].result
    assert result.output == revised


def test_make_model_needs_an_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        make_model()


def test_make_model_uses_the_responses_api(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # never used: nothing is sent
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    model = make_model()
    assert model.use_responses_api is True
    assert model.model_name == "gpt-5.6-luna"


def test_cli_without_api_key_is_a_usage_error(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["analyse"])
    assert exit_info.value.code == 2
    assert "OPENAI_API_KEY is not set" in capsys.readouterr().err


def test_cli_plan_prints_tool_calls_outputs_and_validation(monkeypatch, capsys):
    model = ScriptedModel(
        calls(("find_installation_points", {"device_type": "LevelTransmitterA"})),
        calls(("Analysis", ANALYSIS.model_dump())),
        calls(("list_type_approvals", {"device_type": "LevelTransmitterB"})),
        calls(("ReplacementPlan", plan_with("ex:LevelTransmitterB").model_dump())),
    )
    monkeypatch.setattr(cli, "make_model", lambda: model)
    cli.main(["plan"])
    out = capsys.readouterr().out

    assert 'tool call: find_installation_points({"device_type": "LevelTransmitterA"})' in out
    assert '  result:  [{"device": "ex:LT-101"' in out
    assert "structured output (Analysis):" in out
    assert "structured output (ReplacementPlan):" in out
    assert "Conforms: no (1 violation)" in out
