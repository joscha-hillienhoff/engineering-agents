"""Command-line interface: ``engineering-agents query <name> [argument]``."""

import argparse

from engineering_agents.graph import load_graph
from engineering_agents.queries import QUERIES, run_query


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

    args = parser.parse_args(argv)
    query = QUERIES[args.name]
    if query.parameter and args.argument is None:
        query_parser.error(f"query {query.name!r} needs an argument ({query.parameter})")
    if not query.parameter and args.argument is not None:
        query_parser.error(f"query {query.name!r} takes no argument")

    rows = run_query(load_graph(), args.name, args.argument)
    print(format_table(rows))


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
