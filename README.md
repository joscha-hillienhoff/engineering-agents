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
- an analysis agent and a planning agent (OpenAI models via LangChain) that
  use the queries and the validator as tools and return structured results;
  the planning agent can revise a plan from a list of rule violations
- a LangGraph graph in which a rule-based supervisor runs the analysis agent,
  the planning agent and the SHACL validator, sends rejected plans back to the
  planner with the violations, stops with "needs human" after too many
  rejections, and hands over a conforming plan

Planned: a human approval step before the result is handed over.

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
    tools.py                   queries and validation as tools for the agents
    agent.py                   the tool-calling loop and the OpenAI model setup
    analysis.py                analysis agent: finds the affected devices
    planning.py                planning agent: proposes and revises replacements
    orchestration.py           LangGraph graph: shared state, supervisor, agents, validator
    cli.py                     command-line interface
tests/                         graph, queries, validation, agents, orchestration (offline)
docs/concepts/                 explanations of the underlying concepts
docs/img/graph.mmd             the orchestration graph as a Mermaid diagram
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

The agents call the OpenAI API. Copy `.env.example` to `.env` and set
`OPENAI_API_KEY` (or set it in the environment); `OPENAI_MODEL` overrides the
default small model. Each command prints every tool call and the structured
result:

```console
uv run engineering-agents analyse   # analysis agent
uv run engineering-agents plan      # analysis, planning, then a SHACL check of the plan
uv run engineering-agents run       # the whole graph: supervisor, agents, validator, feedback loop
```

`run` prints each step as it happens: which node ran, why the supervisor
chose it, the agents' tool calls, each plan and each violation.
`uv run engineering-agents graph` prints the graph as a Mermaid diagram (no
API key needed).

Checks (the tests use a scripted stand-in for the model and never call the API):

```console
uv run pytest
uv run ruff check
uv run ruff format --check
```

## Learning material

Start with [docs/concepts](docs/concepts/README.md). Chapter 01 explains RDF,
Turtle, ontologies and SPARQL using this project's files; chapter 02 explains
validation with SHACL; chapter 03 explains agents, tool calling and
structured output; chapter 04 explains orchestration with a supervisor in
LangGraph.

## License

[MIT](LICENSE)
