"""Orchestration: a LangGraph graph in which a supervisor coordinates the agents.

The graph has five nodes that all read and write one shared ``State``:

- ``supervisor`` looks at the state and decides which node runs next, and why.
  It is plain code: the order of the work is fixed, so a rule is enough and
  easier to follow than asking a model.
- ``analysis`` runs the analysis agent (an LLM with tools).
- ``planning`` runs the planning agent (an LLM with tools). On a second round it
  also receives the rejected plan and the violations.
- ``validator`` checks the plan against the SHACL shapes. It is deterministic
  code, not an agent: a rule check must give the same answer every time.
- ``hand_over`` records the accepted plan as the result of the run.

Every worker node returns to the supervisor, so the supervisor sees every step
and is the only place where the route is decided. A rejected plan goes back to
the planner; after ``max_planning_rounds`` rejected plans the supervisor stops
and asks for a person instead of looping on.

The model is not part of the graph. It is passed in for each run as the
*context* (``Context``), so the graph can be built, drawn and tested without
an API key.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

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
    "conforms",  # the plan passed the validation
    "needs_human",  # stopped: too many rejected plans
    "handed_over",  # done: the accepted plan is the result
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
    planning_rounds: int
    status: Status
    next: str  # the supervisor's last routing decision
    # A reducer: the lists that nodes return are appended to the trace instead
    # of replacing it, so the trace keeps the whole history.
    trace: Annotated[list[TraceEntry], operator.add]


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
        planning_rounds=0,
        status="new",
        next="",
        trace=[],
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
        # A human approval will sit between this decision and the hand-over.
        return _route("hand_over", "the plan conforms to all rules")
    if status == "rejected":
        rounds, limit = state["planning_rounds"], runtime.context.max_planning_rounds
        if rounds >= limit:
            return {
                "status": "needs_human",
                "next": END,
                "trace": [
                    _note("supervisor", f"stop: {rounds} plans rejected, a person must decide")
                ],
            }
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
    """Run the planning agent; after a rejection, with the rejected plan and its violations."""
    result = run_planning(
        runtime.context.model,
        state["analysis"],
        state["replacement_type"],
        previous_plan=state["plan"],
        violations=state["violations"],
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
    if not violations:
        return {"violations": [], "status": "conforms", "trace": [_note("validator", "conforms")]}
    count = len(violations)
    return {
        "violations": violations,
        "status": "rejected",
        "trace": [_note("validator", f"does not conform: {count} violation(s)")],
    }


def hand_over(state: State) -> dict:
    """Record the accepted plan as the result of the run."""
    count = len(state["plan"].entries)
    return {
        "status": "handed_over",
        "trace": [_note("hand_over", f"plan with {count} entries handed over")],
    }


def build_graph():
    """Wire the nodes into a graph and compile it into something that can run."""
    builder = StateGraph(State, context_schema=Context)
    builder.add_node("supervisor", supervisor)
    builder.add_node("analysis", analysis)
    builder.add_node("planning", planning)
    builder.add_node("validator", validator)
    builder.add_node("hand_over", hand_over)

    builder.add_edge(START, "supervisor")
    # The supervisor's decision picks the next node. The list names every
    # possible target, so the drawn graph can show all of them.
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state["next"],
        ["analysis", "planning", "validator", "hand_over", END],
    )
    for worker in ("analysis", "planning", "validator"):
        builder.add_edge(worker, "supervisor")
    builder.add_edge("hand_over", END)
    return builder.compile()


def _route(target: str, why: str) -> dict:
    return {"next": target, "trace": [_note("supervisor", f"-> {target}: {why}")]}


def _note(node: str, note: str) -> TraceEntry:
    return TraceEntry(node=node, note=note)
