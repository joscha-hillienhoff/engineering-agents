"""Named SPARQL queries over the plant knowledge graph.

Each query lives in its own ``sparql/<name>.rq`` file so it can be read (and
run in any SPARQL tool) on its own. This module loads a query by name, binds
its single optional parameter and returns the result rows as plain strings,
which makes them easy to print or to hand to other code.
"""

from dataclasses import dataclass
from importlib.resources import files

from rdflib import Graph, URIRef
from rdflib.term import Node

from engineering_agents.graph import EX


@dataclass(frozen=True)
class Query:
    name: str
    description: str
    # Name of the SPARQL variable that is bound from the caller's argument,
    # or None if the query takes no argument.
    parameter: str | None = None


QUERIES = {
    query.name: query
    for query in [
        Query("devices", "All devices with type, location, unit and Ex zone"),
        Query(
            "installation-points",
            "All installation points of a device type",
            parameter="deviceType",
        ),
        Query(
            "type-approvals",
            "Ex zones a device type is approved for",
            parameter="deviceType",
        ),
        Query(
            "replacement-candidates",
            "Active types with the same function that are approved for a device's zone",
            parameter="device",
        ),
    ]
}


def run_query(graph: Graph, name: str, argument: str | None = None) -> list[dict[str, str]]:
    """Run the named query and return one dict per result row.

    ``argument`` is the local name of a resource in the demo plant, for
    example ``"LevelTransmitterA"`` or ``"LT-101"`` (an ``ex:`` prefix is
    accepted too).
    """
    query = QUERIES[name]
    text = (files("engineering_agents") / "sparql" / f"{name}.rq").read_text()

    # initBindings fixes a variable to a value before the query is evaluated.
    # This is safer and clearer than pasting the value into the query string.
    bindings = {}
    if query.parameter is not None:
        if argument is None:
            raise ValueError(f"query {name!r} needs an argument ({query.parameter})")
        bindings[query.parameter] = EX[argument.removeprefix("ex:")]

    result = graph.query(text, initBindings=bindings)
    return [{str(var): _to_text(graph, row[var]) for var in result.vars} for row in result]


def _to_text(graph: Graph, term: Node | None) -> str:
    """Render an IRI with its prefix (ex:LT-101), a literal as its value."""
    if term is None:  # variable left unbound, e.g. by OPTIONAL
        return ""
    if isinstance(term, URIRef):
        return term.n3(graph.namespace_manager)
    return str(term)
