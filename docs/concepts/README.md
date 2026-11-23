# Concepts

Short explanations of the ideas behind this project, each using the project's
own files as examples. Read them in order; every chapter builds on the one
before.

| Chapter | Topics |
| --- | --- |
| [01 - Ontology, knowledge graph and SPARQL](01-ontology-and-sparql.md) | RDF triples, Turtle, TBox vs. ABox, OWL classes and properties, SPARQL patterns, OPTIONAL, FILTER, property paths |
| [02 - Validation with SHACL](02-shacl.md) | open vs. closed world, validation vs. inference, node and property shapes, targets, SHACL Core constraints, SHACL-SPARQL, the validation report, validating a proposed change |
| [03 - Agents and tool calling](03-agents-and-tool-calling.md) | model + tools + loop, tool calls and tool messages at the API level, tool schemas from docstrings and type hints, structured output with pydantic, specialised agents sharing an ontology, feedback from violations, testing without the API |
| [04 - Orchestration with LangGraph](04-orchestration-with-langgraph.md) | what orchestration is, supervisor vs. pipeline, swarm and blackboard, LangGraph state, nodes, edges, conditional edges, reducers, context, compile and stream, rule-based vs. LLM supervisor, why the validator is code, how the feedback loop ends |

Planned chapter: human approval via interrupts.
