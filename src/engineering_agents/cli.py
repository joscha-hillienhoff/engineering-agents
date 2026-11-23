"""Command-line interface.

engineering-agents query <name> [argument]
engineering-agents validate
engineering-agents check-replacement --type <new type> [--from <old type>]
engineering-agents analyse [--task <text>]
engineering-agents plan [--task <text>] [--type <new type>]
engineering-agents run [--task <text>] [--type <new type>] [--max-rounds <n>] [--auto-approve]
engineering-agents demo [--offline] [--auto-approve]
engineering-agents graph
"""

import argparse
import json
import textwrap
from collections.abc import Callable

import openai
from langchain_core.language_models import BaseChatModel
from langgraph.types import Command
from pydantic import BaseModel

from engineering_agents.agent import AgentResult, ToolCall, make_model
from engineering_agents.analysis import TASK, run_analysis
from engineering_agents.graph import ROOT, load_graph
from engineering_agents.offline import OfflineModel
from engineering_agents.orchestration import (
    Context,
    build_graph,
    initial_state,
    memory_checkpointer,
)
from engineering_agents.planning import run_planning
from engineering_agents.queries import QUERIES, run_query
from engineering_agents.validation import ValidationResult, check_replacements, validate_plant

GRAPH_DIAGRAM = ROOT / "docs" / "img" / "graph.mmd"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="engineering-agents")
    commands = parser.add_subparsers(dest="command", required=True)

    query_help = "\n".join(
        f"  {q.name:<24} {q.description}" + (f" (argument: {q.parameter})" if q.parameter else "")
        for q in QUERIES.values()
    )
    query_parser = commands.add_parser(
        "query",
        help="run a SPARQL query against the demo plant",
        description="Run a named SPARQL query and print the result as a table.\n\n"
        f"queries:\n{query_help}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    query_parser.add_argument("name", choices=QUERIES, metavar="name")
    query_parser.add_argument(
        "argument", nargs="?", help="local name of a device or device type, e.g. LT-101"
    )

    commands.add_parser(
        "validate",
        help="validate the demo plant against the SHACL shapes",
        description="Validate the demo plant against shapes/plant-shapes.ttl. "
        "Exits with status 1 if the plant does not conform.",
    )

    replacement_parser = commands.add_parser(
        "check-replacement",
        help="validate replacing every device of one type with another type",
        description="Replace the type of every device of the --from type with the --type type "
        "in a copy of the plant, and validate that copy. The plant itself is not changed. "
        "Exits with status 1 if the result does not conform.",
    )
    replacement_parser.add_argument(
        "--type", required=True, help="new device type, e.g. LevelTransmitterB"
    )
    replacement_parser.add_argument(
        "--from",
        dest="from_type",
        default="LevelTransmitterA",
        help="device type to replace (default: %(default)s)",
    )

    analyse_parser = commands.add_parser(
        "analyse",
        help="run the analysis agent once (calls the OpenAI API)",
        description="Run the analysis agent on a task and print its tool calls and its "
        "structured result. Needs OPENAI_API_KEY in the environment or in .env.",
    )
    analyse_parser.add_argument("--task", default=TASK, help="the task (default: %(default)s)")

    plan_parser = commands.add_parser(
        "plan",
        help="run the analysis agent, then the planning agent (calls the OpenAI API)",
        description="Run the analysis agent, hand its result to the planning agent, print "
        "both agents' tool calls and results, and validate the proposed plan. "
        "Needs OPENAI_API_KEY in the environment or in .env.",
    )
    plan_parser.add_argument("--task", default=TASK, help="the task (default: %(default)s)")
    plan_parser.add_argument(
        "--type",
        default="LevelTransmitterB",
        help="requested replacement type (default: %(default)s)",
    )

    run_parser = commands.add_parser(
        "run",
        help="run the whole multi-agent graph with the supervisor (calls the OpenAI API)",
        description="Run the LangGraph graph: the supervisor sends the task to the analysis "
        "agent, the planning agent and the SHACL validator, sends rejected plans back to the "
        "planner, pauses for a person to approve a conforming plan, and hands it over. "
        "Prints every step as it happens. Needs OPENAI_API_KEY in the environment or in .env.",
    )
    add_auto_approve(run_parser)
    run_parser.add_argument("--task", default=TASK, help="the task (default: %(default)s)")
    run_parser.add_argument(
        "--type",
        default="LevelTransmitterB",
        help="requested replacement type (default: %(default)s)",
    )
    run_parser.add_argument(
        "--max-rounds",
        type=int,
        default=Context.max_planning_rounds,
        help="planning rounds before the supervisor stops (default: %(default)s)",
    )

    demo_parser = commands.add_parser(
        "demo",
        help="run the whole scenario step by step, online or with a scripted offline model",
        description="Run the demo scenario end to end with narrated output: analysis, a plan "
        "with type B, the SHACL violation in zone 1, the revised plan, the approval prompt and "
        "the hand-over. Uses the OpenAI model if OPENAI_API_KEY works, and otherwise a scripted "
        "offline stand-in for the model; the output says which.",
    )
    demo_parser.add_argument(
        "--offline",
        action="store_true",
        help="use the scripted offline model even if an API key is available",
    )
    add_auto_approve(demo_parser)

    commands.add_parser(
        "graph",
        help="print the graph as a Mermaid diagram",
        description="Print the compiled LangGraph graph as a Mermaid diagram and write it "
        "to docs/img/graph.mmd.",
    )

    args = parser.parse_args(argv)

    if args.command == "graph":
        diagram = build_graph().get_graph().draw_mermaid()
        GRAPH_DIAGRAM.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_DIAGRAM.write_text(diagram)
        print(diagram)
        return

    if args.command == "run":
        try:
            model = make_model()
        except RuntimeError as error:
            parser.error(str(error))
        print(f"Model: {model.model_name}\n")
        context = Context(model=model, max_planning_rounds=args.max_rounds)
        run_graph(initial_state(args.task, args.type), context, reviewer(args.auto_approve))
        return

    if args.command == "demo":
        if args.offline:
            model, mode = OfflineModel(), OFFLINE.format(why="--offline")
        else:
            model, mode = demo_model()
        print(DEMO_INTRO.format(task=TASK, mode=mode))
        run_graph(initial_state(), Context(model=model), reviewer(args.auto_approve), narrate=True)
        return

    if args.command in ("analyse", "plan"):
        try:
            model = make_model()
        except RuntimeError as error:
            parser.error(str(error))
        print(f"Model: {model.model_name}\n")

        print("== Analysis agent")
        analysis = run_analysis(model, args.task, on_tool_call=print_tool_call)
        print_output(analysis)
        if args.command == "plan":
            print("\n== Planning agent")
            plan = run_planning(model, analysis.output, args.type, on_tool_call=print_tool_call)
            print_output(plan)
            print("\n== Validation of the proposed plan")
            try:
                result = check_replacements(load_graph(), plan.output.replacements())
            except ValueError as error:  # e.g. the model proposed a type that does not exist
                print(f"Cannot validate the plan: {error}")
            else:
                print(format_result(result))
        return

    graph = load_graph()

    if args.command == "query":
        query = QUERIES[args.name]
        if query.parameter and args.argument is None:
            query_parser.error(f"query {query.name!r} needs an argument ({query.parameter})")
        if not query.parameter and args.argument is not None:
            query_parser.error(f"query {query.name!r} takes no argument")
        print(format_table(run_query(graph, args.name, args.argument)))

    elif args.command == "validate":
        result = validate_plant(graph)
        print(format_result(result))
        if not result.conforms:
            raise SystemExit(1)

    elif args.command == "check-replacement":
        # Reuse the SPARQL query from chapter 01 to find the devices to change.
        points = run_query(graph, "installation-points", args.from_type)
        if not points:
            replacement_parser.error(f"no devices of type {args.from_type!r}")
        replacements = {row["device"]: args.type for row in points}
        try:
            result = check_replacements(graph, replacements)
        except ValueError as error:
            replacement_parser.error(str(error))
        devices = ", ".join(replacements)
        print(f"Proposed change: {args.from_type} -> {args.type} at {devices}\n")
        print(format_result(result))
        if not result.conforms:
            raise SystemExit(1)


Decision = dict  # the resume value: {"approved": True} or {"approved": False, "comment": ...}

DEMO_INTRO = """\
engineering-agents demo
=======================
Task: {task}

Mode: {mode}
"""

OFFLINE = (
    "offline - a scripted stand-in plays the language model ({why}).\n"
    "      The SPARQL tools, the SHACL validation and the LangGraph run are real."
)

# In the demo, a heading before each node explains what is about to happen.
STEPS = {
    "analysis": "Analysis agent: an LLM with SPARQL tools finds the affected devices",
    "planning": "Planning agent: an LLM with tools proposes a type for every device",
    "validator": "SHACL validator: plain code checks the plan against the rules",
    "approval": "Human approval: the graph pauses with an interrupt until a person decides",
    "hand_over": "Hand-over: the approved plan is the result of the run",
}


def run_graph(
    state: dict, context: Context, review: Callable[[], Decision], narrate: bool = False
) -> dict:
    """Run the graph and print each step as it happens; return the final state.

    ``stream`` yields events while the graph runs. Three kinds are requested:
    "custom" (what a node sends while it runs; here the agents' tool calls),
    "updates" (the part of the state a node returned, after each node) and
    "values" (the whole state after each step).

    When the run reaches the approval node it pauses and the stream ends. The
    checkpointer has saved the state under the thread id, so ``get_state``
    shows the pause and its request. ``review`` returns the person's decision,
    and streaming ``Command(resume=decision)`` on the same thread continues the
    run where it stopped. ``narrate`` adds a heading before each node.
    """
    graph = build_graph(checkpointer=memory_checkpointer())
    config = {"configurable": {"thread_id": "cli-run"}}  # where this run's checkpoints go
    graph_input = state
    step = 0
    while True:
        for mode, event in graph.stream(
            graph_input, config, context=context, stream_mode=["custom", "updates"]
        ):
            if mode == "custom":
                print_tool_call(event)
            elif "__interrupt__" not in event:  # the pause is handled below
                print_update(event)
                next_node = event.get("supervisor", {}).get("next")
                if narrate and next_node in STEPS:
                    step += 1
                    print(f"\n== Step {step} - {STEPS[next_node]}")
        paused = graph.get_state(config)
        if not paused.interrupts:
            break
        print("\n" + paused.interrupts[0].value + "\n")
        graph_input = Command(resume=review())

    final = paused.values
    print(f"\nStatus: {final['status']}")
    if final["status"] == "handed_over":
        print(f"Plan: {final['plan'].summary}")
    return final


def reviewer(auto_approve: bool) -> Callable[[], Decision]:
    """The person who approves plans: the terminal, or automatic approval."""
    return approve_automatically if auto_approve else ask_in_terminal


def ask_in_terminal() -> Decision:
    """Ask whether to approve the plan; a rejection needs a comment for the planner."""
    while True:
        try:
            answer = input("Approve this plan? [a]pprove / [r]eject: ").strip().lower()
        except EOFError:
            raise SystemExit(
                "No answer: the approval needs a terminal. "
                "Use --auto-approve for a non-interactive run."
            ) from None
        if answer in ("a", "approve"):
            return {"approved": True}
        if answer in ("r", "reject"):
            comment = input("Why? (sent to the planning agent): ").strip()
            return {"approved": False, "comment": comment}


def approve_automatically() -> Decision:
    print("--auto-approve: approved without asking (for tests and non-interactive runs only)")
    return {"approved": True}


def demo_model() -> tuple[BaseChatModel, str]:
    """The OpenAI model if the API key works, otherwise the offline model; and which mode it is.

    One cheap request checks the key: it looks up the model, which costs nothing.
    """
    try:
        model = make_model()
        model.root_client.with_options(timeout=10, max_retries=0).models.retrieve(model.model_name)
    except RuntimeError:
        return OfflineModel(), OFFLINE.format(why="OPENAI_API_KEY is not set")
    except openai.OpenAIError as error:
        why = f"the OpenAI API check failed: {type(error).__name__}"
        return OfflineModel(), OFFLINE.format(why=why)
    return model, f"online - the agents use the OpenAI model {model.model_name}."


def add_auto_approve(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="approve the plan without asking; for tests and non-interactive runs only",
    )


def print_update(event: dict[str, dict]) -> None:
    """Show what a node did: its trace notes, and its result (devices, plan, violations)."""
    for node, update in event.items():
        for entry in update["trace"]:
            print(f"[{entry['node']}] {entry['note']}")
        if node == "analysis":
            rows = [
                {"device": d.device, "location": d.location, "zone": d.zone}
                for d in update["analysis"].affected_devices
            ]
            print(textwrap.indent(format_table(rows), "    "))
            print(f"    {update['analysis'].ex_zone_note}")
        if node == "planning":
            rows = [
                {"device": e.device, "proposed type": e.proposed_type, "reason": e.reason}
                for e in update["plan"].entries
            ]
            print(textwrap.indent(format_table(rows), "    "))
        if node == "validator":
            for violation in update["violations"]:
                print(f"    - {violation.message}")


def print_tool_call(call: ToolCall) -> None:
    """Show one tool call while the agent is running: what it asked for, what it got."""
    print(f"tool call: {call.name}({json.dumps(call.args, ensure_ascii=False)})")
    print(f"  result:  {textwrap.shorten(call.result, width=150, placeholder=' ...')}")


def print_output(result: AgentResult[BaseModel]) -> None:
    """Show the structured result an agent returned."""
    print(f"\nstructured output ({type(result.output).__name__}):")
    print(result.output.model_dump_json(indent=2))


def format_result(result: ValidationResult) -> str:
    """Summarise a validation result: verdict, a table of violations, their messages."""
    if result.conforms:
        return "Conforms: yes"
    count = len(result.violations)
    rows = [
        {
            "device": v.device or "",
            "location": v.location or "",
            "zone": v.zone or "",
            "type": v.type or "",
        }
        for v in result.violations
    ]
    messages = "\n".join(f"- {v.message}" for v in result.violations)
    return (
        f"Conforms: no ({count} violation{'s' if count != 1 else ''})\n\n"
        f"{format_table(rows)}\n\n{messages}"
    )


def format_table(rows: list[dict[str, str]]) -> str:
    """Format result rows as a plain-text table with aligned columns."""
    if not rows:
        return "(no results)"
    columns = list(rows[0])
    widths = {c: max(len(c), *(len(row[c]) for row in rows)) for c in columns}
    lines = [
        "  ".join(c.ljust(widths[c]) for c in columns),
        "  ".join("-" * widths[c] for c in columns),
        *("  ".join(row[c].ljust(widths[c]) for c in columns) for row in rows),
    ]
    return "\n".join(line.rstrip() for line in lines)
