"""A scripted stand-in for the language model, so the demo runs without an API key.

``OfflineModel`` plays the model's part in the demo scenario. It never calls
an API; it answers with fixed tool calls, chosen from the conversation it is
sent:

- which agent is asking (the system message),
- for the planning agent, whether this is a revision ("Your previous plan:"
  in the task),
- whether a tool result has come back yet (the last message is a tool message).

Like a real model in this demo, its first plan uses the requested type B
everywhere, which breaks the zone 1 rule at LT-101; its revision uses type C
there. Everything around it is real: the agent loop runs the tools, the tools
run their SPARQL queries, the validator runs SHACL and the graph is the same
LangGraph graph. Only the model's judgement is scripted, so it ignores the
wording of a reviewer's comment.
"""

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from engineering_agents.analysis import INSTRUCTIONS as ANALYSIS_INSTRUCTIONS
from engineering_agents.analysis import AffectedDevice, Analysis
from engineering_agents.planning import INSTRUCTIONS as PLANNING_INSTRUCTIONS
from engineering_agents.planning import PlanEntry, ReplacementPlan

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
    ex_zone_note="LT-101 is in Zone 1 and LT-103 in Zone 2; a replacement type must be "
    "approved for these zones.",
)


def _plan(type_for_lt_101: str, reason_for_lt_101: str, summary: str) -> ReplacementPlan:
    reasons = {
        "ex:LT-102": "Requested type; non-hazardous area.",
        "ex:LT-103": "Requested type; type B is approved for Zone 2.",
        "ex:LT-301": "Requested type; non-hazardous area.",
    }
    return ReplacementPlan(
        entries=[
            PlanEntry(
                device=device.device,
                current_type="ex:LevelTransmitterA",
                proposed_type=type_for_lt_101
                if device.device == "ex:LT-101"
                else "ex:LevelTransmitterB",
                reason=reason_for_lt_101
                if device.device == "ex:LT-101"
                else reasons[device.device],
            )
            for device in ANALYSIS.affected_devices
        ],
        summary=summary,
    )


# The first plan overlooks the zone 1 approval at LT-101.
PLAN_B = _plan(
    "ex:LevelTransmitterB",
    "Requested type.",
    "Replace all four type A level transmitters with type B.",
)
# The revision after the validator's feedback.
PLAN_C = _plan(
    "ex:LevelTransmitterC",
    "Type B is not approved for Zone 1; type C is active and approved for Zone 1.",
    "Replace LT-102, LT-103 and LT-301 with type B, and LT-101 in Zone 1 with type C, "
    "the only active level transmitter approved for Zone 1.",
)


class OfflineModel:
    """Plays the LLM's part in the demo scenario with fixed answers. No API call."""

    model_name = "offline (scripted)"

    def bind_tools(self, tools, **kwargs) -> "OfflineModel":
        return self  # it already knows which tools it will call

    def invoke(self, messages: list[BaseMessage]) -> AIMessage:
        system, task = messages[0].content, messages[1].content
        tool_result_is_back = isinstance(messages[-1], ToolMessage)
        if system == ANALYSIS_INSTRUCTIONS:
            if not tool_result_is_back:
                return _call("find_installation_points", device_type="LevelTransmitterA")
            return _call("Analysis", **ANALYSIS.model_dump())
        if system == PLANNING_INSTRUCTIONS:
            revision = "Your previous plan:" in task
            if not tool_result_is_back:
                if revision:
                    return _call("find_replacement_candidates", device="LT-101")
                return _call("list_type_approvals", device_type="LevelTransmitterB")
            return _call("ReplacementPlan", **(PLAN_C if revision else PLAN_B).model_dump())
        raise ValueError("the offline model only plays the analysis and the planning agent")


def _call(name: str, **args) -> AIMessage:
    """An assistant message with one tool call, as a real model would send it."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{name}"}])
