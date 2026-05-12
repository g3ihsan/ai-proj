from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
from ortools.sat.python import cp_model

from .data import Employee, ProblemData, opening_closing_shift_indices, shift_start_end
from .model import build_model


@dataclass
class Assignment:
    employee_id: int
    day: int
    shift: int
    role: str


@dataclass
class SolverMetrics:
    status: str
    objective_value: float | None
    best_bound: float | None
    wall_time_sec: float
    num_conflicts: int
    num_branches: int
    num_variables: int
    num_constraints: int


@dataclass
class SolveResult:
    metrics: SolverMetrics
    assignments: List[Assignment]
    shortages: Dict[Tuple[int, int, str], int]
    violations: List[str]
    constraint_metadata: Dict[str, int]


def solve(
    data: ProblemData,
    time_limit_sec: float = 10.0,
    seed: int = 1,
) -> SolveResult:
    model, artifacts = build_model(data)
    variables = artifacts.assignments
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_sec
    solver.parameters.random_seed = seed
    solver.parameters.num_search_workers = 1
    solver.parameters.randomize_search = False

    status = solver.Solve(model)
    status_name = solver.StatusName(status)

    assignments: List[Assignment] = []
    shortages: Dict[Tuple[int, int, str], int] = {}
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        for (employee_id, day, shift, role), var in variables.items():
            if solver.Value(var) == 1:
                assignments.append(
                    Assignment(
                        employee_id=employee_id,
                        day=day,
                        shift=shift,
                        role=role,
                    )
                )
        for key, var in artifacts.shortages.items():
            shortages[key] = solver.Value(var)

    metrics = SolverMetrics(
        status=status_name,
        objective_value=(
            solver.ObjectiveValue()
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            else None
        ),
        best_bound=(
            solver.BestObjectiveBound()
            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE)
            else None
        ),
        wall_time_sec=solver.WallTime(),
        num_conflicts=solver.NumConflicts(),
        num_branches=solver.NumBranches(),
        num_variables=len(model.Proto().variables),
        num_constraints=len(model.Proto().constraints),
    )

    violations = []
    if assignments:
        violations = validate_solution(data, assignments, shortages)

    return SolveResult(
        metrics=metrics,
        assignments=assignments,
        shortages=shortages,
        violations=violations,
        constraint_metadata=artifacts.constraint_metadata,
    )


def validate_solution(
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int] | None = None,
) -> List[str]:
    errors: List[str] = []
    employee_index: Dict[int, Employee] = {e.employee_id: e for e in data.employees}

    coverage: Dict[Tuple[int, int, str], int] = {}
    daily_work: Dict[Tuple[int, int], int] = {}
    weekly_hours: Dict[int, int] = {e.employee_id: 0 for e in data.employees}
    employee_shift_assignments: Dict[int, List[Tuple[int, int]]] = {
        e.employee_id: [] for e in data.employees
    }

    for assignment in assignments:
        employee = employee_index[assignment.employee_id]
        key = (assignment.day, assignment.shift, assignment.role)
        coverage[key] = coverage.get(key, 0) + 1

        daily_key = (assignment.employee_id, assignment.day)
        daily_work[daily_key] = daily_work.get(daily_key, 0) + 1
        if daily_work[daily_key] > 1:
            errors.append(
                f"Employee {assignment.employee_id} works multiple shifts on day {assignment.day}"
            )

        weekly_hours[assignment.employee_id] += data.shift_length_hours
        if weekly_hours[assignment.employee_id] > employee.max_weekly_hours:
            errors.append(
                f"Employee {assignment.employee_id} exceeds weekly hours"
            )

        if assignment.role not in employee.roles:
            errors.append(
                f"Employee {assignment.employee_id} assigned to unqualified role {assignment.role}"
            )

        if not employee.availability[assignment.day][assignment.shift]:
            errors.append(
                f"Employee {assignment.employee_id} assigned while unavailable"
            )

        employee_shift_assignments[assignment.employee_id].append(
            (assignment.day, assignment.shift)
        )

    _validate_rest_windows(data, employee_shift_assignments, errors)
    _validate_closing_to_opening(data, employee_shift_assignments, errors)
    _validate_max_consecutive_days(data, employee_shift_assignments, errors)

    for day in data.days:
        for shift in range(len(data.shifts)):
            for role in data.roles:
                required = data.demand[day][shift][role]
                actual = coverage.get((day, shift, role), 0)
                shortage_actual = required - actual
                if shortage_actual < 0:
                    errors.append(
                        f"Overstaffed day {day} shift {shift} role {role}: {actual} > {required}"
                    )
                if shortages is not None:
                    reported = shortages.get((day, shift, role))
                    if reported is None:
                        errors.append(
                            f"Missing shortage entry for day {day} shift {shift} role {role}"
                        )
                    elif reported != max(0, shortage_actual):
                        errors.append(
                            f"Shortage mismatch day {day} shift {shift} role {role}: {reported} != {max(0, shortage_actual)}"
                        )

    return errors


def _validate_rest_windows(
    data: ProblemData,
    employee_shift_assignments: Dict[int, List[Tuple[int, int]]],
    errors: List[str],
) -> None:
    for employee_id, shifts in employee_shift_assignments.items():
        shift_windows = []
        for day, shift in shifts:
            start, end = shift_start_end(
                data.shift_start_hours,
                data.shift_end_hours,
                day,
                shift,
            )
            shift_windows.append((start, end, day, shift))
        shift_windows.sort(key=lambda item: item[0])
        for idx in range(1, len(shift_windows)):
            _prev_start, prev_end, prev_day, prev_shift = shift_windows[idx - 1]
            start, _end, day, shift = shift_windows[idx]
            rest_hours = start - prev_end
            if rest_hours < data.min_rest_hours:
                errors.append(
                    "Employee "
                    f"{employee_id} violates minimum rest between day {prev_day} shift {prev_shift} "
                    f"and day {day} shift {shift}"
                )


def _validate_closing_to_opening(
    data: ProblemData,
    employee_shift_assignments: Dict[int, List[Tuple[int, int]]],
    errors: List[str],
) -> None:
    opening_index, closing_index = opening_closing_shift_indices(
        data.shift_start_hours,
        data.shift_end_hours,
    )
    for employee_id, shifts in employee_shift_assignments.items():
        assigned_days = {(day, shift) for day, shift in shifts}
        for day in data.days[:-1]:
            if (day, closing_index) in assigned_days and (
                day + 1,
                opening_index,
            ) in assigned_days:
                errors.append(
                    "Employee "
                    f"{employee_id} violates closing-to-opening between day {day} and day {day + 1}"
                )


def _validate_max_consecutive_days(
    data: ProblemData,
    employee_shift_assignments: Dict[int, List[Tuple[int, int]]],
    errors: List[str],
) -> None:
    max_days = data.max_consecutive_days
    for employee_id, shifts in employee_shift_assignments.items():
        worked_days = sorted({day for day, _shift in shifts})
        consecutive = 0
        last_day = None
        for day in worked_days:
            if last_day is None or day != last_day + 1:
                consecutive = 1
            else:
                consecutive += 1
            if consecutive > max_days:
                errors.append(
                    f"Employee {employee_id} exceeds max consecutive days"
                )
                break
            last_day = day
