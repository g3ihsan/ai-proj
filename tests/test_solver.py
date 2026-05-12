from __future__ import annotations

from typing import Dict, List, Tuple

from workforce_scheduling.data import Employee, ProblemData, generate_synthetic_data
from workforce_scheduling.solve import Assignment, solve, validate_solution


def _shift_length(shift_start_hours: List[int], shift_end_hours: List[int]) -> int:
    start = shift_start_hours[0]
    end = shift_end_hours[0]
    return end - start if end > start else end + 24 - start


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
    shift_length_hours = _shift_length(shift_start_hours, shift_end_hours)
    return ProblemData(
        employees=employees,
        roles=roles,
        days=list(range(num_days)),
        shifts=[f"shift_{idx}" for idx in range(len(shift_start_hours))],
        shift_length_hours=shift_length_hours,
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


def test_minimum_rest_enforced() -> None:
    roles = ["worker"]
    shift_start_hours = [8, 20]
    shift_end_hours = [16, 4]
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
        min_rest_hours=10,
        max_consecutive_days=5,
    )
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
    roles = ["worker"]
    shift_start_hours = [8]
    shift_end_hours = [16]
    demand = _build_demand(1, 1, roles, default=2)

    availability = [[True]]
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

    assert result.shortages[(0, 0, "worker")] == 1
    assert not result.violations


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
