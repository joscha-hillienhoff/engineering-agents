"""The analysis agent: finds out which devices a task is about.

It only reads the knowledge graph. Its tools answer "where is this type
installed?" and "what do we know about this device?", and its answer is an
``Analysis`` object, not free text, so the planning agent (and later the
supervisor) can use it directly.
"""

from collections.abc import Callable

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, Field

from engineering_agents.agent import AgentResult, ToolCall, run_agent
from engineering_agents.tools import find_installation_points, get_device_details

TASK = (
    "Level transmitter type A is discontinued. Find all installation points in the plant "
    "and prepare the replacement with type B."
)

INSTRUCTIONS = """\
You are the analysis agent for a process plant. You find the facts a task is about by \
querying the plant's knowledge graph with your tools.

- Every device, location and zone in your answer must come from a tool result. Never guess.
- Device types are named like "LevelTransmitterA"; device tags like "LT-101".
- Do not plan or recommend replacements; a separate planning agent does that.
- Point out every affected device in an Ex zone (Zone 0, 1 or 2): there, any replacement \
type must be approved for that zone.

When you have the facts, call the Analysis tool with your result."""


class AffectedDevice(BaseModel):
    """One installed device affected by the task."""

    device: str = Field(description='Device tag as returned by the tools, e.g. "ex:LT-101".')
    location: str = Field(description='Installation location, e.g. "T-101 Solvent storage tank".')
    zone: str = Field(description='Ex zone of the location, e.g. "Zone 1" or "Non-hazardous".')
    ex_zone: bool = Field(description="True if the zone is an Ex zone (Zone 0, 1 or 2).")


class Analysis(BaseModel):
    """The result of the analysis: the affected devices and what to watch out for."""

    device_type: str = Field(
        description='The device type the task is about, e.g. "ex:LevelTransmitterA".'
    )
    affected_devices: list[AffectedDevice] = Field(
        description="Every installed device of that type."
    )
    ex_zone_note: str = Field(
        description="One or two sentences on the affected devices in Ex zones and what a "
        "replacement must satisfy there; say so if none is in an Ex zone."
    )


def run_analysis(
    model: BaseChatModel,
    task: str = TASK,
    on_tool_call: Callable[[ToolCall], None] | None = None,
) -> AgentResult[Analysis]:
    """Let the analysis agent work on ``task`` and return its ``Analysis``."""
    return run_agent(
        model,
        instructions=INSTRUCTIONS,
        task=task,
        tools=[find_installation_points, get_device_details],
        output_schema=Analysis,
        on_tool_call=on_tool_call,
    )
