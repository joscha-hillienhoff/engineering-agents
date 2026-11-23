"""Orchestration: a LangGraph graph in which a supervisor coordinates the agents.

The graph has six nodes that all read and write one shared ``State``:

- ``supervisor`` looks at the state and decides which node runs next, and why.
  It is plain code: the order of the work is fixed, so a rule is enough and
  easier to follow than asking a model.
- ``analysis`` runs the analysis agent (an LLM with tools).
- ``planning`` runs the planning agent (an LLM with tools). On a later round it
  also receives the rejected plan and the feedback: rule violations, or the
  comment of the person who rejected it.
- ``validator`` checks the plan against the SHACL shapes. It is deterministic
  code, not an agent: a rule check must give the same answer every time.
- ``approval`` pauses the run with an *interrupt* and waits for a person to
  approve or reject the conforming plan.
- ``hand_over`` records the approved plan as the result of the run.

Every worker node returns to the supervisor, so the supervisor sees every step
and is the only place where the route is decided. A rejected plan goes back to
the planner; after ``max_planning_rounds`` rejected plans the supervisor stops
and asks for a person instead of looping on.

The model is not part of the graph. It is passed in for each run as the
*context* (``Context``), so the graph can be built, drawn and tested without
an API key.

An interrupt only works with a *checkpointer*: it saves the state after every
step, so a paused run can be resumed later with ``Command(resume=...)``. Each
run gets a thread id, under which its checkpoints are stored.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from engineering_agents.analysis import TASK, Analysis, run_analysis
from engineering_agents.planning import ReplacementPlan, run_planning
from engineering_agents.tools import plant_graph
from engineering_agents.validation import Violation, check_replacements

# What has happened so far; the supervisor routes on this alone.
Status = Literal[
    "new",  # nothing done yet
    "analysed",  # the analysis is there, no plan yet
    "planned",  # a new plan is there and has not been checked
    "rejected",  # the plan breaks rules; see violations
    "conforms",  # the plan passed the validation and waits for a person
    "approved",  # a person approved the plan
    "changes_requested",  # a person rejected the plan; see review_comment
    "needs_human",  # stopped: too many rejected plans
    "handed_over",  # done: the approved plan is the result
]


class TraceEntry(TypedDict):
    """One line of the run's history: which node ran and what it did or decided."""

    node: str
    note: str


class State(TypedDict):
    """The shared state. Every node receives all of it and returns only what it changes."""

    task: str
    replacement_type: str
    analysis: Analysis | None
    plan: ReplacementPlan | None
    violations: list[Violation]
    review_comment: str | None  # the reviewer's latest comment on a rejected plan
    planning_rounds: int
    status: Status
    next: str  # the supervisor's last routing decision
    # Reducers: the lists that nodes return are appended instead of replacing
    # the old ones, so these keep the whole history of a run.
    trace: Annotated[list[TraceEntry], operator.add]
    history: Annotated[list[str], operator.add]  # each verdict on a plan, and why


@dataclass(frozen=True)
class Context:
    """What a run needs besides the state: the model and the iteration limit."""

    model: BaseChatModel
    max_planning_rounds: int = 3


def initial_state(task: str = TASK, replacement_type: str = "LevelTransmitterB") -> State:
    """The state a run starts with: only the task and the requested type are known."""
    return State(
        task=task,
        replacement_type=replacement_type,
        analysis=None,
        plan=None,
        violations=[],
        review_comment=None,
        planning_rounds=0,
        status="new",
        next="",
        trace=[],
        history=[],
    )


def supervisor(state: State, runtime: Runtime[Context]) -> dict:
    """Decide the next node from the status, and say why."""
    status = state["status"]
    if status == "new":
        return _route("analysis", "no analysis yet; find the affected devices first")
    if status == "analysed":
        return _route("planning", "the affected devices are known; a plan is needed")
    if status == "planned":
        return _route("validator", "a new plan must be checked against the rules")
    if status == "conforms":
        return _route("approval", "the plan conforms to all rules; a person must approve it")
    if status == "approved":
        return _route("hand_over", "the reviewer approved the plan")
    if status in ("rejected", "changes_requested"):
        rounds, limit = state["planning_rounds"], runtime.context.max_planning_rounds
        if rounds >= limit:
            return {
                "status": "needs_human",
                "next": END,
                "trace": [
                    _note("supervisor", f"stop: {rounds} plans rejected, a person must decide")
                ],
            }
        if status == "changes_requested":
            return _route("planning", "the reviewer rejected the plan; send it back with why")
        count = len(state["violations"])
        return _route("planning", f"the plan breaks {count} rule(s); send it back with them")
    raise ValueError(f"the supervisor has no route for status {status!r}")


def analysis(state: State, runtime: Runtime[Context]) -> dict:
    """Run the analysis agent on the task."""
    result = run_analysis(runtime.context.model, state["task"], on_tool_call=runtime.stream_writer)
    devices = result.output.affected_devices
    found = ", ".join(f"{d.device} ({d.zone})" for d in devices)
    return {
        "analysis": result.output,
        "status": "analysed",
        "trace": [_note("analysis", f"found {len(devices)} device(s): {found}")],
    }


def planning(state: State, runtime: Runtime[Context]) -> dict:
    """Run the planning agent; after a rejection, with the rejected plan and the feedback."""
    result = run_planning(
        runtime.context.model,
        state["analysis"],
        state["replacement_type"],
        previous_plan=state["plan"],
        violations=state["violations"],
        review_comment=state["review_comment"],
        on_tool_call=runtime.stream_writer,
    )
    rounds = state["planning_rounds"] + 1
    action = "revised the plan" if state["plan"] is not None else "proposed a plan"
    return {
        "plan": result.output,
        "planning_rounds": rounds,
        "status": "planned",
        "trace": [_note("planning", f"{action} (round {rounds})")],
    }


def validator(state: State) -> dict:
    """Check the plan with SHACL. Plain code: no model, no context."""
    try:
        result = check_replacements(plant_graph(), state["plan"].replacements())
    except ValueError as error:  # e.g. the plan names a type that does not exist
        violations = [Violation(None, None, None, None, f"The plan cannot be applied: {error}.")]
    else:
        violations = result.violations
    round_ = f"round {state.get('planning_rounds', 0)}"
    if not violations:
        return {
            "violations": [],
            "status": "conforms",
            "trace": [_note("validator", "conforms")],
            "history": [f"{round_}: conforms to the SHACL rules"],
        }
    count = len(violations)
    reasons = "".join(f"\n    - {violation.message}" for violation in violations)
    return {
        "violations": violations,
        "status": "rejected",
        "trace": [_note("validator", f"does not conform: {count} violation(s)")],
        "history": [f"{round_}: rejected by the SHACL validator{reasons}"],
    }


def approval(state: State) -> dict:
    """Pause the run until a person approves or rejects the plan.

    ``interrupt(request)`` stops the run here. The checkpointer has saved the
    state, and the caller receives ``request``. The caller resumes the run with
    ``Command(resume=decision)``; LangGraph then runs this node again from its
    first line, and this time ``interrupt`` returns the decision instead of
    stopping. The decision is ``{"approved": True}`` or
    ``{"approved": False, "comment": "..."}``.
    """
    decision = interrupt(approval_request(state))
    round_ = f"round {state['planning_rounds']}"
    if decision["approved"]:
        return {
            "status": "approved",
            "trace": [_note("approval", "approved by the reviewer")],
            "history": [f"{round_}: approved by the reviewer"],
        }
    comment = decision.get("comment") or "(no comment)"
    return {
        "status": "changes_requested",
        "review_comment": comment,
        "trace": [_note("approval", f"rejected by the reviewer: {comment}")],
        "history": [f"{round_}: rejected by the reviewer: {comment}"],
    }


def approval_request(state: State) -> str:
    """What the reviewer sees: every device with its zone and types, and the history."""
    zones = {device.device: device.zone for device in state["analysis"].affected_devices}
    entries = state["plan"].entries
    device_width = max(len(entry.device) for entry in entries)
    zone_width = max(len(zones.get(entry.device, "?")) for entry in entries)
    rows = "\n".join(
        f"  {entry.device:<{device_width}}  {zones.get(entry.device, '?'):<{zone_width}}  "
        f"{entry.current_type} -> {entry.proposed_type}"
        for entry in entries
    )
    history = "\n".join(f"  {line}" for line in state["history"])
    return (
        f"Approval needed: replacement plan, round {state['planning_rounds']}, "
        f"{len(entries)} device(s)\n"
        f"{rows}\n"
        f"Summary: {state['plan'].summary}\n"
        f"History:\n{history}"
    )


def hand_over(state: State) -> dict:
    """Record the approved plan as the result of the run."""
    count = len(state["plan"].entries)
    return {
        "status": "handed_over",
        "trace": [_note("hand_over", f"plan with {count} entries handed over")],
    }


def memory_checkpointer() -> InMemorySaver:
    """A checkpointer that keeps the saved states in memory, for as long as the process runs.

    Checkpoints store the state in a serialized form. When they are read back,
    LangGraph rebuilds only the classes it has been told are safe, so the
    project's own state classes are listed here.
    """
    safe = [Analysis, ReplacementPlan, Violation]
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=safe))


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    """Wire the nodes into a graph and compile it into something that can run.

    A run that reaches ``approval`` needs a ``checkpointer`` (for example
    ``memory_checkpointer()``) and a thread id in its config; without them
    LangGraph cannot pause it. Building the graph only to draw it needs neither.
    """
    builder = StateGraph(State, context_schema=Context)
    builder.add_node("supervisor", supervisor)
    builder.add_node("analysis", analysis)
    builder.add_node("planning", planning)
    builder.add_node("validator", validator)
    builder.add_node("approval", approval)
    builder.add_node("hand_over", hand_over)

    builder.add_edge(START, "supervisor")
    # The supervisor's decision picks the next node. The list names every
    # possible target, so the drawn graph can show all of them.
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state["next"],
        ["analysis", "planning", "validator", "approval", "hand_over", END],
    )
    for worker in ("analysis", "planning", "validator", "approval"):
        builder.add_edge(worker, "supervisor")
    builder.add_edge("hand_over", END)
    return builder.compile(checkpointer=checkpointer)


def _route(target: str, why: str) -> dict:
    return {"next": target, "trace": [_note("supervisor", f"-> {target}: {why}")]}


def _note(node: str, note: str) -> TraceEntry:
    return TraceEntry(node=node, note=note)
