# 03 - Agents and tool calling

Chapters 01 and 02 built the facts (the knowledge graph), the questions
(SPARQL queries) and the rules (SHACL shapes). All of it is deterministic
code: `engineering-agents check-replacement --type LevelTransmitterB` always
runs the same query and the same validation.

This chapter hands the same building blocks to a language model. Two
**agents** work on the task in plain English:

> Level transmitter type A is discontinued. Find all installation points in the
> plant and prepare the replacement with type B.

The analysis agent finds the affected devices. The planning agent proposes a
replacement type for each. Neither agent is told which queries to run: each
decides for itself which tools to call, in which order, and when it knows
enough.

Files used as examples:

| File | Role |
| --- | --- |
| [`src/engineering_agents/tools.py`](../../src/engineering_agents/tools.py) | the tools: SPARQL queries and validation, wrapped for the model |
| [`src/engineering_agents/agent.py`](../../src/engineering_agents/agent.py) | the agent loop both agents share, and the OpenAI model setup |
| [`src/engineering_agents/analysis.py`](../../src/engineering_agents/analysis.py) | the analysis agent: instructions, tools, `Analysis` output |
| [`src/engineering_agents/planning.py`](../../src/engineering_agents/planning.py) | the planning agent: instructions, tools, `ReplacementPlan` output |
| [`tests/test_agents.py`](../../tests/test_agents.py) | offline tests with a scripted stand-in for the model |

## 1. What an LLM agent is

A language model on its own turns text into text. It has never seen this
plant, so if it is simply asked where type A is installed, it can only guess.
An **agent** combines three parts:

| Part | Here |
| --- | --- |
| a **model** that decides what to do next | an OpenAI chat model (`gpt-5.6-luna` by default) |
| **tools** it can ask to have run | Python functions around the SPARQL queries and the SHACL validator |
| a **loop** that runs the requested tools and reports back | `run_agent` in `agent.py`, about 30 lines |

The model never touches the graph itself. It *asks* for a tool to be run; the
code runs it and shows the model the result. The model then decides the next
step from what it has learned, which is what makes an agent different from a
fixed script: the plan of steps comes from the model, the facts come from the
tools.

## 2. Tool calling at the API level

Tool calling is part of the chat API. A request contains the conversation
*and* a list of tools, each described by a name, a description and a JSON
schema for its arguments. Instead of a text answer, the model can reply with
**tool calls**:

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "call_0",
      "type": "function",
      "function": {
        "name": "find_installation_points",
        "arguments": "{\"device_type\": \"LevelTransmitterA\"}"
      }
    }
  ]
}
```

Nothing has run yet. This is only a request, with the arguments as a JSON
string. The code runs the function and appends the result to the
conversation as a **tool message**, linked to the request by its id:

```json
{
  "role": "tool",
  "tool_call_id": "call_0",
  "content": "[{\"device\": \"ex:LT-101\", \"location\": \"T-101 Solvent storage tank\", \"unit\": \"U100 Tank farm\", \"zone\": \"Zone 1\", \"medium\": \"solvent\"}, ...]"
}
```

Then the whole conversation goes back to the model, which now knows the
result and picks the next step. One such round:

```mermaid
sequenceDiagram
    participant Loop as Agent loop (Python)
    participant LLM as Model (OpenAI API)
    participant Tool as Tool (SPARQL / SHACL)
    Loop->>LLM: messages + tool schemas
    LLM-->>Loop: tool call: find_installation_points {"device_type": "LevelTransmitterA"}
    Loop->>Tool: find_installation_points("LevelTransmitterA")
    Tool-->>Loop: 4 rows: LT-101 (Zone 1), LT-102, LT-103, LT-301
    Loop->>LLM: messages + tool message (call_0: the 4 rows)
    LLM-->>Loop: next tool call(s) ... or the final answer
```

Three things follow from this:

- **The model can only call what it is offered**, with arguments that fit the
  schema. It cannot run arbitrary code or write to the graph.
- **The model may request several calls at once** (parallel tool calls), for
  example `get_device_details` for LT-101 and LT-103. Each one gets its own
  tool message.
- **The API is stateless.** Every request carries the full conversation, so
  each round makes the request longer. Small, compact tool results keep runs
  fast and cheap.

## 3. From a Python function to a tool schema

The model only sees the tool's name, description and argument schema, never
its code. In `tools.py` that description is generated from the function with
LangChain's `@tool` decorator:

```python
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
```

This is exactly what is sent to the API (`convert_to_openai_tool(find_installation_points)`):

```json
{
  "type": "function",
  "function": {
    "name": "find_installation_points",
    "description": "Find every installed device of a device type, with its location, unit and Ex zone. Use this to answer \"where is type X installed?\". Returns one entry per\ndevice with the keys device, location, unit, zone and medium. The zone is\n\"Zone 0\", \"Zone 1\", \"Zone 2\" (explosion-hazardous areas) or\n\"Non-hazardous\". An empty list means no device has this type.",
    "parameters": {
      "type": "object",
      "properties": {
        "device_type": {
          "type": "string",
          "description": "Local name of the device type, for example \"LevelTransmitterA\"."
        }
      },
      "required": ["device_type"]
    }
  }
}
```

| In the function | In the schema |
| --- | --- |
| function name | `name` |
| docstring (without `Args:`) | `description` |
| `device_type: str` | `properties.device_type.type = "string"`, listed in `required` |
| `Args:` line for `device_type` | `properties.device_type.description` |

A docstring is therefore part of the prompt. The tool descriptions in this
project say what the tool answers, what to pass in (with an example in the
format the graph uses) and what comes back, including what an empty result
means. That is what the model needs to choose the right tool and fill in the
arguments without guessing.

The tools stay thin on purpose: each one calls a query from chapter 01 or the
validator from chapter 02 and returns plain JSON. The knowledge lives in the
graph, the rules in the shapes; the tools only make them reachable. If a tool
fails, for example because the model asked for an unknown device, the error
text goes back to the model as the tool result, so the model can correct its
arguments instead of the run crashing.

## 4. Structured output

An agent's final answer is meant for other code: the planning agent needs the
list of affected devices, and the supervisor in the next chapter needs a plan
it can validate. Free text would have to be parsed. Instead, each agent's
result is a **pydantic model**:

```python
class AffectedDevice(BaseModel):
    """One installed device affected by the task."""

    device: str = Field(description='Device tag as returned by the tools, e.g. "ex:LT-101".')
    location: str = Field(description='Installation location, e.g. "T-101 Solvent storage tank".')
    zone: str = Field(description='Ex zone of the location, e.g. "Zone 1" or "Non-hazardous".')
    ex_zone: bool = Field(description="True if the zone is an Ex zone (Zone 0, 1 or 2).")


class Analysis(BaseModel):
    """The result of the analysis: the affected devices and what to watch out for."""

    device_type: str = Field(...)
    affected_devices: list[AffectedDevice] = Field(...)
    ex_zone_note: str = Field(...)
```

The trick used here: **the result is just one more tool.** `bind_tools`
accepts a pydantic class as a tool, and turns it into a schema the same way
as a function (class name as `name`, class docstring as `description`, the
fields with their `Field(description=...)` as arguments). The model "calls"
the `Analysis` tool to hand in its answer, and the arguments of that call
*are* the answer. The loop then:

1. validates the arguments with `Analysis.model_validate(...)`,
2. on success returns the `Analysis` object and ends the loop,
3. on a validation error sends the error back as a tool message, so the model
   can fix its answer.

`tool_choice="required"` forbids replies without a tool call, so every turn
is either a lookup or the final answer, never loose text.

(LangChain also offers `model.with_structured_output(Analysis)`, which uses
the API's structured-output mode. Used after the lookups, it needs an extra
request. Treating the answer as a tool keeps lookups and the answer in one
conversation and one loop.)

## 5. The loop

The core of `run_agent` in `agent.py`:

```python
model_with_tools = model.bind_tools([*tools, output_schema], tool_choice="required")
messages = [SystemMessage(instructions), HumanMessage(task)]

for _ in range(max_turns):
    reply = model_with_tools.invoke(messages)  # one API request
    messages.append(reply)

    for call in reply.tool_calls:
        if call["name"] == answer_tool:  # the final answer
            try:
                output = output_schema.model_validate(call["args"])
            except ValidationError as error:
                result = f"Error: the {answer_tool} does not match its schema:\n{error}"
            else:
                return AgentResult(output=output, tool_calls=tool_calls)
        else:  # a lookup: run the tool
            result = _execute(tools_by_name, call["name"], call["args"])
            ...
        messages.append(ToolMessage(result, tool_call_id=call["id"]))

raise AgentError(...)
```

| Message type | Role in the API | Contains |
| --- | --- | --- |
| `SystemMessage` | `system` | the agent's instructions: its role and its rules |
| `HumanMessage` | `user` | the task |
| `AIMessage` | `assistant` | the model's reply; here always tool calls |
| `ToolMessage` | `tool` | one tool result, linked by `tool_call_id` |

`max_turns` is a safety net: a model that never finishes cannot run up an
unlimited bill.

LangChain and LangGraph ship ready-made versions of this loop. It is
written out here because it is short, and
because seeing it once makes every agent framework easier to understand: they
all run this loop.

## 6. Two specialised agents

Both agents run the same loop. What makes them different is only their
instructions, their tools and their output:

| | Analysis agent | Planning agent |
| --- | --- | --- |
| Job | find the facts: which devices, where, in which zone | decide: which type replaces which device, and why |
| Input | the task text | the `Analysis`, the requested type, optionally a rejected plan and its violations |
| Tools | `find_installation_points`, `get_device_details` | `list_type_approvals`, `find_replacement_candidates`, `get_device_details`, `check_replacement_plan` |
| Output | `Analysis` | `ReplacementPlan` (one `PlanEntry` per device: device, current type, proposed type, reason) |
| Must not | recommend replacements | invent approvals or candidates |

Why split the work instead of one agent with all six tools?

- **Focus.** Fewer tools and a narrower instruction give a small, cheap model
  fewer ways to go wrong.
- **A clear handover.** The `Analysis` object is a contract between the two.
  It can be printed, checked and tested on its own, and a person can see where
  a wrong result started.
- **Separate responsibilities.** The analysis agent only reads facts; only the
  planning agent proposes changes. That mirrors how the work is split between
  people, and it decides who gets which tools.
- **Exchangeable parts.** Each agent is a plain function
  (`run_analysis(model, task)`, `run_planning(model, analysis, ...)`), so the
  next chapter can turn each into a node of a LangGraph graph without changing
  the agents.

### How the agents share the ontology

The agents never exchange vague descriptions such as "the level sensor on the
solvent tank". Every tool returns identifiers from the knowledge graph
(`ex:LT-101`, `ex:LevelTransmitterC`), and the output models ask for exactly
those identifiers. So the analysis agent's `ex:LT-101` is the same resource
the planning agent looks up, the same resource `check_replacement_plan`
changes in its copy of the graph, and the same resource the SHACL report
names in a violation. The ontology is the shared vocabulary that lets
specialised agents (and the deterministic validator) work on one picture of
the plant.

## 7. Feedback: revising a rejected plan

The planning agent may check its draft with `check_replacement_plan`, but
the final check belongs to someone who did not write the plan. From the next
chapter on, the supervisor validates each plan, and if it breaks a rule, the
planning agent gets the plan back. That entry point already exists:

```python
run_planning(
    model,
    analysis,
    "LevelTransmitterB",
    previous_plan=rejected_plan,
    violations=result.violations,  # from validation.check_replacements
)
```

The violations become part of the task text:

```
The previous plan was rejected because it breaks these rules:
- LT-101 at T-101 Solvent storage tank (Zone 1): type Level transmitter type B is not approved for Zone 1.
Revise the affected entries.
```

The instructions tell the agent to change only the affected entries and to
explain each change in its `reason`. For LT-101, the tool
`find_replacement_candidates` returns type C, the one level transmitter that
is active and approved for zone 1. This is the sentence chapter 02 wrote the
SHACL message for: a rule violation a person and a model can both read.

## 8. Testing without the API

Model output varies from run to run, and every call costs money, so the tests
never call the API. `tests/test_agents.py` replaces the model with a
`ScriptedModel`: it returns predefined tool calls in order and records every
conversation it receives. Everything else is real: the tools run their SPARQL
queries against the demo plant, and the loop validates the answers. The tests
check

- that the schema the model would see comes from the signature and docstring,
- that tool results go back as tool messages with the right ids, also for
  parallel calls,
- that tool errors and an invalid answer are reported back to the model
  instead of crashing,
- that the planning agent receives the analysis, and, on a revision, the
  rejected plan and its violations.

What these tests cannot check is the model's judgement. That is what a live
run is for.

## 9. Try it yourself

The agents need an OpenAI API key. Copy `.env.example` to `.env` and fill in
`OPENAI_API_KEY` (`.env` is ignored by git), or set it in the environment.
`OPENAI_MODEL` selects another model; the default is a small, cheap model with
tool calling.

`make_model()` creates the model with `ChatOpenAI(..., use_responses_api=True)`,
so requests go to OpenAI's Responses API (`/v1/responses`) instead of the older
Chat Completions API. Current reasoning models such as the default one reject
function tools on Chat Completions, while the Responses API supports them.
LangChain translates the messages, so the loop above and its tool calls and
tool messages stay the same; only the JSON on the wire differs a little from
the Chat Completions format shown in section 2.

```console
uv run engineering-agents analyse   # the analysis agent alone
uv run engineering-agents plan      # analysis, then planning, then a SHACL check of the plan
```

Both commands print every tool call as it happens (name, arguments, a
shortened result) and then the structured output as JSON. A run makes a
handful of requests to a small model; at the default model's list price that
is in the range of a cent. Because the
model decides the steps, two runs may call different tools in a different
order and still reach the same result.

Exercises:

1. Run `analyse` twice. Which tools did the model call, and in which order? Was
   `get_device_details` needed at all?
2. Delete the `Args:` section from the docstring of `find_installation_points`
   and print `convert_to_openai_tool(find_installation_points)`. What
   information has the model lost?
3. Run `plan --type LevelTransmitterC`. Why does the plan now pass the
   validation without any revision?
4. Give the analysis agent a different task, for example
   `analyse --task "Which devices are installed in zone 1?"`. Can it answer
   with its two tools, and does `Analysis` still fit the question?
