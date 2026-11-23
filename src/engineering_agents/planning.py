"""The planning agent: proposes a replacement type for every affected device.

It starts from the analysis agent's ``Analysis`` and the replacement type the
engineer asked for. Its tools look up approvals and replacement candidates and
check a draft plan against the SHACL rules. Its answer is a ``ReplacementPlan``.

If a plan was rejected because it breaks a rule, the planning agent is run
again with the rejected plan and the violations, and has to revise the
affected entries. Who rejects the plan and sends it back is up to the caller;
in this project that will be the supervisor.
"""

from collections.abc import Callable, Sequence

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from engineering_agents.agent import AgentResult, ToolCall, run_agent
from engineering_agents.analysis import Analysis
from engineering_agents.tools import (
    check_replacement_plan,
    find_replacement_candidates,
    get_device_details,
    list_type_approvals,
)
from engineering_agents.validation import Violation

INSTRUCTIONS = """\
You are the planning agent for a process plant. You receive an analysis of installed \
devices whose type must be replaced, and the replacement type the engineer asked for. \
Propose a replacement type for every affected device.

- Use the requested type wherever it is allowed.
- A device in an Ex zone (Zone 0, 1 or 2) needs a type approved for that zone. Where the \
requested type is not allowed, choose another type that is (find_replacement_candidates) \
and say why in the reason.
- Use your tools to look up approvals and candidates; never guess them. You may check a \
draft plan with check_replacement_plan.
- If you are given rule violations of an earlier plan, revise exactly the affected entries, \
keep the others unchanged, and explain each change in its reason.

When the plan is complete, call the ReplacementPlan tool."""


class PlanEntry(BaseModel):
    """The proposed replacement for one device."""

    device: str = Field(description='Device tag, e.g. "ex:LT-101".')
    current_type: str = Field(description='Type installed today, e.g. "ex:LevelTransmitterA".')
    proposed_type: str = Field(description='Proposed new type, e.g. "ex:LevelTransmitterB".')
    reason: str = Field(
        description="Why this type: for example its approval for the device's zone, or, "
        "for a revised entry, which violation made the change necessary."
    )


class ReplacementPlan(BaseModel):
    """A replacement proposal covering every affected device."""

    entries: list[PlanEntry] = Field(description="One entry per affected device.")
    summary: str = Field(description="Two or three sentences summarising the plan.")

    def replacements(self) -> dict[str, str]:
        """The plan as {device: proposed type}, the input of ``check_replacements``."""
        return {entry.device: entry.proposed_type for entry in self.entries}


def run_planning(
    model: BaseChatModel,
    analysis: Analysis,
    replacement_type: str,
    previous_plan: ReplacementPlan | None = None,
    violations: Sequence[Violation] = (),
    on_tool_call: Callable[[ToolCall], None] | None = None,
) -> AgentResult[ReplacementPlan]:
    """Let the planning agent propose (or revise) a replacement plan."""
    return run_agent(
        model,
        instructions=INSTRUCTIONS,
        task=planning_task(analysis, replacement_type, previous_plan, violations),
        tools=[
            list_type_approvals,
            find_replacement_candidates,
            get_device_details,
            check_replacement_plan,
        ],
        output_schema=ReplacementPlan,
        on_tool_call=on_tool_call,
    )


def planning_task(
    analysis: Analysis,
    replacement_type: str,
    previous_plan: ReplacementPlan | None = None,
    violations: Sequence[Violation] = (),
) -> str:
    """Write the planning agent's task: the analysis, the request and any feedback."""
    parts = [
        f"Requested replacement type: {replacement_type}",
        f"Analysis:\n{analysis.model_dump_json(indent=2)}",
    ]
    if previous_plan is not None:
        parts.append(f"Your previous plan:\n{previous_plan.model_dump_json(indent=2)}")
    if violations:
        listed = "\n".join(f"- {violation.message}" for violation in violations)
        parts.append(
            f"The previous plan was rejected because it breaks these rules:\n{listed}\n"
            "Revise the affected entries."
        )
    return "\n\n".join(parts)
