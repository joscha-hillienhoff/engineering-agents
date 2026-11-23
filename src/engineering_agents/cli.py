"""Command-line interface.

engineering-agents query <name> [argument]
engineering-agents validate
engineering-agents check-replacement --type <new type> [--from <old type>]
"""

import argparse

from engineering_agents.graph import load_graph
from engineering_agents.queries import QUERIES, run_query
from engineering_agents.validation import ValidationResult, check_replacements, validate_plant


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

    args = parser.parse_args(argv)
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
