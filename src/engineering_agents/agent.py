"""The agent loop shared by the analysis and the planning agent.

An LLM agent is three things: a language model, a set of tools, and a loop.

1. Send the conversation so far to the model, together with the tool schemas.
2. The model answers with one or more *tool calls*: a tool name and JSON
   arguments. It does not run anything itself.
3. Run each requested tool and append its result to the conversation as a
   *tool message*.
4. Repeat until the model calls the answer tool.

The answer tool is how the agent returns *structured output*: its schema is a
pydantic model (for example ``Analysis``), so its arguments are the result,
already in the right shape. The loop validates them against the pydantic model;
if they do not fit, the error goes back to the model like any tool result and
the model can try again.
"""

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dotenv import find_dotenv, load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ValidationError

# A small, current OpenAI model that supports tool calling. Override it with
# the OPENAI_MODEL environment variable (or in .env).
DEFAULT_MODEL = "gpt-5.6-luna"


def make_model() -> BaseChatModel:
    """Create the OpenAI chat model, reading OPENAI_API_KEY and OPENAI_MODEL.

    Both can come from the environment or from a ``.env`` file in the current
    directory or one of its parents (see ``.env.example``). Values already in
    the environment win.
    """
    # Imported here so that code and tests that never talk to OpenAI do not need it.
    from langchain_openai import ChatOpenAI

    load_dotenv(find_dotenv(usecwd=True))
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is not set; put it in the environment or in .env")
    return ChatOpenAI(model=os.environ.get("OPENAI_MODEL", DEFAULT_MODEL))


@dataclass(frozen=True)
class ToolCall:
    """One tool call made by the model during a run, and what it returned."""

    name: str
    args: dict[str, Any]
    result: str


@dataclass(frozen=True)
class AgentResult[Output: BaseModel]:
    output: Output
    tool_calls: list[ToolCall]


class AgentError(RuntimeError):
    pass


def run_agent[Output: BaseModel](
    model: BaseChatModel,
    instructions: str,
    task: str,
    tools: list[BaseTool],
    output_schema: type[Output],
    on_tool_call: Callable[[ToolCall], None] | None = None,
    max_turns: int = 12,
) -> AgentResult[Output]:
    """Run the tool-calling loop until the model returns an ``output_schema`` object.

    ``instructions`` is the system message (the agent's role), ``task`` the
    user message. ``on_tool_call`` is called after every executed tool call,
    so a caller can show the loop as it happens. ``max_turns`` limits the
    number of model calls.
    """
    tools_by_name = {t.name: t for t in tools}
    answer_tool = output_schema.__name__

    # bind_tools sends the JSON schema of every tool along with each request.
    # The pydantic model counts as one more tool: the one that ends the loop.
    # tool_choice="required" makes the model call a tool on every turn instead
    # of replying with free text, so every run ends with a structured answer.
    model_with_tools = model.bind_tools([*tools, output_schema], tool_choice="required")

    messages: list[BaseMessage] = [SystemMessage(instructions), HumanMessage(task)]
    tool_calls: list[ToolCall] = []

    for _ in range(max_turns):
        reply = model_with_tools.invoke(messages)
        messages.append(reply)  # the model's tool calls become part of the conversation

        for call in reply.tool_calls:
            if call["name"] == answer_tool:
                try:
                    output = output_schema.model_validate(call["args"])
                except ValidationError as error:
                    result = f"Error: the {answer_tool} does not match its schema:\n{error}"
                else:
                    return AgentResult(output=output, tool_calls=tool_calls)
            else:
                result = _execute(tools_by_name, call["name"], call["args"])
                record = ToolCall(name=call["name"], args=call["args"], result=result)
                tool_calls.append(record)
                if on_tool_call:
                    on_tool_call(record)
            # Every tool call needs an answer that refers to it by its id.
            messages.append(ToolMessage(result, tool_call_id=call["id"]))

    raise AgentError(f"no {answer_tool} after {max_turns} model calls")


def _execute(tools_by_name: dict[str, BaseTool], name: str, args: dict[str, Any]) -> str:
    """Run one tool and return its result as text for the model.

    Errors are returned as text too: the model chose the arguments, so it is
    the one that has to see the error and correct them.
    """
    if name not in tools_by_name:
        return f"Error: there is no tool named {name!r}"
    try:
        result = tools_by_name[name].invoke(args)
    except Exception as error:  # any tool error is reported back to the model
        return f"Error: {error}"
    return json.dumps(result, ensure_ascii=False)
