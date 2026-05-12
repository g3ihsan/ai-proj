from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Tuple

from .data import (
    Employee,
    ProblemData,
    opening_closing_shift_indices,
    shift_duration_hours,
    shift_start_end,
)


HintAssignments = Dict[Tuple[int, int, int, str], int]


def with_warm_start_hints(
    data: ProblemData,
    *,
    preserve_existing: bool = True,
) -> ProblemData:
    if preserve_existing and data.hint_assignments:
        return replace(data, hint_assignments=dict(data.hint_assignments))

    hints = build_warm_start_hints(data)
    return replace(data, hint_assignments=hints)


def without_hints(data: ProblemData) -> ProblemData:
    return replace(data, hint_assignments={})


def build_warm_start_hints(data: ProblemData) -> HintAssignments:
    assigned_by_employee: Dict[int, List[Tuple[int, int]]] = {
        employee.employee_id: [] for employee in data.employees
    }
    assigned_days: Dict[int, set[int]] = {
        employee.employee_id: set() for employee in data.employees
    }
    assigned_hours: Dict[int, int] = {
        employee.employee_id: 0 for employee in data.employees
    }
    weekend_assignments: Dict[int, int] = {
        employee.employee_id: 0 for employee in data.employees
    }
    shift_counts: Dict[Tuple[int, int], int] = {
        (employee.employee_id, shift): 0
        for employee in data.employees
        for shift in range(len(data.shifts))
    }
    hints: HintAssignments = {}

    for day, shift, role in _ordered_demand_slots(data):
        required = data.demand[day][shift][role]
        selected_count = 0
        while selected_count < required:
            candidates = [
                employee
                for employee in data.employees
                if _can_hint_employee(
                    data,
                    employee,
                    day,
                    shift,
                    role,
                    assigned_by_employee,
                    assigned_days,
                    assigned_hours,
                )
            ]
            if not candidates:
                break

            employee = min(
                candidates,
                key=lambda candidate: (
                    assigned_hours[candidate.employee_id],
                    weekend_assignments[candidate.employee_id]
                    if day in (5, 6)
                    else 0,
                    shift_counts[(candidate.employee_id, shift)],
                    candidate.hourly_cost,
                    candidate.employee_id,
                ),
            )
            hints[(employee.employee_id, day, shift, role)] = 1
            assigned_by_employee[employee.employee_id].append((day, shift))
            assigned_days[employee.employee_id].add(day)
            duration = shift_duration_hours(
                data.shift_start_hours,
                data.shift_end_hours,
                shift,
            )
            assigned_hours[employee.employee_id] += duration
            if day in (5, 6):
                weekend_assignments[employee.employee_id] += 1
            shift_counts[(employee.employee_id, shift)] += 1
            selected_count += 1

    return hints


def _ordered_demand_slots(data: ProblemData) -> List[Tuple[int, int, str]]:
    slots = [
        (day, shift, role)
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
        if data.demand[day][shift][role] > 0
    ]
    return sorted(
        slots,
        key=lambda slot: (
            _role_available_candidate_count(data, *slot),
            slot[0],
            slot[1],
            data.roles.index(slot[2]),
        ),
    )


def _role_available_candidate_count(
    data: ProblemData,
    day: int,
    shift: int,
    role: str,
) -> int:
    return sum(
        1
        for employee in data.employees
        if role in employee.roles and employee.availability[day][shift]
    )


def _can_hint_employee(
    data: ProblemData,
    employee: Employee,
    day: int,
    shift: int,
    role: str,
    assigned_by_employee: Dict[int, List[Tuple[int, int]]],
    assigned_days: Dict[int, set[int]],
    assigned_hours: Dict[int, int],
) -> bool:
    if role not in employee.roles:
        return False
    if not employee.availability[day][shift]:
        return False
    if day in assigned_days[employee.employee_id]:
        return False

    duration = shift_duration_hours(
        data.shift_start_hours,
        data.shift_end_hours,
        shift,
    )
    if assigned_hours[employee.employee_id] + duration > employee.max_weekly_hours:
        return False

    employee_assignments = assigned_by_employee[employee.employee_id]
    if not _minimum_rest_compatible(data, employee_assignments, day, shift):
        return False
    if not _closing_to_opening_compatible(data, employee_assignments, day, shift):
        return False
    if not _max_consecutive_days_compatible(data, employee_assignments, day):
        return False

    return True


def _minimum_rest_compatible(
    data: ProblemData,
    assignments: List[Tuple[int, int]],
    day: int,
    shift: int,
) -> bool:
    windows = [
        shift_start_end(data.shift_start_hours, data.shift_end_hours, d, s)
        for d, s in assignments
    ]
    windows.append(
        shift_start_end(data.shift_start_hours, data.shift_end_hours, day, shift)
    )
    windows.sort(key=lambda item: item[0])
    for idx in range(1, len(windows)):
        _prev_start, prev_end = windows[idx - 1]
        current_start, _current_end = windows[idx]
        if current_start - prev_end < data.min_rest_hours:
            return False
    return True


def _closing_to_opening_compatible(
    data: ProblemData,
    assignments: List[Tuple[int, int]],
    day: int,
    shift: int,
) -> bool:
    opening_index, closing_index = opening_closing_shift_indices(
        data.shift_start_hours,
        data.shift_end_hours,
    )
    assigned_slots = set(assignments)
    assigned_slots.add((day, shift))
    for assigned_day in data.days[:-1]:
        if (
            (assigned_day, closing_index) in assigned_slots
            and (assigned_day + 1, opening_index) in assigned_slots
        ):
            return False
    return True


def _max_consecutive_days_compatible(
    data: ProblemData,
    assignments: List[Tuple[int, int]],
    day: int,
) -> bool:
    worked_days = sorted({assigned_day for assigned_day, _shift in assignments} | {day})
    consecutive = 0
    last_day = None
    for worked_day in worked_days:
        if last_day is None or worked_day != last_day + 1:
            consecutive = 1
        else:
            consecutive += 1
        if consecutive > data.max_consecutive_days:
            return False
        last_day = worked_day
    return True
