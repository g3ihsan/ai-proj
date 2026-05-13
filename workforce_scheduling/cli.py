from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .csv_adapter import (
    DEFAULT_MAX_CONSECUTIVE_DAYS,
    DEFAULT_MIN_REST_HOURS,
    DEFAULT_SHORTAGE_PENALTY,
    payload_from_csv_files,
    write_roster_solution_csv,
)
from .data import generate_synthetic_data
from .output import format_roster_text, write_roster_csv
from .schemas import error_payload, parse_solve_request, solve_payload
from .solve import Assignment, solve


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
    parser.add_argument(
        "--employees-csv",
        type=str,
        default="",
        help="employees.csv input path for the 3-file CSV solve boundary",
    )
    parser.add_argument(
        "--shifts-csv",
        type=str,
        default="",
        help="shifts.csv input path for the 3-file CSV solve boundary",
    )
    parser.add_argument(
        "--demand-csv",
        type=str,
        default="",
        help="demand.csv input path for the 3-file CSV solve boundary",
    )
    parser.add_argument(
        "--roster-csv",
        type=str,
        default="",
        help="Output roster CSV path for the 3-file CSV solve boundary",
    )
    parser.add_argument(
        "--min-rest-hours",
        type=int,
        default=DEFAULT_MIN_REST_HOURS,
        help="Minimum rest hours for CSV input solves",
    )
    parser.add_argument(
        "--max-consecutive-days",
        type=int,
        default=DEFAULT_MAX_CONSECUTIVE_DAYS,
        help="Maximum consecutive working days for CSV input solves",
    )
    parser.add_argument(
        "--shortage-penalty",
        type=int,
        default=DEFAULT_SHORTAGE_PENALTY,
        help="Shortage penalty for CSV input solves",
    )
    parser.add_argument(
        "--use-warm-start",
        action="store_true",
        help="Use deterministic warm-start hints for CSV input solves",
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

    csv_inputs = [args.employees_csv, args.shifts_csv, args.demand_csv]
    if any(csv_inputs):
        if not all(csv_inputs):
            print(
                "--employees-csv, --shifts-csv, and --demand-csv are required together",
                file=sys.stderr,
            )
            return 2
        if not args.roster_csv:
            print("--roster-csv is required for CSV input solves", file=sys.stderr)
            return 2
        try:
            request_payload = payload_from_csv_files(
                args.employees_csv,
                args.shifts_csv,
                args.demand_csv,
                min_rest_hours=args.min_rest_hours,
                max_consecutive_days=args.max_consecutive_days,
                shortage_penalty=args.shortage_penalty,
                time_limit_sec=args.time_limit,
                seed=args.seed,
                use_warm_start=args.use_warm_start,
            )
            data = parse_solve_request(request_payload).problem
            response_payload = solve_payload(request_payload)
        except Exception as exc:
            print(f"CSV solve failed: {exc}", file=sys.stderr)
            return 1
        if not response_payload["ok"]:
            error = response_payload["error"]
            print(
                f"CSV solve failed: {error['type']}: {error['message']}",
                file=sys.stderr,
            )
            return 1

        result_payload = response_payload["result"]
        _print_metrics_payload(result_payload)
        if result_payload["metrics"]["status"] not in ("OPTIMAL", "FEASIBLE"):
            print("No feasible schedule found")
            return 1
        if result_payload["violations"]:
            print("Validation issues detected:")
            for violation in result_payload["violations"]:
                print(f"  - {violation}")
            return 2
        write_roster_solution_csv(
            args.roster_csv,
            data,
            _assignments_from_payload(result_payload),
            _shortages_from_payload(result_payload),
        )
        print(f"CSV roster written to: {args.roster_csv}")
        return 0

    data = generate_synthetic_data(seed=args.seed)
    result = solve(data, time_limit_sec=args.time_limit, seed=args.seed)

    _print_metrics(result)

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


def _print_metrics(result) -> None:
    print("Solve metrics:")
    print(f"  status: {result.metrics.status}")
    print(f"  objective: {result.metrics.objective_value}")
    print(f"  best_bound: {result.metrics.best_bound}")
    print(f"  wall_time_sec: {result.metrics.wall_time_sec:.4f}")
    print(f"  conflicts: {result.metrics.num_conflicts}")
    print(f"  branches: {result.metrics.num_branches}")
    print(f"  variables: {result.metrics.num_variables}")
    print(f"  constraints: {result.metrics.num_constraints}")


def _print_metrics_payload(result_payload) -> None:
    metrics = result_payload["metrics"]
    print("Solve metrics:")
    print(f"  status: {metrics['status']}")
    print(f"  objective: {metrics['objective_value']}")
    print(f"  best_bound: {metrics['best_bound']}")
    print(f"  wall_time_sec: {metrics['wall_time_sec']:.4f}")
    print(f"  conflicts: {metrics['num_conflicts']}")
    print(f"  branches: {metrics['num_branches']}")
    print(f"  variables: {metrics['num_variables']}")
    print(f"  constraints: {metrics['num_constraints']}")


def _assignments_from_payload(result_payload) -> list[Assignment]:
    return [
        Assignment(
            employee_id=record["employee_id"],
            day=record["day"],
            shift=record["shift"],
            role=record["role"],
        )
        for record in result_payload["assignments"]
    ]


def _shortages_from_payload(result_payload) -> dict[tuple[int, int, str], int]:
    return {
        (record["day"], record["shift"], record["role"]): record["shortage_count"]
        for record in result_payload["shortages"]
    }


if __name__ == "__main__":
    sys.exit(main())
