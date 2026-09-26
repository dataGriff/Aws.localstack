"""CLI: python -m tools.generator --env local --sets valid,duplicates [--synthetic] [--fresh]"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from tools.common import output
from tools.generator import ALL_SETS, FIXTURES_DIR, freshen, load_set, mark_synthetic, send


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay third-party fixtures onto the ingress bus")
    parser.add_argument("--env", default="local", help="environment whose outputs file to read")
    parser.add_argument("--bus-name", help="override the ingress bus name from outputs")
    parser.add_argument("--fixtures", type=Path, default=FIXTURES_DIR)
    parser.add_argument(
        "--sets", default=",".join(ALL_SETS), help="comma-separated fixture folders"
    )
    parser.add_argument(
        "--synthetic", action="store_true", help="mark payloads as smoke-test traffic"
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        default=True,
        help="suffix provider ids so re-runs are not deduplicated away (default)",
    )
    parser.add_argument("--no-fresh", dest="fresh", action="store_false")
    args = parser.parse_args(argv)

    bus = args.bus_name or output(args.env, "IngressBusName")
    suffix = uuid.uuid4().hex[:10]
    total = 0
    for name in [s.strip() for s in args.sets.split(",") if s.strip()]:
        payloads = load_set(name, args.fixtures)
        if args.fresh:
            payloads = [freshen(p, suffix) for p in payloads]
        if args.synthetic:
            payloads = [mark_synthetic(p) for p in payloads]
        count = send(bus, payloads)
        total += count
        print(f"{name}: sent {count} payload(s) to {bus}")
    print(f"total {total} (run suffix {suffix})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
