from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .data import generate_synthetic_data
from .output import format_roster_text, write_roster_csv
from .schemas import error_payload, solve_payload
from .solve import solve


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic weekly workforce scheduling sandbox"
    )
    parser.add_argument("--seed", type=int, default=1, help="Random seed")
    parser.add_argument(
        "--time-limit",
        type=float,
        default=10.0,
        help="Solver time limit in seconds",
    )
    parser.add_argument(
        "--output-csv",
        type=str,
        default="",
        help="Optional CSV output path",
    )
    parser.add_argument(
        "--no-roster",
        action="store_true",
        help="Suppress roster text output",
    )
    parser.add_argument(
        "--request-json",
        type=str,
        default="",
        help="Read a JSON solve request file and emit a JSON response envelope",
    )
    parser.add_argument(
        "--response-json",
        type=str,
        default="",
        help="Optional output path for --request-json response",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.request_json:
        try:
            request_payload = json.loads(Path(args.request_json).read_text())
            response_payload = solve_payload(request_payload)
        except Exception as exc:
            response_payload = error_payload(exc)
        response_text = json.dumps(response_payload, indent=2)
        if args.response_json:
            Path(args.response_json).write_text(response_text + "\n")
        else:
            print(response_text)
        return 0 if response_payload["ok"] else 1

    data = generate_synthetic_data(seed=args.seed)
    result = solve(data, time_limit_sec=args.time_limit, seed=args.seed)

    print("Solve metrics:")
    print(f"  status: {result.metrics.status}")
    print(f"  objective: {result.metrics.objective_value}")
    print(f"  best_bound: {result.metrics.best_bound}")
    print(f"  wall_time_sec: {result.metrics.wall_time_sec:.4f}")
    print(f"  conflicts: {result.metrics.num_conflicts}")
    print(f"  branches: {result.metrics.num_branches}")
    print(f"  variables: {result.metrics.num_variables}")
    print(f"  constraints: {result.metrics.num_constraints}")

    if result.metrics.status not in ("OPTIMAL", "FEASIBLE"):
        print("No feasible schedule found")
        return 1

    if result.violations:
        print("Validation issues detected:")
        for violation in result.violations:
            print(f"  - {violation}")
        return 2

    if not args.no_roster:
        print("Roster:")
        print(format_roster_text(data, result.assignments, result.shortages))

    if args.output_csv:
        write_roster_csv(args.output_csv, data, result.assignments)
        print(f"CSV roster written to: {args.output_csv}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
