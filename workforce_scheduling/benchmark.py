from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List

from .data import Employee, ProblemData, generate_synthetic_data
from .solve import SolveResult, solve


@dataclass(frozen=True)
class BenchmarkCase:
    name: str
    description: str
    data_factory: Callable[[], ProblemData]
    time_limit_sec: float = 10.0
    seed: int = 1


@dataclass(frozen=True)
class BenchmarkResult:
    name: str
    status: str
    objective_value: float | None
    best_bound: float | None
    wall_time_sec: float
    num_conflicts: int
    num_branches: int
    num_variables: int
    num_constraints: int
    assignment_count: int
    total_shortage: int
    shortage_objective_value: int
    workload_fairness_value: int
    weekend_fairness_value: int
    shift_distribution_fairness_value: int
    fairness_objective_value: int
    labor_cost_value: int
    total_objective_value: int
    validation_violation_count: int


def benchmark_cases() -> List[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="small_fully_feasible",
            description="Two employees covering two simple weekday shifts.",
            data_factory=small_fully_feasible_case,
        ),
        BenchmarkCase(
            name="temporal_rest_constrained",
            description="One employee cannot cover close-to-open demand due to rest.",
            data_factory=temporal_rest_constrained_case,
        ),
        BenchmarkCase(
            name="unavoidable_understaffing",
            description="Demand exceeds qualified available workers for one slot.",
            data_factory=unavoidable_understaffing_case,
        ),
        BenchmarkCase(
            name="fairness_vs_cost",
            description="Fair full-coverage roster is more expensive than unfair coverage.",
            data_factory=fairness_vs_cost_case,
        ),
        BenchmarkCase(
            name="synthetic_40_employee_weekly",
            description="Deterministic generated 40-employee, 7-day, 3-shift case.",
            data_factory=synthetic_40_employee_weekly_case,
            time_limit_sec=15.0,
            seed=7,
        ),
    ]


def small_fully_feasible_case() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(num_days=2, num_shifts=1, roles=roles, default=1)
    employees = [
        _make_employee(0, roles, [[True], [True]]),
        _make_employee(1, roles, [[True], [True]]),
    ]
    return _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )


def temporal_rest_constrained_case() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(num_days=2, num_shifts=2, roles=roles)
    demand[0][1]["worker"] = 1
    demand[1][0]["worker"] = 1
    employees = [_make_employee(0, roles, [[True, True], [True, True]])]
    return _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8, 20],
        shift_end_hours=[16, 4],
        demand=demand,
        min_rest_hours=10,
        max_consecutive_days=5,
    )


def unavoidable_understaffing_case() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(num_days=1, num_shifts=1, roles=roles, default=2)
    employees = [_make_employee(0, roles, [[True]])]
    return _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )


def fairness_vs_cost_case() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(num_days=2, num_shifts=1, roles=roles, default=1)
    employees = [
        _make_employee(0, roles, [[True], [True]], hourly_cost=1),
        _make_employee(1, roles, [[True], [True]], hourly_cost=1000),
    ]
    return _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )


def synthetic_40_employee_weekly_case() -> ProblemData:
    return generate_synthetic_data(
        seed=17,
        num_employees=40,
        num_days=7,
        shifts_per_day=3,
    )


def run_benchmark_case(case: BenchmarkCase) -> BenchmarkResult:
    result = solve(
        case.data_factory(),
        time_limit_sec=case.time_limit_sec,
        seed=case.seed,
    )
    return _result_from_solve(case.name, result)


def run_benchmarks(
    cases: Iterable[BenchmarkCase] | None = None,
) -> List[BenchmarkResult]:
    selected_cases = list(cases) if cases is not None else benchmark_cases()
    return [run_benchmark_case(case) for case in selected_cases]


def format_benchmark_results(results: List[BenchmarkResult]) -> str:
    headers = [
        "case",
        "status",
        "objective",
        "shortage",
        "fairness",
        "labor_cost",
        "vars",
        "constraints",
        "branches",
        "conflicts",
        "wall_sec",
    ]
    rows = [
        [
            result.name,
            result.status,
            _format_objective(result.objective_value),
            str(result.total_shortage),
            str(
                result.workload_fairness_value
                + result.weekend_fairness_value
                + result.shift_distribution_fairness_value
            ),
            str(result.labor_cost_value),
            str(result.num_variables),
            str(result.num_constraints),
            str(result.num_branches),
            str(result.num_conflicts),
            f"{result.wall_time_sec:.4f}",
        ]
        for result in results
    ]
    widths = [
        max(len(row[idx]) for row in [headers, *rows])
        for idx in range(len(headers))
    ]
    lines = [
        "  ".join(
            value.ljust(widths[idx])
            for idx, value in enumerate(headers)
        )
    ]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend(
        "  ".join(
            value.ljust(widths[idx])
            for idx, value in enumerate(row)
        )
        for row in rows
    )
    return "\n".join(lines)


def main() -> int:
    parser = ArgumentParser(
        description="Run deterministic workforce solver benchmark fixtures."
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=[case.name for case in benchmark_cases()],
        help="Benchmark case name. May be passed more than once.",
    )
    args = parser.parse_args()

    cases = benchmark_cases()
    if args.case:
        requested = set(args.case)
        cases = [case for case in cases if case.name in requested]

    results = run_benchmarks(cases)
    print(format_benchmark_results(results))
    return 0 if all(result.status in ("OPTIMAL", "FEASIBLE") for result in results) else 1


def _result_from_solve(name: str, result: SolveResult) -> BenchmarkResult:
    breakdown = result.objective_breakdown
    return BenchmarkResult(
        name=name,
        status=result.metrics.status,
        objective_value=result.metrics.objective_value,
        best_bound=result.metrics.best_bound,
        wall_time_sec=result.metrics.wall_time_sec,
        num_conflicts=result.metrics.num_conflicts,
        num_branches=result.metrics.num_branches,
        num_variables=result.metrics.num_variables,
        num_constraints=result.metrics.num_constraints,
        assignment_count=len(result.assignments),
        total_shortage=breakdown.total_shortage,
        shortage_objective_value=breakdown.shortage_objective_value,
        workload_fairness_value=breakdown.workload_fairness_value,
        weekend_fairness_value=breakdown.weekend_fairness_value,
        shift_distribution_fairness_value=breakdown.shift_distribution_fairness_value,
        fairness_objective_value=breakdown.fairness_objective_value,
        labor_cost_value=breakdown.labor_cost_value,
        total_objective_value=breakdown.total_objective_value,
        validation_violation_count=len(result.violations),
    )


def _build_demand(
    num_days: int,
    num_shifts: int,
    roles: List[str],
    default: int = 0,
) -> Dict[int, Dict[int, Dict[str, int]]]:
    return {
        day: {
            shift: {role: default for role in roles}
            for shift in range(num_shifts)
        }
        for day in range(num_days)
    }


def _make_employee(
    employee_id: int,
    roles: List[str],
    availability: List[List[bool]],
    hourly_cost: int = 20,
    max_weekly_hours: int = 40,
) -> Employee:
    return Employee(
        employee_id=employee_id,
        name=f"E{employee_id}",
        roles=tuple(roles),
        hourly_cost=hourly_cost,
        max_weekly_hours=max_weekly_hours,
        availability=availability,
    )


def _make_problem(
    employees: List[Employee],
    roles: List[str],
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    demand: Dict[int, Dict[int, Dict[str, int]]],
    min_rest_hours: int,
    max_consecutive_days: int,
    shortage_penalty: int = 1000,
) -> ProblemData:
    return ProblemData(
        employees=employees,
        roles=roles,
        days=list(range(len(demand))),
        shifts=[f"shift_{idx}" for idx in range(len(shift_start_hours))],
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        min_rest_hours=min_rest_hours,
        max_consecutive_days=max_consecutive_days,
        shortage_penalty=shortage_penalty,
        demand=demand,
        hint_assignments={},
    )


def _format_objective(objective_value: float | None) -> str:
    if objective_value is None:
        return "-"
    if objective_value.is_integer():
        return str(int(objective_value))
    return f"{objective_value:.4f}"


if __name__ == "__main__":
    raise SystemExit(main())
