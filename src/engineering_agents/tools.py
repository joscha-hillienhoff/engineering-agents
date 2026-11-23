"""Tools the agents can call: thin wrappers around the SPARQL queries and SHACL validation.

A tool is an ordinary Python function plus a description the language model
can read. The ``@tool`` decorator builds that description from the function:

- the function name becomes the tool name,
- the docstring becomes the tool description (``parse_docstring=True`` also
  turns the ``Args:`` section into one description per argument),
- the type hints become a JSON schema for the arguments.

The model never runs these functions. It only sees the schema and answers
with "please call ``find_installation_points`` with ``{"device_type": ...}``";
the agent loop (see ``agent.py``) runs the function and sends the result back.
So the docstrings below are written for the model: what the tool does, what to
pass in, and what comes back.
"""

from functools import cache

from langchain_core.tools import tool
from rdflib import Graph

from engineering_agents.graph import load_graph
from engineering_agents.queries import run_query
from engineering_agents.validation import check_replacements


@cache
def plant_graph() -> Graph:
    """The demo plant graph, loaded once and shared by all tools."""
    return load_graph()


@tool(parse_docstring=True)
def find_installation_points(device_type: str) -> list[dict[str, str]]:
    """Find every installed device of a device type, with its location, unit and Ex zone.

    Use this to answer "where is type X installed?". Returns one entry per
    device with the keys device, location, unit, zone and medium. The zone is
    "Zone 0", "Zone 1", "Zone 2" (explosion-hazardous areas) or
    "Non-hazardous". An empty list means no device has this type.

    Args:
        device_type: Local name of the device type, for example "LevelTransmitterA".
    """
    return run_query(plant_graph(), "installation-points", device_type)


@tool(parse_docstring=True)
def get_device_details(device: str) -> dict[str, str]:
    """Get the details of one installed device: type, location, unit, Ex zone and medium.

    Args:
        device: Tag of the device, for example "LT-101" or "ex:LT-101".
    """
    wanted = "ex:" + device.removeprefix("ex:")
    for row in run_query(plant_graph(), "devices"):
        if row["device"] == wanted:
            return row
    raise ValueError(f"unknown device: {device}")


@tool(parse_docstring=True)
def list_type_approvals(device_type: str) -> list[dict[str, str]]:
    """List the Ex zones a device type is approved for, with certificate and status.

    A device may only be installed in an Ex zone (Zone 0, 1 or 2) its type is
    approved for. Returns one entry per approved zone with the keys zone,
    certificate and status ("active" or "discontinued").

    Args:
        device_type: Local name of the device type, for example "LevelTransmitterB".
    """
    return run_query(plant_graph(), "type-approvals", device_type)


@tool(parse_docstring=True)
def find_replacement_candidates(device: str) -> list[dict[str, str]]:
    """Find device types that could replace the current type of one installed device.

    A candidate measures the same variable as the current type, is still
    active, and is approved for the Ex zone of the device's location. Returns
    one entry per candidate with the keys candidate, label, manufacturer and
    zone.

    Args:
        device: Tag of the device, for example "LT-101".
    """
    return run_query(plant_graph(), "replacement-candidates", device)


@tool(parse_docstring=True)
def check_replacement_plan(replacements: dict[str, str]) -> dict:
    """Check a proposed replacement plan against the plant's engineering rules.

    The plan is applied to a copy of the plant and validated with SHACL; the
    plant itself is not changed. Returns {"conforms": true, "violations": []}
    if every rule holds. Otherwise each violation names the device, location,
    zone and type involved and explains which rule is broken.

    Args:
        replacements: Maps each device tag to its proposed new device type,
            for example {"LT-101": "LevelTransmitterC", "LT-102": "LevelTransmitterB"}.
    """
    result = check_replacements(plant_graph(), replacements)
    return {
        "conforms": result.conforms,
        "violations": [vars(violation) for violation in result.violations],
    }
