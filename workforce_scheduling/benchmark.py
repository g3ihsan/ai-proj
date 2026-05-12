from __future__ import annotations

from argparse import ArgumentParser
from dataclasses import asdict, dataclass
import json
from typing import Callable, Dict, Iterable, List

from .data import Employee, ProblemData, generate_synthetic_data
from .solve import SolveResult, solve
from .warm_start import with_warm_start_hints, without_hints


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
    employee_count: int
    day_count: int
    shift_count: int
    total_demand: int
    warm_start_enabled: bool
    hint_count: int
    status: str
    objective_value: float | None
    best_bound: float | None
    absolute_optimality_gap: float | None
    relative_optimality_gap_percent: float | None
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


@dataclass(frozen=True)
class BenchmarkComparison:
    name: str
    baseline: BenchmarkResult
    warm_start: BenchmarkResult


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


def scaling_benchmark_cases() -> List[BenchmarkCase]:
    return [
        BenchmarkCase(
            name="synthetic_20_employee_weekly",
            description=(
                "Deterministic generated 20-employee weekly case with scaled "
                "coverage demand."
            ),
            data_factory=synthetic_20_employee_weekly_case,
            time_limit_sec=10.0,
            seed=7,
        ),
        BenchmarkCase(
            name="synthetic_40_employee_weekly",
            description="Deterministic generated 40-employee, 7-day, 3-shift case.",
            data_factory=synthetic_40_employee_weekly_case,
            time_limit_sec=15.0,
            seed=7,
        ),
        BenchmarkCase(
            name="synthetic_80_employee_weekly",
            description="Deterministic generated 80-employee, 7-day, 3-shift case.",
            data_factory=synthetic_80_employee_weekly_case,
            time_limit_sec=20.0,
            seed=7,
        ),
        BenchmarkCase(
            name="synthetic_120_employee_weekly",
            description="Deterministic generated 120-employee, 7-day, 3-shift case.",
            data_factory=synthetic_120_employee_weekly_case,
            time_limit_sec=25.0,
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


def synthetic_20_employee_weekly_case() -> ProblemData:
    return generate_synthetic_data(
        seed=17,
        num_employees=20,
        num_days=7,
        shifts_per_day=3,
        base_role_demand={"cashier": 1, "cook": 1, "manager": 1},
    )


def synthetic_80_employee_weekly_case() -> ProblemData:
    return generate_synthetic_data(
        seed=17,
        num_employees=80,
        num_days=7,
        shifts_per_day=3,
    )


def synthetic_120_employee_weekly_case() -> ProblemData:
    return generate_synthetic_data(
        seed=17,
        num_employees=120,
        num_days=7,
        shifts_per_day=3,
    )


def run_benchmark_case(
    case: BenchmarkCase,
    *,
    warm_start: bool = True,
) -> BenchmarkResult:
    data = case.data_factory()
    if warm_start:
        data = with_warm_start_hints(data)
    else:
        data = without_hints(data)
    result = solve(
        data,
        time_limit_sec=case.time_limit_sec,
        seed=case.seed,
    )
    return _result_from_solve(
        case.name,
        data,
        result,
        warm_start_enabled=warm_start,
        hint_count=len(data.hint_assignments),
    )


def run_benchmarks(
    cases: Iterable[BenchmarkCase] | None = None,
    *,
    warm_start: bool = True,
) -> List[BenchmarkResult]:
    selected_cases = list(cases) if cases is not None else benchmark_cases()
    return [
        run_benchmark_case(case, warm_start=warm_start)
        for case in selected_cases
    ]


def run_benchmark_comparisons(
    cases: Iterable[BenchmarkCase] | None = None,
) -> List[BenchmarkComparison]:
    selected_cases = list(cases) if cases is not None else benchmark_cases()
    return [
        BenchmarkComparison(
            name=case.name,
            baseline=run_benchmark_case(case, warm_start=False),
            warm_start=run_benchmark_case(case, warm_start=True),
        )
        for case in selected_cases
    ]


def format_benchmark_results(results: List[BenchmarkResult]) -> str:
    headers = [
        "case",
        "employees",
        "demand",
        "warm_start",
        "hints",
        "status",
        "objective",
        "best_bound",
        "gap_abs",
        "gap_pct",
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
            str(result.employee_count),
            str(result.total_demand),
            "yes" if result.warm_start_enabled else "no",
            str(result.hint_count),
            result.status,
            _format_objective(result.objective_value),
            _format_number(result.best_bound),
            _format_number(result.absolute_optimality_gap),
            _format_percent(result.relative_optimality_gap_percent),
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
    return "\n\n".join(
        [
            "\n".join(lines),
            format_benchmark_summary(summarize_benchmark_results(results)),
        ]
    )


def format_benchmark_comparisons(comparisons: List[BenchmarkComparison]) -> str:
    headers = [
        "case",
        "employees",
        "demand",
        "base_status",
        "warm_status",
        "base_wall",
        "warm_wall",
        "base_obj",
        "warm_obj",
        "base_bound",
        "warm_bound",
        "base_gap_pct",
        "warm_gap_pct",
        "base_branches",
        "warm_branches",
        "base_conflicts",
        "warm_conflicts",
        "base_shortage",
        "warm_shortage",
        "base_violations",
        "warm_violations",
        "warm_hints",
    ]
    rows = [
        [
            comparison.name,
            str(comparison.baseline.employee_count),
            str(comparison.baseline.total_demand),
            comparison.baseline.status,
            comparison.warm_start.status,
            f"{comparison.baseline.wall_time_sec:.4f}",
            f"{comparison.warm_start.wall_time_sec:.4f}",
            _format_objective(comparison.baseline.objective_value),
            _format_objective(comparison.warm_start.objective_value),
            _format_number(comparison.baseline.best_bound),
            _format_number(comparison.warm_start.best_bound),
            _format_percent(comparison.baseline.relative_optimality_gap_percent),
            _format_percent(comparison.warm_start.relative_optimality_gap_percent),
            str(comparison.baseline.num_branches),
            str(comparison.warm_start.num_branches),
            str(comparison.baseline.num_conflicts),
            str(comparison.warm_start.num_conflicts),
            str(comparison.baseline.total_shortage),
            str(comparison.warm_start.total_shortage),
            str(comparison.baseline.validation_violation_count),
            str(comparison.warm_start.validation_violation_count),
            str(comparison.warm_start.hint_count),
        ]
        for comparison in comparisons
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
    return "\n\n".join(
        [
            "\n".join(lines),
            format_comparison_summary(summarize_benchmark_comparisons(comparisons)),
        ]
    )


def summarize_benchmark_results(results: List[BenchmarkResult]) -> Dict[str, object]:
    if not results:
        return {
            "case_count": 0,
            "fastest_case": None,
            "slowest_case": None,
            "largest_variable_count": None,
            "largest_variable_count_case": None,
            "largest_constraint_count": None,
            "largest_constraint_count_case": None,
            "optimal_cases": 0,
            "feasible_cases": 0,
            "non_success_cases": 0,
            "has_validation_violations": False,
            "total_shortage": 0,
        }

    fastest = min(results, key=lambda result: result.wall_time_sec)
    slowest = max(results, key=lambda result: result.wall_time_sec)
    largest_variables = max(results, key=lambda result: result.num_variables)
    largest_constraints = max(results, key=lambda result: result.num_constraints)
    success_statuses = {"OPTIMAL", "FEASIBLE"}
    return {
        "case_count": len(results),
        "fastest_case": fastest.name,
        "slowest_case": slowest.name,
        "largest_variable_count": largest_variables.num_variables,
        "largest_variable_count_case": largest_variables.name,
        "largest_constraint_count": largest_constraints.num_constraints,
        "largest_constraint_count_case": largest_constraints.name,
        "optimal_cases": sum(1 for result in results if result.status == "OPTIMAL"),
        "feasible_cases": sum(1 for result in results if result.status == "FEASIBLE"),
        "non_success_cases": sum(
            1 for result in results if result.status not in success_statuses
        ),
        "has_validation_violations": any(
            result.validation_violation_count > 0 for result in results
        ),
        "total_shortage": sum(result.total_shortage for result in results),
    }


def summarize_benchmark_comparisons(
    comparisons: List[BenchmarkComparison],
) -> Dict[str, object]:
    improved_wall_time = [
        comparison.name
        for comparison in comparisons
        if comparison.warm_start.wall_time_sec < comparison.baseline.wall_time_sec
    ]
    worsened_wall_time = [
        comparison.name
        for comparison in comparisons
        if comparison.warm_start.wall_time_sec > comparison.baseline.wall_time_sec
    ]
    objective_changed = [
        comparison.name
        for comparison in comparisons
        if comparison.warm_start.objective_value != comparison.baseline.objective_value
    ]
    validation_violations = [
        comparison.name
        for comparison in comparisons
        if comparison.baseline.validation_violation_count > 0
        or comparison.warm_start.validation_violation_count > 0
    ]
    branch_reductions = [
        (
            comparison.name,
            comparison.baseline.num_branches - comparison.warm_start.num_branches,
        )
        for comparison in comparisons
    ]
    conflict_reductions = [
        (
            comparison.name,
            comparison.baseline.num_conflicts - comparison.warm_start.num_conflicts,
        )
        for comparison in comparisons
    ]
    best_branch_reduction = _largest_positive_reduction(branch_reductions)
    best_conflict_reduction = _largest_positive_reduction(conflict_reductions)
    return {
        "case_count": len(comparisons),
        "warm_start_improved_wall_time_cases": improved_wall_time,
        "warm_start_worsened_wall_time_cases": worsened_wall_time,
        "objective_changed_cases": objective_changed,
        "validation_violation_cases": validation_violations,
        "largest_branch_reduction": best_branch_reduction,
        "largest_conflict_reduction": best_conflict_reduction,
    }


def format_benchmark_summary(summary: Dict[str, object]) -> str:
    lines = ["Summary:"]
    for key in [
        "case_count",
        "fastest_case",
        "slowest_case",
        "largest_variable_count",
        "largest_variable_count_case",
        "largest_constraint_count",
        "largest_constraint_count_case",
        "optimal_cases",
        "feasible_cases",
        "non_success_cases",
        "has_validation_violations",
        "total_shortage",
    ]:
        lines.append(f"  {key}: {_format_summary_value(summary[key])}")
    return "\n".join(lines)


def format_comparison_summary(summary: Dict[str, object]) -> str:
    lines = ["Summary:"]
    for key in [
        "case_count",
        "warm_start_improved_wall_time_cases",
        "warm_start_worsened_wall_time_cases",
        "objective_changed_cases",
        "validation_violation_cases",
        "largest_branch_reduction",
        "largest_conflict_reduction",
    ]:
        lines.append(f"  {key}: {_format_summary_value(summary[key])}")
    return "\n".join(lines)


def benchmark_results_payload(results: List[BenchmarkResult]) -> Dict[str, object]:
    return {
        "results": [asdict(result) for result in results],
        "summary": summarize_benchmark_results(results),
    }


def benchmark_comparisons_payload(
    comparisons: List[BenchmarkComparison],
) -> Dict[str, object]:
    return {
        "comparisons": [asdict(comparison) for comparison in comparisons],
        "summary": summarize_benchmark_comparisons(comparisons),
    }


def main() -> int:
    parser = ArgumentParser(
        description="Run deterministic workforce solver benchmark fixtures."
    )
    parser.add_argument(
        "--case",
        action="append",
        choices=_case_choices(),
        help="Benchmark case name. May be passed more than once.",
    )
    parser.add_argument(
        "--scaling",
        action="store_true",
        help="Run deterministic synthetic scaling benchmark cases.",
    )
    parser.add_argument(
        "--no-warm-start",
        action="store_true",
        help="Run benchmark fixtures without generated warm-start hints.",
    )
    parser.add_argument(
        "--compare-warm-start",
        action="store_true",
        help="Run each fixture without and with warm-start hints.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print benchmark output as JSON.",
    )
    args = parser.parse_args()

    cases = scaling_benchmark_cases() if args.scaling else benchmark_cases()
    if args.case:
        case_map = _case_map()
        cases = [case_map[name] for name in args.case]

    if args.compare_warm_start:
        comparisons = run_benchmark_comparisons(cases)
        if args.json:
            print(json.dumps(benchmark_comparisons_payload(comparisons), indent=2))
        else:
            print(format_benchmark_comparisons(comparisons))
        return 0 if all(
            comparison.baseline.status in ("OPTIMAL", "FEASIBLE")
            and comparison.warm_start.status in ("OPTIMAL", "FEASIBLE")
            for comparison in comparisons
        ) else 1

    results = run_benchmarks(cases, warm_start=not args.no_warm_start)
    if args.json:
        print(json.dumps(benchmark_results_payload(results), indent=2))
    else:
        print(format_benchmark_results(results))
    return (
        0
        if all(result.status in ("OPTIMAL", "FEASIBLE") for result in results)
        else 1
    )


def _result_from_solve(
    name: str,
    data: ProblemData,
    result: SolveResult,
    *,
    warm_start_enabled: bool,
    hint_count: int,
) -> BenchmarkResult:
    breakdown = result.objective_breakdown
    return BenchmarkResult(
        name=name,
        employee_count=len(data.employees),
        day_count=len(data.days),
        shift_count=len(data.shifts),
        total_demand=_total_demand(data),
        warm_start_enabled=warm_start_enabled,
        hint_count=hint_count,
        status=result.metrics.status,
        objective_value=result.metrics.objective_value,
        best_bound=result.metrics.best_bound,
        absolute_optimality_gap=_absolute_optimality_gap(
            result.metrics.objective_value,
            result.metrics.best_bound,
        ),
        relative_optimality_gap_percent=_relative_optimality_gap_percent(
            result.metrics.objective_value,
            result.metrics.best_bound,
        ),
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


def _case_choices() -> List[str]:
    return sorted(_case_map())


def _case_map() -> Dict[str, BenchmarkCase]:
    cases: Dict[str, BenchmarkCase] = {}
    for case in [*benchmark_cases(), *scaling_benchmark_cases()]:
        cases[case.name] = case
    return cases


def _total_demand(data: ProblemData) -> int:
    return sum(
        data.demand[day][shift][role]
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
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
    return _format_number(objective_value)


def _format_number(value: float | None) -> str:
    if value is None:
        return "-"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}"


def _format_percent(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _format_summary_value(value: object) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "none"
    if isinstance(value, dict):
        if not value:
            return "none"
        return ", ".join(f"{key}={item}" for key, item in value.items())
    if value is None:
        return "none"
    return str(value)


def _absolute_optimality_gap(
    objective_value: float | None,
    best_bound: float | None,
) -> float | None:
    if objective_value is None or best_bound is None:
        return None
    return abs(objective_value - best_bound)


def _relative_optimality_gap_percent(
    objective_value: float | None,
    best_bound: float | None,
) -> float | None:
    if objective_value is None or best_bound is None:
        return None
    if objective_value == 0:
        return 0 if best_bound == 0 else None
    absolute_gap = _absolute_optimality_gap(objective_value, best_bound)
    return absolute_gap / abs(objective_value) * 100


def _largest_positive_reduction(
    reductions: List[tuple[str, int]],
) -> Dict[str, int | str] | None:
    positive_reductions = [
        (case_name, reduction)
        for case_name, reduction in reductions
        if reduction > 0
    ]
    if not positive_reductions:
        return None
    case_name, reduction = max(positive_reductions, key=lambda item: item[1])
    return {"case": case_name, "reduction": reduction}


if __name__ == "__main__":
    raise SystemExit(main())
