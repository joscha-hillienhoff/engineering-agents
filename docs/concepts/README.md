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
| [05 - Human in the loop](05-human-in-the-loop.md) | why approval sits before the hand-over, `interrupt` and `Command(resume=...)`, checkpointers, threads, inspecting a paused run, approve vs. reject paths, the offline model for the demo |

The [walkthrough notebook](../../notebooks/walkthrough.ipynb) shows the main
steps of all five chapters cell by cell.

## Self-check

Questions only; the answers are in the chapters. If one is hard to answer
aloud in two or three sentences, reread the section it points to.

### 01 - Ontology, knowledge graph and SPARQL

1. What are the three parts of an RDF triple? Write the triple that says
   LT-101 is installed at T-101.
2. What is the difference between the TBox and the ABox, and which file of
   this project holds which?
3. Why does the graph name things with IRIs like `ex:LT-101` instead of plain
   strings?
4. What does `OPTIONAL` do in a SPARQL query, and what happens to a row
   without it when the optional part is missing?
5. How does the query `installation-points` get from a device to its Ex zone?

### 02 - Validation with SHACL

1. Why can OWL alone not report "type B is not approved for Zone 1"? (Hint:
   open vs. closed world.)
2. What is the difference between a shape's target and its constraints?
3. Why does the Ex-zone rule need SHACL-SPARQL instead of SHACL Core?
4. How is a proposed replacement validated without changing the plant data?
5. Which parts of a validation report end up in the message the planning
   agent reads?

### 03 - Agents and tool calling

1. Name the three parts of an LLM agent and say where each one is in this
   project.
2. Who executes a tool call: the model or the program? What does the model
   actually send?
3. Where does the JSON schema of a tool come from?
4. How does the agent loop get a `ReplacementPlan` object instead of free
   text, and what happens if the model's answer does not fit the schema?
5. Why do the two agents exchange ontology identifiers instead of prose?

### 04 - Orchestration with LangGraph

1. What four questions does orchestration answer?
2. What is the difference between a node, an edge and a conditional edge?
3. What does the reducer on `trace` change compared to a normal state key?
4. Why is the supervisor a set of rules and not an LLM here? When would an
   LLM supervisor be the better choice?
5. Why is the validator code and not an agent?
6. Name every way a run of the graph can end.

### 05 - Human in the loop

1. Why does the approval sit after the validation and before the hand-over,
   and not anywhere else?
2. What happens, step by step, when a node calls `interrupt(...)`, and what
   happens when the run is resumed?
3. What does the checkpointer store, and what is a thread id for?
4. Why must code before an `interrupt` call be safe to run twice?
5. Follow a rejection: where does the reviewer's comment go, and which
   checks does the revised plan pass before the reviewer sees it again?
6. What does `--auto-approve` do, and why does the run stop instead of
   approving when there is no terminal?
