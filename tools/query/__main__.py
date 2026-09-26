"""CLI: python -m tools.query --env local [--sql "..."] [--file path.sql].

Without --sql/--file it reads statements from stdin (end each with a semicolon).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tools.query import open_connection

EXAMPLES = Path(__file__).resolve().parent / "examples"


def print_result(con, sql: str) -> None:  # type: ignore[no-untyped-def]
    rel = con.sql(sql)
    if rel is not None:
        rel.show(max_rows=200)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Query the Iceberg archive with DuckDB")
    parser.add_argument("--env", default="local")
    parser.add_argument("--sql", help="run one statement and exit")
    parser.add_argument("--file", type=Path, help="run every statement in a .sql file and exit")
    args = parser.parse_args(argv)

    con = open_connection(args.env)
    if args.sql:
        print_result(con, args.sql)
        return 0
    if args.file:
        for statement in args.file.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                print(f"-- {statement.strip()}")
                print_result(con, statement)
        return 0

    print("Views: ingress_events, domain_events (latest snapshot). End statements with ';'.")
    print(f"Examples: {EXAMPLES}")
    buffer: list[str] = []
    for line in sys.stdin:
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statement = "".join(buffer)
            buffer.clear()
            try:
                print_result(con, statement)
            except Exception as exc:
                print(f"error: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
