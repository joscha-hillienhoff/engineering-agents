# engineering-agents

A deliberately small learning project: specialised software agents that solve a
plant-engineering task together over a shared ontology.

The example task:

> Level transmitter type A is discontinued. Find all installation points in the
> plant and prepare the replacement with type B.

The catch: one installation point lies in Ex zone 1, and type B is not approved
for zone 1. A correct solution has to notice that and pick a different type
there.

All plant data, names, manufacturers and certificate numbers are invented.

## Status

Built so far:

- an OWL ontology for devices, device types, locations, Ex zones and approvals
- a knowledge graph of an invented plant with 18 devices
- named SPARQL queries and a small command-line interface to run them
- SHACL shapes, including the rule that a device's type must be approved for
  the Ex zone of its installation location, and a validator that checks the
  plant or a proposed replacement plan against them

Planned: an analysis agent and a planning agent with tool calling, a
supervisor that orchestrates them (sending rejected proposals back to the
planner), and a human approval step before the result is handed over.

## Layout

```
ontology/plant.ttl             vocabulary (classes and properties), OWL in Turtle
data/plant.ttl                 the demo plant: devices, types, locations, approvals
shapes/plant-shapes.ttl        SHACL shapes: the rules the plant data must satisfy
src/engineering_agents/
    graph.py                   loads ontology and data into one rdflib graph
    queries.py                 named queries: load, bind parameter, run
    sparql/*.rq                the SPARQL queries, one per file
    validation.py              SHACL validation of the plant or a proposed change
    cli.py                     command-line interface
tests/                         graph consistency, query results, validation results
docs/concepts/                 explanations of the underlying concepts
```

## Running

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```console
uv sync
uv run engineering-agents query --help
uv run engineering-agents query devices
uv run engineering-agents query installation-points LevelTransmitterA
uv run engineering-agents query type-approvals LevelTransmitterB
uv run engineering-agents query replacement-candidates LT-101

uv run engineering-agents validate
uv run engineering-agents check-replacement --type LevelTransmitterB   # violation in zone 1
uv run engineering-agents check-replacement --type LevelTransmitterC   # conforms
```

Checks:

```console
uv run pytest
uv run ruff check
uv run ruff format --check
```

## Learning material

Start with [docs/concepts](docs/concepts/README.md). Chapter 01 explains RDF,
Turtle, ontologies and SPARQL using this project's files; chapter 02 explains
validation with SHACL.

## License

[MIT](LICENSE)
