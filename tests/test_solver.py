from __future__ import annotations

from itertools import product
from typing import Dict, List, Tuple

import pytest

from workforce_scheduling.data import Employee, ProblemData, generate_synthetic_data
from workforce_scheduling.solve import (
    Assignment,
    ObjectiveBreakdown,
    SolveResult,
    compute_objective_breakdown,
    solve,
    validate_solution,
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
