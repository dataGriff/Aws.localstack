"""CLI: python -m tools.smoke --env dev [--timeout 300]"""

from __future__ import annotations

import argparse
import sys

from tools.smoke import SmokeFailureError, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post-deploy smoke test")
    parser.add_argument("--env", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args(argv)
    try:
        result = run(args.env, timeout=args.timeout, interval=args.interval)
    except SmokeFailureError as exc:
        print(f"SMOKE FAILED: {exc}", file=sys.stderr)
        return 1
    print(f"SMOKE OK: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
