from __future__ import annotations

from dataclasses import replace
from itertools import product
from typing import Dict, List, Tuple
import json
import os
import subprocess
import sys

import pytest

from workforce_scheduling.benchmark import (
    BenchmarkResult,
    benchmark_comparisons_payload,
    benchmark_cases,
    benchmark_results_payload,
    format_benchmark_comparisons,
    format_benchmark_results,
    run_benchmark_case,
    run_benchmark_comparisons,
    run_benchmarks,
    scaling_benchmark_cases,
)
from workforce_scheduling.benchmark import (
    _absolute_optimality_gap,
    _relative_optimality_gap_percent,
)
from workforce_scheduling.data import Employee, ProblemData, generate_synthetic_data
from workforce_scheduling.solve import (
    Assignment,
    ObjectiveBreakdown,
    SolveResult,
    compute_objective_breakdown,
    solve,
    validate_solution,
)
from workforce_scheduling.warm_start import (
    build_warm_start_hints,
    with_warm_start_hints,
    without_hints,
)


REQUIRED_BENCHMARK_RESULT_FIELDS = {
    "name",
    "employee_count",
    "day_count",
    "shift_count",
    "total_demand",
    "warm_start_enabled",
    "hint_count",
    "status",
    "objective_value",
    "best_bound",
    "absolute_optimality_gap",
    "relative_optimality_gap_percent",
    "wall_time_sec",
    "num_conflicts",
    "num_branches",
    "num_variables",
    "num_constraints",
    "assignment_count",
    "total_shortage",
    "fairness_objective_value",
    "labor_cost_value",
    "total_objective_value",
    "validation_violation_count",
}
COMPLETE_BENCHMARK_RESULT_FIELDS = set(BenchmarkResult.__dataclass_fields__)


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
    num_days = len(demand)
    return ProblemData(
        employees=employees,
        roles=roles,
        days=list(range(num_days)),
        shifts=[f"shift_{idx}" for idx in range(len(shift_start_hours))],
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        min_rest_hours=min_rest_hours,
        max_consecutive_days=max_consecutive_days,
        shortage_penalty=shortage_penalty,
        demand=demand,
        hint_assignments={},
    )


def _assignment_tuples(assignments: List[Assignment]) -> List[Tuple[int, int, int, str]]:
    return sorted(
        (a.employee_id, a.day, a.shift, a.role)
        for a in assignments
    )


def _small_fully_feasible_problem() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(2, 1, roles, default=1)
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


def _constrained_rest_window_problem() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(2, 2, roles)
    demand[0][1]["worker"] = 1
    demand[1][0]["worker"] = 1
    employee = _make_employee(0, roles, [[True, True], [True, True]])
    return _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8, 20],
        shift_end_hours=[16, 4],
        demand=demand,
        min_rest_hours=10,
        max_consecutive_days=5,
    )


def _unavoidable_understaffing_problem() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=2)
    employee = _make_employee(0, roles, [[True]])
    return _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )


def _expensive_worker_shortage_priority_problem() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]], hourly_cost=10_000)
    return _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1,
    )


def _fairness_vs_cost_problem() -> ProblemData:
    roles = ["worker"]
    demand = _build_demand(2, 1, roles, default=1)
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


def _assert_objective_total_matches_components(
    breakdown: ObjectiveBreakdown,
) -> None:
    assert breakdown.total_objective_value == (
        breakdown.shortage_objective_value
        + breakdown.fairness_objective_value
        + breakdown.labor_cost_value
    )


def _raw_fairness_value(breakdown: ObjectiveBreakdown) -> int:
    return (
        breakdown.workload_fairness_value
        + breakdown.weekend_fairness_value
        + breakdown.shift_distribution_fairness_value
    )


def _stable_benchmark_tuple(result: BenchmarkResult) -> Tuple[object, ...]:
    return (
        result.name,
        result.employee_count,
        result.day_count,
        result.shift_count,
        result.total_demand,
        result.warm_start_enabled,
        result.hint_count,
        result.status,
        result.objective_value,
        result.best_bound,
        result.absolute_optimality_gap,
        result.relative_optimality_gap_percent,
        result.num_conflicts,
        result.num_branches,
        result.num_variables,
        result.num_constraints,
        result.assignment_count,
        result.total_shortage,
        result.shortage_objective_value,
        result.workload_fairness_value,
        result.weekend_fairness_value,
        result.shift_distribution_fairness_value,
        result.fairness_objective_value,
        result.labor_cost_value,
        result.total_objective_value,
        result.validation_violation_count,
    )


def _assert_benchmark_result_payload_valid(result: Dict[str, object]) -> None:
    assert REQUIRED_BENCHMARK_RESULT_FIELDS <= result.keys()
    assert COMPLETE_BENCHMARK_RESULT_FIELDS <= result.keys()
    for gap_field in (
        "absolute_optimality_gap",
        "relative_optimality_gap_percent",
    ):
        gap_value = result[gap_field]
        if gap_value is not None:
            assert gap_value >= 0


def _assert_benchmark_results_payload_valid(payload: Dict[str, object]) -> None:
    assert "results" in payload
    assert "summary" in payload

    results = payload["results"]
    summary = payload["summary"]
    assert isinstance(results, list)
    assert isinstance(summary, dict)
    assert summary["case_count"] == len(results)

    success_statuses = {"OPTIMAL", "FEASIBLE"}
    for result in results:
        assert isinstance(result, dict)
        _assert_benchmark_result_payload_valid(result)

    assert summary["total_shortage"] == sum(
        result["total_shortage"] for result in results
    )
    assert summary["has_validation_violations"] == any(
        result["validation_violation_count"] > 0 for result in results
    )
    assert summary["optimal_cases"] == sum(
        1 for result in results if result["status"] == "OPTIMAL"
    )
    assert summary["feasible_cases"] == sum(
        1 for result in results if result["status"] == "FEASIBLE"
    )
    assert summary["non_success_cases"] == sum(
        1 for result in results if result["status"] not in success_statuses
    )


def _assert_benchmark_comparisons_payload_valid(payload: Dict[str, object]) -> None:
    assert "comparisons" in payload
    assert "summary" in payload

    comparisons = payload["comparisons"]
    summary = payload["summary"]
    assert isinstance(comparisons, list)
    assert isinstance(summary, dict)
    assert summary["case_count"] == len(comparisons)

    objective_changed_cases = []
    validation_violation_cases = []
    for comparison in comparisons:
        assert isinstance(comparison, dict)
        assert {"name", "baseline", "warm_start"} <= comparison.keys()
        baseline = comparison["baseline"]
        warm_start = comparison["warm_start"]
        assert isinstance(baseline, dict)
        assert isinstance(warm_start, dict)
        _assert_benchmark_result_payload_valid(baseline)
        _assert_benchmark_result_payload_valid(warm_start)
        assert "hint_count" in warm_start
        assert "validation_violation_count" in baseline
        assert "validation_violation_count" in warm_start
        assert baseline["name"] == comparison["name"]
        assert warm_start["name"] == comparison["name"]
        assert not baseline["warm_start_enabled"]
        assert warm_start["warm_start_enabled"]

        if baseline["objective_value"] != warm_start["objective_value"]:
            objective_changed_cases.append(comparison["name"])
        if (
            baseline["validation_violation_count"] > 0
            or warm_start["validation_violation_count"] > 0
        ):
            validation_violation_cases.append(comparison["name"])

    assert summary["objective_changed_cases"] == objective_changed_cases
    assert summary["validation_violation_cases"] == validation_violation_cases


def _run_benchmark_cli_json(*args: str) -> Dict[str, object]:
    env = os.environ.copy()
    cwd = os.getcwd()
    current_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        cwd
        if not current_pythonpath
        else os.pathsep.join([cwd, current_pythonpath])
    )
    completed = subprocess.run(
        [sys.executable, "-m", "workforce_scheduling.benchmark", *args],
        check=True,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _assert_objective_breakdown_consistent(result: SolveResult) -> None:
    breakdown = result.objective_breakdown
    assert breakdown.total_shortage == sum(result.shortages.values())
    assert breakdown.fairness_objective_value == (
        _raw_fairness_value(breakdown)
        * result.objective_metadata["fairness_priority_weight"]
    )
    assert breakdown.shortage_objective_value == (
        breakdown.total_shortage
        * result.objective_metadata["shortage_priority_weight"]
    )
    _assert_objective_total_matches_components(breakdown)
    assert breakdown.total_objective_value == int(result.metrics.objective_value)


def _assert_diagnostics_consistent(
    data: ProblemData,
    result: SolveResult,
) -> None:
    employee_ids = {employee.employee_id for employee in data.employees}
    assignments_by_slot: Dict[Tuple[int, int, str], List[Assignment]] = {}
    for assignment in result.assignments:
        assignments_by_slot.setdefault(
            (assignment.day, assignment.shift, assignment.role),
            [],
        ).append(assignment)

    for diagnostic in result.demanded_slot_diagnostics:
        key = (diagnostic.day, diagnostic.shift, diagnostic.role)
        matching_assignments = assignments_by_slot.get(key, [])
        matching_employee_ids = sorted(
            assignment.employee_id for assignment in matching_assignments
        )
        assert diagnostic.assigned_count == len(matching_assignments)
        assert diagnostic.assigned_employee_ids == matching_employee_ids
        assert diagnostic.shortage_count == max(
            0,
            diagnostic.required_count - diagnostic.assigned_count,
        )
        assert diagnostic.candidate_employee_count == len(
            diagnostic.currently_assignable_employee_ids
        )
        assert diagnostic.could_work_employee_ids == diagnostic.role_available_employee_ids
        assert diagnostic.assigned_employee_ids == sorted(
            diagnostic.assigned_employee_ids
        )
        assert diagnostic.could_work_employee_ids == sorted(
            diagnostic.could_work_employee_ids
        )
        assert diagnostic.currently_assignable_employee_ids == sorted(
            diagnostic.currently_assignable_employee_ids
        )

        all_reported_ids = set(diagnostic.assigned_employee_ids)
        all_reported_ids.update(diagnostic.could_work_employee_ids)
        all_reported_ids.update(diagnostic.currently_assignable_employee_ids)
        for reason, blocked_employee_ids in (
            diagnostic.blocked_employee_ids_by_reason.items()
        ):
            assert blocked_employee_ids == sorted(blocked_employee_ids), reason
            all_reported_ids.update(blocked_employee_ids)
            assert not (
                set(diagnostic.assigned_employee_ids) & set(blocked_employee_ids)
            ), reason
        assert all_reported_ids <= employee_ids


def _brute_force_best_priority(
    data: ProblemData,
    objective_metadata: Dict[str, int],
) -> Tuple[Tuple[int, int, int], List[Assignment], ObjectiveBreakdown]:
    demanded_slots = [
        (day, shift, role)
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
        if data.demand[day][shift][role] > 0
    ]
    assert all(data.demand[day][shift][role] == 1 for day, shift, role in demanded_slots)

    choices = [None] + [employee.employee_id for employee in data.employees]
    best: Tuple[Tuple[int, int, int], List[Assignment], ObjectiveBreakdown] | None = None

    for assignment_choices in product(choices, repeat=len(demanded_slots)):
        assignments = [
            Assignment(
                employee_id=employee_id,
                day=day,
                shift=shift,
                role=role,
            )
            for (day, shift, role), employee_id in zip(
                demanded_slots,
                assignment_choices,
            )
            if employee_id is not None
        ]
        shortages = _shortages_for_assignments(data, assignments)
        if validate_solution(data, assignments, shortages):
            continue
        breakdown = compute_objective_breakdown(
            data,
            assignments,
            shortages,
            objective_metadata,
        )
        priority = (
            breakdown.total_shortage,
            _raw_fairness_value(breakdown),
            breakdown.labor_cost_value,
        )
        if best is None or priority < best[0]:
            best = (priority, assignments, breakdown)

    assert best is not None
    return best


def _shortages_for_assignments(
    data: ProblemData,
    assignments: List[Assignment],
) -> Dict[Tuple[int, int, str], int]:
    coverage: Dict[Tuple[int, int, str], int] = {}
    for assignment in assignments:
        key = (assignment.day, assignment.shift, assignment.role)
        coverage[key] = coverage.get(key, 0) + 1

    return {
        (day, shift, role): max(
            0,
            data.demand[day][shift][role] - coverage.get((day, shift, role), 0),
        )
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
    }


def test_deterministic_solve() -> None:
    data = generate_synthetic_data(seed=7)
    result_a = solve(data, time_limit_sec=5.0, seed=7)
    result_b = solve(data, time_limit_sec=5.0, seed=7)

    assert result_a.metrics.status in ("OPTIMAL", "FEASIBLE")
    assert result_b.metrics.status in ("OPTIMAL", "FEASIBLE")
    assert _assignment_tuples(result_a.assignments) == _assignment_tuples(
        result_b.assignments
    )
    assert result_a.shortages == result_b.shortages
    assert result_a.metrics.objective_value == result_b.metrics.objective_value
    assert result_a.objective_breakdown == result_b.objective_breakdown
    assert not result_a.violations
    assert result_a.objective_breakdown.total_shortage == 0
    assert result_a.objective_breakdown.total_objective_value == int(
        result_a.metrics.objective_value
    )
    assert result_a.objective_breakdown.total_objective_value == (
        result_a.objective_breakdown.shortage_objective_value
        + result_a.objective_breakdown.fairness_objective_value
        + result_a.objective_breakdown.labor_cost_value
    )
    _assert_objective_breakdown_consistent(result_a)
    _assert_diagnostics_consistent(data, result_a)


def test_gitignore_excludes_python_test_artifacts() -> None:
    with open(".gitignore") as handle:
        patterns = set(handle.read().splitlines())

    assert {
        "__pycache__/",
        "*.pyc",
        ".pytest_cache/",
        ".venv/",
        "venv/",
    }.issubset(patterns)


def test_warm_start_hints_are_deterministic_and_valid() -> None:
    data = _constrained_rest_window_problem()
    hints_a = build_warm_start_hints(data)
    hints_b = build_warm_start_hints(data)
    hinted_assignments = [
        Assignment(employee_id=employee_id, day=day, shift=shift, role=role)
        for employee_id, day, shift, role in hints_a
        if hints_a[(employee_id, day, shift, role)] == 1
    ]

    assert hints_a == hints_b
    assert len(hints_a) == 1
    assert not validate_solution(
        data,
        hinted_assignments,
        _shortages_for_assignments(data, hinted_assignments),
    )


def test_warm_start_preserves_existing_hints_without_unioning_rosters() -> None:
    data = _small_fully_feasible_problem()
    data.hint_assignments = {(0, 0, 0, "worker"): 1}

    hinted = with_warm_start_hints(data)

    assert hinted.hint_assignments == data.hint_assignments
    assert hinted is not data


def test_warm_start_hints_do_not_change_optimal_objective_priority() -> None:
    for data in (
        _small_fully_feasible_problem(),
        _constrained_rest_window_problem(),
        _unavoidable_understaffing_problem(),
        _fairness_vs_cost_problem(),
    ):
        baseline = solve(without_hints(data), time_limit_sec=5.0, seed=1)
        hinted = solve(
            with_warm_start_hints(data, preserve_existing=False),
            time_limit_sec=5.0,
            seed=1,
        )

        assert baseline.metrics.status == "OPTIMAL"
        assert hinted.metrics.status == "OPTIMAL"
        assert baseline.objective_breakdown == hinted.objective_breakdown
        assert baseline.shortages == hinted.shortages
        assert not baseline.violations
        assert not hinted.violations


def test_benchmark_cases_include_required_scenarios() -> None:
    names = {case.name for case in benchmark_cases()}

    assert {
        "small_fully_feasible",
        "temporal_rest_constrained",
        "unavoidable_understaffing",
        "fairness_vs_cost",
        "synthetic_40_employee_weekly",
    } == names


def test_scaling_benchmark_cases_include_required_sizes() -> None:
    cases = scaling_benchmark_cases()
    names = {case.name for case in cases}

    assert {
        "synthetic_20_employee_weekly",
        "synthetic_40_employee_weekly",
        "synthetic_80_employee_weekly",
        "synthetic_120_employee_weekly",
    } == names


def test_benchmark_runner_reports_solver_and_objective_baselines() -> None:
    expected_shortages = {
        "small_fully_feasible": 0,
        "temporal_rest_constrained": 1,
        "unavoidable_understaffing": 1,
        "fairness_vs_cost": 0,
    }
    cases = [
        case
        for case in benchmark_cases()
        if case.name in expected_shortages
    ]

    results = run_benchmarks(cases)

    assert {result.name for result in results} == set(expected_shortages)
    for result in results:
        assert result.status == "OPTIMAL"
        assert result.employee_count > 0
        assert result.total_demand > 0
        assert result.warm_start_enabled
        assert result.hint_count > 0
        assert result.best_bound is not None
        assert result.absolute_optimality_gap is not None
        assert result.relative_optimality_gap_percent is not None
        assert result.validation_violation_count == 0
        assert result.num_variables > 0
        assert result.num_constraints > 0
        assert result.total_shortage == expected_shortages[result.name]
        assert result.total_objective_value == int(result.objective_value)
        assert result.total_objective_value == (
            result.shortage_objective_value
            + result.fairness_objective_value
            + result.labor_cost_value
        )


def test_benchmark_runner_is_deterministic_for_small_cases() -> None:
    cases = [
        case
        for case in benchmark_cases()
        if case.name != "synthetic_40_employee_weekly"
    ]
    first_run = run_benchmarks(cases)
    second_run = run_benchmarks(cases)

    assert [_stable_benchmark_tuple(result) for result in first_run] == [
        _stable_benchmark_tuple(result) for result in second_run
    ]


def test_synthetic_40_employee_weekly_benchmark_fixture_solves() -> None:
    case = next(
        case
        for case in benchmark_cases()
        if case.name == "synthetic_40_employee_weekly"
    )
    result = run_benchmark_case(replace(case, time_limit_sec=1.0))

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.validation_violation_count == 0
    assert result.total_shortage == 0
    assert result.num_variables > 0
    assert result.num_constraints > 0


def test_synthetic_20_employee_scaling_fixture_solves_without_large_manual_cases() -> None:
    case = next(
        case
        for case in scaling_benchmark_cases()
        if case.name == "synthetic_20_employee_weekly"
    )
    result = run_benchmark_case(case)

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.employee_count == 20
    assert result.day_count == 7
    assert result.shift_count == 3
    assert result.total_demand == 63
    assert result.validation_violation_count == 0
    assert result.total_shortage == 0
    assert result.num_variables > 0
    assert result.num_constraints > 0


def test_benchmark_gap_metrics_handle_solved_and_missing_objectives() -> None:
    result = run_benchmark_case(
        next(case for case in benchmark_cases() if case.name == "small_fully_feasible")
    )

    assert result.absolute_optimality_gap == 0
    assert result.relative_optimality_gap_percent == 0
    assert _absolute_optimality_gap(None, 10.0) is None
    assert _absolute_optimality_gap(10.0, None) is None
    assert _relative_optimality_gap_percent(None, 10.0) is None
    assert _relative_optimality_gap_percent(0.0, 0.0) is None
    assert _relative_optimality_gap_percent(100.0, 90.0) == 10.0


def test_benchmark_results_format_is_stable_and_readable() -> None:
    case = next(
        case for case in benchmark_cases() if case.name == "small_fully_feasible"
    )
    output = format_benchmark_results([run_benchmark_case(case)])

    assert "case" in output
    assert "employees" in output
    assert "demand" in output
    assert "best_bound" in output
    assert "gap_abs" in output
    assert "gap_pct" in output
    assert "status" in output
    assert "warm_start" in output
    assert "shortage" in output
    assert "small_fully_feasible" in output
    assert "Summary:" in output
    assert "case_count" in output


def test_benchmark_comparison_reports_unhinted_and_warm_started_runs() -> None:
    cases = [
        case
        for case in benchmark_cases()
        if case.name != "synthetic_40_employee_weekly"
    ]
    comparisons = run_benchmark_comparisons(cases)
    output = format_benchmark_comparisons(comparisons)

    assert "base_status" in output
    assert "warm_status" in output
    assert "base_wall" in output
    assert "warm_wall" in output
    assert "base_bound" in output
    assert "warm_bound" in output
    assert "base_gap_pct" in output
    assert "warm_gap_pct" in output
    assert "base_shortage" in output
    assert "warm_shortage" in output
    assert "base_violations" in output
    assert "warm_violations" in output
    assert "employees" in output
    assert "demand" in output
    assert "Summary:" in output
    assert len(comparisons) == len(cases)
    for comparison in comparisons:
        assert not comparison.baseline.warm_start_enabled
        assert comparison.baseline.hint_count == 0
        assert comparison.warm_start.warm_start_enabled
        assert comparison.warm_start.hint_count > 0
        assert comparison.baseline.status == "OPTIMAL"
        assert comparison.warm_start.status == "OPTIMAL"
        assert (
            comparison.baseline.objective_value
            == comparison.warm_start.objective_value
        )


def test_benchmark_json_payloads_include_results_and_summaries() -> None:
    case = next(
        case for case in benchmark_cases() if case.name == "small_fully_feasible"
    )
    results_payload = benchmark_results_payload([run_benchmark_case(case)])
    comparisons_payload = benchmark_comparisons_payload(
        [run_benchmark_comparisons([case])[0]]
    )

    encoded_results = json.loads(json.dumps(results_payload))
    encoded_comparisons = json.loads(json.dumps(comparisons_payload))

    _assert_benchmark_results_payload_valid(encoded_results)
    _assert_benchmark_comparisons_payload_valid(encoded_comparisons)
    assert encoded_results["results"][0]["name"] == "small_fully_feasible"
    assert "relative_optimality_gap_percent" in encoded_results["results"][0]
    assert encoded_results["summary"]["case_count"] == 1
    assert encoded_comparisons["comparisons"][0]["name"] == "small_fully_feasible"
    assert "largest_branch_reduction" in encoded_comparisons["summary"]


def test_benchmark_cli_json_output_is_complete_and_consistent() -> None:
    payload = _run_benchmark_cli_json(
        "--case",
        "small_fully_feasible",
        "--json",
    )

    _assert_benchmark_results_payload_valid(payload)
    assert payload["results"][0]["name"] == "small_fully_feasible"


def test_benchmark_cli_comparison_json_output_is_complete_and_consistent() -> None:
    payload = _run_benchmark_cli_json(
        "--case",
        "small_fully_feasible",
        "--compare-warm-start",
        "--json",
    )

    _assert_benchmark_comparisons_payload_valid(payload)
    assert payload["comparisons"][0]["name"] == "small_fully_feasible"


def test_shift_length_hours_is_not_part_of_problem_data() -> None:
    data = generate_synthetic_data(seed=3)

    assert not hasattr(data, "shift_length_hours")


def test_minimum_rest_enforced() -> None:
    data = _constrained_rest_window_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 1
    assert sum(result.shortages.values()) == 1
    assert not result.violations


def test_closing_to_opening_enforced() -> None:
    roles = ["worker"]
    shift_start_hours = [6, 14]
    shift_end_hours = [14, 22]
    demand = _build_demand(2, 2, roles)
    demand[0][1]["worker"] = 1
    demand[1][0]["worker"] = 1

    availability = [[True, True], [True, True]]
    employee = _make_employee(0, roles, availability)

    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 1
    assert sum(result.shortages.values()) == 1
    assert not result.violations


def test_max_consecutive_days_enforced() -> None:
    roles = ["worker"]
    shift_start_hours = [8]
    shift_end_hours = [16]
    demand = _build_demand(3, 1, roles, default=1)

    availability = [[True], [True], [True]]
    employee = _make_employee(0, roles, availability)

    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=2,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 2
    assert sum(result.shortages.values()) == 1
    assert not result.violations


def test_soft_understaffing_behavior() -> None:
    data = _unavoidable_understaffing_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert result.shortages[(0, 0, "worker")] == 1
    assert result.objective_breakdown.total_shortage == 1
    assert not result.violations


def test_shortage_is_prioritized_over_labor_cost() -> None:
    data = _expensive_worker_shortage_priority_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 1
    assert result.shortages[(0, 0, "worker")] == 0
    assert result.constraint_metadata["shortage_priority_weight"] > 80_000
    assert result.objective_breakdown.total_shortage == 0
    assert result.objective_breakdown.labor_cost_value == 80_000
    assert not result.violations


def test_shortage_is_prioritized_over_fairness() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employees = [
        _make_employee(0, roles, [[True]]),
        _make_employee(1, roles, [[True]]),
        _make_employee(2, roles, [[True]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 1
    assert result.shortages[(0, 0, "worker")] == 0
    assert result.fairness_metrics.workload_spread == 8
    assert not result.violations


def test_workload_fairness_spreads_hours_when_alternatives_exist() -> None:
    data = _fairness_vs_cost_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert sum(result.shortages.values()) == 0
    assert result.fairness_metrics.assigned_hours_per_employee == {0: 8, 1: 8}
    assert result.fairness_metrics.workload_spread == 0
    assert result.objective_breakdown.workload_fairness_value == 0
    assert not result.violations


def test_fairness_dominates_labor_cost_when_full_coverage_options_exist() -> None:
    result = solve(_fairness_vs_cost_problem(), time_limit_sec=5.0, seed=1)

    assigned_employee_ids = {assignment.employee_id for assignment in result.assignments}
    assert assigned_employee_ids == {0, 1}
    assert result.objective_breakdown.total_shortage == 0
    assert result.objective_breakdown.workload_fairness_value == 0
    assert result.objective_breakdown.labor_cost_value == 8008
    assert not result.violations


def test_labor_cost_breaks_ties_when_shortage_and_fairness_are_equal() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employees = [
        _make_employee(0, roles, [[True]], hourly_cost=1),
        _make_employee(1, roles, [[True]], hourly_cost=100),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert _assignment_tuples(result.assignments) == [(0, 0, 0, "worker")]
    assert result.objective_breakdown.total_shortage == 0
    assert result.objective_breakdown.workload_fairness_value == 8
    assert result.objective_breakdown.shift_distribution_fairness_value == 1
    assert result.objective_breakdown.labor_cost_value == 8
    assert not result.violations


def test_tiny_brute_force_oracle_matches_cp_sat_priority() -> None:
    data = _fairness_vs_cost_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)
    oracle_priority, oracle_assignments, oracle_breakdown = _brute_force_best_priority(
        data,
        result.objective_metadata,
    )

    result_priority = (
        result.objective_breakdown.total_shortage,
        _raw_fairness_value(result.objective_breakdown),
        result.objective_breakdown.labor_cost_value,
    )
    assert result_priority == oracle_priority
    assert _assignment_tuples(result.assignments) == _assignment_tuples(
        oracle_assignments
    )
    assert result.objective_breakdown.total_shortage == oracle_breakdown.total_shortage
    assert _raw_fairness_value(result.objective_breakdown) == _raw_fairness_value(
        oracle_breakdown
    )
    assert result.objective_breakdown.labor_cost_value == oracle_breakdown.labor_cost_value


def test_weekend_fairness_spreads_weekend_assignments_when_possible() -> None:
    roles = ["worker"]
    demand = _build_demand(7, 1, roles)
    demand[5][0]["worker"] = 1
    demand[6][0]["worker"] = 1
    employees = [
        _make_employee(0, roles, [[True] for _ in range(7)]),
        _make_employee(1, roles, [[True] for _ in range(7)]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert sum(result.shortages.values()) == 0
    assert result.fairness_metrics.weekend_assignments_per_employee == {0: 1, 1: 1}
    assert not result.violations


def test_shift_distribution_fairness_spreads_repeated_shift_types() -> None:
    roles = ["worker"]
    demand = _build_demand(4, 2, roles)
    demand[0][0]["worker"] = 1
    demand[1][1]["worker"] = 1
    demand[2][0]["worker"] = 1
    demand[3][1]["worker"] = 1
    employees = [
        _make_employee(0, roles, [[True, True] for _ in range(4)]),
        _make_employee(1, roles, [[True, True] for _ in range(4)]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8, 14],
        shift_end_hours=[12, 18],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert sum(result.shortages.values()) == 0
    assert result.fairness_metrics.shift_counts_per_employee_shift[(0, 0)] == 1
    assert result.fairness_metrics.shift_counts_per_employee_shift[(0, 1)] == 1
    assert result.fairness_metrics.shift_counts_per_employee_shift[(1, 0)] == 1
    assert result.fairness_metrics.shift_counts_per_employee_shift[(1, 1)] == 1
    assert not result.violations


def test_fairness_metrics_match_assignments() -> None:
    roles = ["worker"]
    demand = _build_demand(7, 2, roles)
    demand[0][0]["worker"] = 1
    demand[5][1]["worker"] = 1
    employees = [
        _make_employee(0, roles, [[True, True] for _ in range(7)]),
        _make_employee(1, roles, [[True, True] for _ in range(7)]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8, 20],
        shift_end_hours=[16, 4],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assigned_hours = {0: 0, 1: 0}
    weekend_counts = {0: 0, 1: 0}
    shift_counts = {
        (employee_id, shift): 0
        for employee_id in assigned_hours
        for shift in range(2)
    }
    for assignment in result.assignments:
        assigned_hours[assignment.employee_id] += 8
        if assignment.day in (5, 6):
            weekend_counts[assignment.employee_id] += 1
        shift_counts[(assignment.employee_id, assignment.shift)] += 1

    assert result.fairness_metrics.assigned_hours_per_employee == assigned_hours
    assert result.fairness_metrics.weekend_assignments_per_employee == weekend_counts
    assert result.fairness_metrics.shift_counts_per_employee_shift == shift_counts
    assert result.fairness_metrics.workload_spread == (
        max(assigned_hours.values()) - min(assigned_hours.values())
    )
    assert not result.violations


def test_objective_breakdown_matches_solved_assignments() -> None:
    result = solve(_small_fully_feasible_problem(), time_limit_sec=5.0, seed=1)
    breakdown = result.objective_breakdown

    assert breakdown.total_shortage == sum(result.shortages.values())
    assert breakdown.workload_fairness_value == result.fairness_metrics.workload_spread
    assert breakdown.weekend_fairness_value == 0
    assert breakdown.labor_cost_value == sum(
        20 * 8 for _assignment in result.assignments
    )
    assert breakdown.total_objective_value == int(result.metrics.objective_value)
    _assert_objective_total_matches_components(breakdown)
    assert not result.violations


def test_no_availability_employee_does_not_distort_objective_fairness() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employees = [
        _make_employee(0, roles, [[True]]),
        _make_employee(1, roles, [[False]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert _assignment_tuples(result.assignments) == [(0, 0, 0, "worker")]
    assert result.fairness_metrics.assigned_hours_per_employee == {0: 8, 1: 0}
    assert result.objective_breakdown.workload_fairness_value == 0
    assert result.objective_breakdown.shift_distribution_fairness_value == 0
    assert not result.violations


def test_zero_feasible_assignment_employees_are_handled_deterministically() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employees = [
        _make_employee(0, roles, [[True]]),
        _make_employee(1, roles, [[False]]),
        _make_employee(2, roles, [[False]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result_a = solve(data, time_limit_sec=5.0, seed=1)
    result_b = solve(data, time_limit_sec=5.0, seed=1)

    assert _assignment_tuples(result_a.assignments) == [(0, 0, 0, "worker")]
    assert _assignment_tuples(result_a.assignments) == _assignment_tuples(
        result_b.assignments
    )
    assert result_a.objective_breakdown == result_b.objective_breakdown
    assert not result_a.violations


def test_weekend_fairness_is_zero_when_weekend_days_do_not_exist() -> None:
    result = solve(_small_fully_feasible_problem(), time_limit_sec=5.0, seed=1)

    assert result.objective_metadata["weekend_fairness_component_upper_bound"] == 0
    assert result.objective_breakdown.weekend_fairness_value == 0
    assert not result.violations


def test_shift_distribution_fairness_with_one_shift_is_still_well_defined() -> None:
    result = solve(_small_fully_feasible_problem(), time_limit_sec=5.0, seed=1)

    assert result.objective_breakdown.shift_distribution_fairness_value == 0
    assert result.fairness_metrics.shift_counts_per_employee_shift == {
        (0, 0): 1,
        (1, 0): 1,
    }
    assert not result.violations


def test_variable_shift_durations_drive_weekly_hours() -> None:
    roles = ["worker"]
    shift_start_hours = [8, 8]
    shift_end_hours = [12, 18]
    demand = _build_demand(2, 2, roles)
    demand[0][0]["worker"] = 1
    demand[1][1]["worker"] = 1

    employee = _make_employee(
        0,
        roles,
        [[True, False], [False, True]],
        max_weekly_hours=12,
    )
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignments) == 1
    assert sum(result.shortages.values()) == 1
    assert result.constraint_metadata["shift_duration_hours_min"] == 4
    assert result.constraint_metadata["shift_duration_hours_max"] == 10
    assert not result.violations


def test_problem_data_validation_rejects_bad_temporal_shape() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True, True]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    with pytest.raises(ValueError) as exc_info:
        solve(data, time_limit_sec=5.0, seed=1)

    assert "availability day 0 must have one value per shift" in str(exc_info.value)


def test_problem_data_validation_rejects_zero_max_consecutive_days() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=0,
    )

    with pytest.raises(ValueError) as exc_info:
        solve(data, time_limit_sec=5.0, seed=1)

    assert "max_consecutive_days must be at least 1" in str(exc_info.value)


def test_constraint_metadata_includes_explainability_records() -> None:
    roles = ["worker"]
    shift_start_hours = [8, 20]
    shift_end_hours = [16, 4]
    demand = _build_demand(2, 2, roles)
    demand[0][1]["worker"] = 1
    demand[1][0]["worker"] = 1
    employees = [
        _make_employee(0, roles, [[True, True], [True, True]]),
        _make_employee(1, roles, [[True, True], [True, True]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        demand=demand,
        min_rest_hours=10,
        max_consecutive_days=1,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    families = {record.family for record in result.constraint_records}
    assert {
        "one_shift_per_day",
        "weekly_hours",
        "minimum_rest",
        "max_consecutive_days",
        "staffing_coverage_soft",
    }.issubset(families)
    assert any(
        record.employee_id == 0 and record.description
        for record in result.constraint_records
        if record.family == "minimum_rest"
    )
    assert any(
        record.day == 0 and record.shift == 1 and record.role == "worker"
        for record in result.constraint_records
        if record.family == "staffing_coverage_soft"
    )
    assert result.constraint_metadata["fairness_priority_weight"] > 0
    assert result.constraint_metadata["labor_cost_component_upper_bound"] > 0
    assert result.constraint_metadata["workload_fairness_component_upper_bound"] > 0
    assert result.constraint_metadata["weekend_fairness_component_upper_bound"] == 0
    assert (
        result.constraint_metadata[
            "shift_distribution_fairness_component_upper_bound"
        ]
        > 0
    )
    assert {
        "workload_fairness_component",
        "shift_distribution_fairness_component",
    }.issubset(families)


def test_validator_catches_invalid_schedule() -> None:
    roles = ["worker"]
    shift_start_hours = [8]
    shift_end_hours = [16]
    demand = _build_demand(1, 1, roles, default=1)

    availability = [[False]]
    employee = _make_employee(0, roles, availability)
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=shift_start_hours,
        shift_end_hours=shift_end_hours,
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    invalid_assignment = [Assignment(employee_id=0, day=0, shift=0, role="worker")]
    errors = validate_solution(data, invalid_assignment, {(0, 0, "worker"): 0})
    assert any("unavailable" in error for error in errors)

    errors = validate_solution(data, [], {(0, 0, "worker"): 0})
    assert any("Shortage mismatch" in error for error in errors)


def test_validator_handles_unknown_employee_without_crashing() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    errors = validate_solution(
        data,
        [Assignment(employee_id=99, day=0, shift=0, role="worker")],
        {(0, 0, "worker"): 1},
    )

    assert any("Unknown employee_id 99" in error for error in errors)


def test_validator_handles_unknown_day_shift_role_without_crashing() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    errors = validate_solution(
        data,
        [
            Assignment(employee_id=0, day=5, shift=0, role="worker"),
            Assignment(employee_id=0, day=0, shift=3, role="worker"),
            Assignment(employee_id=0, day=0, shift=0, role="manager"),
        ],
        {(0, 0, "worker"): 1},
    )

    assert any("Unknown day 5" in error for error in errors)
    assert any("Unknown shift 3" in error for error in errors)
    assert any("Unknown role manager" in error for error in errors)


def test_validator_handles_assignment_outside_availability_matrix() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    errors = validate_solution(
        data,
        [Assignment(employee_id=0, day=0, shift=0, role="worker")],
        {(0, 0, "worker"): 0},
    )

    assert any("outside availability matrix" in error for error in errors)


def test_validator_rejects_invalid_shortage_keys_and_values() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]])
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )

    errors = validate_solution(
        data,
        [],
        {
            (0, 0, "worker"): -1,
            (9, 0, "worker"): 0,
        },
    )

    assert any("Invalid shortage key (9, 0, 'worker')" in error for error in errors)
    assert any(
        "Shortage below zero for key (0, 0, 'worker')" in error
        for error in errors
    )


def test_shortage_diagnostics_reports_correct_shortage_slot() -> None:
    roles = ["worker", "manager"]
    demand = _build_demand(1, 1, roles)
    demand[0][0]["worker"] = 2
    employees = [
        _make_employee(0, ["worker"], [[True]]),
        _make_employee(1, ["worker"], [[False]]),
        _make_employee(2, ["manager"], [[True]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.shortage_diagnostics) == 1
    diagnostic = result.shortage_diagnostics[0]
    assert (diagnostic.day, diagnostic.shift, diagnostic.role) == (0, 0, "worker")
    assert diagnostic.required_count == 2
    assert diagnostic.assigned_count == 1
    assert diagnostic.shortage_count == 1
    assert diagnostic.assigned_employee_ids == [0]
    assert diagnostic.candidate_employee_count == 1
    assert diagnostic.role_available_employee_ids == [0]
    assert diagnostic.currently_assignable_employee_ids == [0]
    _assert_diagnostics_consistent(data, result)


def test_shortage_diagnostics_identifies_unavailable_and_missing_role() -> None:
    roles = ["worker", "manager"]
    demand = _build_demand(1, 1, roles)
    demand[0][0]["worker"] = 2
    employees = [
        _make_employee(0, ["worker"], [[True]]),
        _make_employee(1, ["worker"], [[False]]),
        _make_employee(2, ["manager"], [[True]]),
    ]
    data = _make_problem(
        employees=employees,
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    diagnostic = solve(data, time_limit_sec=5.0, seed=1).shortage_diagnostics[0]

    assert diagnostic.blocked_employee_ids_by_reason["unavailable"] == [1]
    assert diagnostic.blocked_employee_ids_by_reason["missing_role"] == [2]
    assert diagnostic.could_work_employee_ids == [0]
    assert diagnostic.role_available_employee_ids == [0]
    assert diagnostic.currently_assignable_employee_ids == [0]


def test_shortage_diagnostics_identifies_rest_window_blocker() -> None:
    data = _constrained_rest_window_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.shortage_diagnostics) == 1
    diagnostic = result.shortage_diagnostics[0]
    assert diagnostic.blocked_employee_ids_by_reason["violates_minimum_rest"] == [0]


def test_shortage_diagnostics_identifies_weekly_hour_blocker() -> None:
    roles = ["worker"]
    demand = _build_demand(2, 1, roles, default=1)
    employee = _make_employee(
        0,
        roles,
        [[True], [True]],
        max_weekly_hours=8,
    )
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[8],
        shift_end_hours=[16],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.shortage_diagnostics) == 1
    diagnostic = result.shortage_diagnostics[0]
    assert diagnostic.blocked_employee_ids_by_reason["exceeds_weekly_hours"] == [0]


def test_demanded_slot_candidate_analysis_includes_full_coverage_slots() -> None:
    data = _small_fully_feasible_problem()
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.demanded_slot_diagnostics) == 2
    assert not result.shortage_diagnostics
    assert all(
        diagnostic.shortage_count == 0
        for diagnostic in result.demanded_slot_diagnostics
    )
    assert all(
        diagnostic.required_count == diagnostic.assigned_count
        for diagnostic in result.demanded_slot_diagnostics
    )
    _assert_diagnostics_consistent(data, result)


def test_diagnostics_are_consistent_and_deterministic() -> None:
    data = _constrained_rest_window_problem()
    result_a = solve(data, time_limit_sec=5.0, seed=1)
    result_b = solve(data, time_limit_sec=5.0, seed=1)

    _assert_diagnostics_consistent(data, result_a)
    assert result_a.demanded_slot_diagnostics == result_b.demanded_slot_diagnostics
    assert result_a.shortage_diagnostics == result_b.shortage_diagnostics


def test_assignment_explanations_calculate_duration_and_labor_cost() -> None:
    roles = ["worker"]
    demand = _build_demand(1, 1, roles, default=1)
    employee = _make_employee(0, roles, [[True]], hourly_cost=25)
    data = _make_problem(
        employees=[employee],
        roles=roles,
        shift_start_hours=[20],
        shift_end_hours=[4],
        demand=demand,
        min_rest_hours=8,
        max_consecutive_days=5,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)

    assert len(result.assignment_explanations) == 1
    explanation = result.assignment_explanations[0]
    assert explanation.employee_id == 0
    assert explanation.shift_duration == 8
    assert explanation.labor_cost_contribution == 200
    assert explanation.employee_weekly_hours == 8
    assert explanation.available
    assert explanation.qualified
    assert explanation.within_weekly_hours
    assert explanation.rest_compatible


def test_diagnostics_do_not_change_deterministic_solve_output() -> None:
    data = _fairness_vs_cost_problem()
    result_a = solve(data, time_limit_sec=5.0, seed=1)
    result_b = solve(data, time_limit_sec=5.0, seed=1)

    assert _assignment_tuples(result_a.assignments) == _assignment_tuples(
        result_b.assignments
    )
    assert result_a.shortages == result_b.shortages
    assert result_a.objective_breakdown == result_b.objective_breakdown
    assert result_a.shortage_diagnostics == result_b.shortage_diagnostics
    assert result_a.assignment_explanations == result_b.assignment_explanations


def test_objective_breakdown_remains_consistent_with_diagnostics_present() -> None:
    result = solve(_unavoidable_understaffing_problem(), time_limit_sec=5.0, seed=1)

    assert result.shortage_diagnostics
    assert result.objective_breakdown.total_shortage == sum(result.shortages.values())
    assert result.objective_breakdown.total_objective_value == int(
        result.metrics.objective_value
    )
    assert result.objective_breakdown.total_objective_value == (
        result.objective_breakdown.shortage_objective_value
        + result.objective_breakdown.fairness_objective_value
        + result.objective_breakdown.labor_cost_value
    )
