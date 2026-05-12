from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
import random


@dataclass
class Employee:
    employee_id: int
    name: str
    roles: Tuple[str, ...]
    hourly_cost: int
    max_weekly_hours: int
    availability: List[List[bool]]  # days x shifts


@dataclass
class ProblemData:
    employees: List[Employee]
    roles: List[str]
    days: List[int]
    shifts: List[str]
    shift_start_hours: List[int]
    shift_end_hours: List[int]
    min_rest_hours: int
    max_consecutive_days: int
    shortage_penalty: int
    demand: Dict[int, Dict[int, Dict[str, int]]]
    hint_assignments: Dict[Tuple[int, int, int, str], int]


def shift_start_end(
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    day: int,
    shift: int,
) -> Tuple[int, int]:
    start_hour = shift_start_hours[shift]
    end_hour = shift_end_hours[shift]
    start = day * 24 + start_hour
    end = day * 24 + end_hour
    if end_hour <= start_hour:
        end += 24
    return start, end


def shift_duration_hours(
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    shift: int,
) -> int:
    start, end = shift_start_end(shift_start_hours, shift_end_hours, 0, shift)
    return end - start


def shift_end_offset(start_hour: int, end_hour: int) -> int:
    return end_hour if end_hour > start_hour else end_hour + 24


def opening_closing_shift_indices(
    shift_start_hours: List[int],
    shift_end_hours: List[int],
) -> Tuple[int, int]:
    opening_index = min(
        range(len(shift_start_hours)),
        key=lambda idx: shift_start_hours[idx],
    )
    closing_index = max(
        range(len(shift_start_hours)),
        key=lambda idx: shift_end_offset(
            shift_start_hours[idx],
            shift_end_hours[idx],
        ),
    )
    return opening_index, closing_index


def validate_problem_data(data: ProblemData) -> List[str]:
    errors: List[str] = []
    shift_count = len(data.shifts)

    if not data.days:
        errors.append("At least one day is required")
    if not data.shifts:
        errors.append("At least one shift is required")
    if not data.roles:
        errors.append("At least one role is required")
    if data.days != list(range(len(data.days))):
        errors.append("Days must be consecutive zero-based integers")
    if len(data.shift_start_hours) != shift_count:
        errors.append("shift_start_hours length must match shifts length")
    if len(data.shift_end_hours) != shift_count:
        errors.append("shift_end_hours length must match shifts length")
    if data.min_rest_hours < 0:
        errors.append("min_rest_hours must be non-negative")
    if data.max_consecutive_days < 1:
        errors.append("max_consecutive_days must be at least 1")
    if data.shortage_penalty < 0:
        errors.append("shortage_penalty must be non-negative")

    if (
        len(data.shift_start_hours) == shift_count
        and len(data.shift_end_hours) == shift_count
    ):
        for shift in range(shift_count):
            start_hour = data.shift_start_hours[shift]
            end_hour = data.shift_end_hours[shift]
            if not 0 <= start_hour < 24:
                errors.append(f"Shift {shift} start hour must be in [0, 23]")
            if not 0 <= end_hour <= 24:
                errors.append(f"Shift {shift} end hour must be in [0, 24]")
            duration = shift_duration_hours(
                data.shift_start_hours,
                data.shift_end_hours,
                shift,
            )
            if duration <= 0:
                errors.append(f"Shift {shift} duration must be positive")

    employee_ids = set()
    for employee in data.employees:
        if employee.employee_id in employee_ids:
            errors.append(f"Duplicate employee_id {employee.employee_id}")
        employee_ids.add(employee.employee_id)
        if employee.hourly_cost < 0:
            errors.append(
                f"Employee {employee.employee_id} hourly_cost must be non-negative"
            )
        if employee.max_weekly_hours < 0:
            errors.append(
                f"Employee {employee.employee_id} max_weekly_hours must be non-negative"
            )
        unknown_roles = [role for role in employee.roles if role not in data.roles]
        if unknown_roles:
            errors.append(
                f"Employee {employee.employee_id} has unknown roles {unknown_roles}"
            )
        if len(employee.availability) != len(data.days):
            errors.append(
                f"Employee {employee.employee_id} availability must have one row per day"
            )
            continue
        for day in data.days:
            if day < 0 or day >= len(employee.availability):
                errors.append(
                    f"Employee {employee.employee_id} availability day {day} "
                    "is outside availability rows"
                )
                continue
            if len(employee.availability[day]) != shift_count:
                errors.append(
                    f"Employee {employee.employee_id} availability day {day} "
                    "must have one value per shift"
                )

    for day in data.days:
        if day not in data.demand:
            errors.append(f"Missing demand for day {day}")
            continue
        for shift in range(shift_count):
            if shift not in data.demand[day]:
                errors.append(f"Missing demand for day {day} shift {shift}")
                continue
            for role in data.roles:
                if role not in data.demand[day][shift]:
                    errors.append(
                        f"Missing demand for day {day} shift {shift} role {role}"
                    )
                    continue
                if data.demand[day][shift][role] < 0:
                    errors.append(
                        f"Demand for day {day} shift {shift} role {role} "
                        "must be non-negative"
                    )

    for employee_id, day, shift, role in data.hint_assignments:
        if employee_id not in employee_ids:
            errors.append(f"Hint references unknown employee {employee_id}")
        if day not in data.days:
            errors.append(f"Hint references unknown day {day}")
        if shift < 0 or shift >= shift_count:
            errors.append(f"Hint references unknown shift {shift}")
        if role not in data.roles:
            errors.append(f"Hint references unknown role {role}")

    return errors


def generate_synthetic_data(
    seed: int,
    num_employees: int = 40,
    num_days: int = 7,
    shifts_per_day: int = 3,
    base_role_demand: Dict[str, int] | None = None,
) -> ProblemData:
    rng = random.Random(seed)
    roles = ["cashier", "cook", "manager"]
    shifts = [f"shift_{i + 1}" for i in range(shifts_per_day)]
    days = list(range(num_days))
    shift_start_hours = [6, 14, 22][:shifts_per_day]
    shift_end_hours = [14, 22, 6][:shifts_per_day]
    min_rest_hours = 10
    max_consecutive_days = 5
    shortage_penalty = 1000
    if base_role_demand is None:
        base_role_demand = {"cashier": 2, "cook": 2, "manager": 1}

    max_attempts = 200
    for _ in range(max_attempts):
        employees = _generate_employees(rng, num_employees, roles)
        demand = _generate_demand(days, shifts_per_day, roles, base_role_demand)
        _assign_availability(rng, employees, num_days, shifts_per_day)

        hint_assignments = _build_feasible_assignment(
            rng,
            employees,
            days,
            shifts_per_day,
            roles,
            demand,
            shift_start_hours,
            shift_end_hours,
            min_rest_hours,
            max_consecutive_days,
        )
        if hint_assignments is not None:
            return ProblemData(
                employees=employees,
                roles=roles,
                days=days,
                shifts=shifts,
                shift_start_hours=shift_start_hours,
                shift_end_hours=shift_end_hours,
                min_rest_hours=min_rest_hours,
                max_consecutive_days=max_consecutive_days,
                shortage_penalty=shortage_penalty,
                demand=demand,
                hint_assignments=hint_assignments,
            )

    raise RuntimeError("Unable to generate a feasible synthetic dataset")


def _generate_employees(
    rng: random.Random,
    num_employees: int,
    roles: List[str],
) -> List[Employee]:
    role_counts = _balanced_role_counts(num_employees, roles)
    role_pool: List[str] = []
    for role, count in role_counts.items():
        role_pool.extend([role] * count)
    rng.shuffle(role_pool)

    employees: List[Employee] = []
    for idx in range(num_employees):
        primary_role = role_pool[idx]
        assigned_roles = {primary_role}
        if rng.random() < 0.35:
            secondary = rng.choice([r for r in roles if r != primary_role])
            assigned_roles.add(secondary)
        if rng.random() < 0.10:
            tertiary = rng.choice([r for r in roles if r not in assigned_roles])
            assigned_roles.add(tertiary)

        base_cost = {"cashier": 18, "cook": 20, "manager": 26}[primary_role]
        hourly_cost = base_cost + rng.randint(0, 4)
        max_weekly_hours = 32 if rng.random() < 0.2 else 40

        employees.append(
            Employee(
                employee_id=idx,
                name=f"E{idx + 1:02d}",
                roles=tuple(sorted(assigned_roles)),
                hourly_cost=hourly_cost,
                max_weekly_hours=max_weekly_hours,
                availability=[],
            )
        )

    return employees


def _balanced_role_counts(num_employees: int, roles: List[str]) -> Dict[str, int]:
    base = num_employees // len(roles)
    remainder = num_employees % len(roles)
    counts = {role: base for role in roles}
    for role in roles[:remainder]:
        counts[role] += 1
    return counts


def _generate_demand(
    days: List[int],
    shifts_per_day: int,
    roles: List[str],
    base_role_demand: Dict[str, int],
) -> Dict[int, Dict[int, Dict[str, int]]]:
    demand: Dict[int, Dict[int, Dict[str, int]]] = {}
    for day in days:
        demand[day] = {}
        for shift in range(shifts_per_day):
            demand[day][shift] = {role: base_role_demand[role] for role in roles}
    return demand


def _assign_availability(
    rng: random.Random,
    employees: List[Employee],
    num_days: int,
    shifts_per_day: int,
) -> None:
    for employee in employees:
        availability: List[List[bool]] = []
        availability_rate = 0.8 if employee.max_weekly_hours >= 40 else 0.7
        for _ in range(num_days):
            day_slots = [rng.random() < availability_rate for _ in range(shifts_per_day)]
            availability.append(day_slots)
        employee.availability = availability


def _build_feasible_assignment(
    rng: random.Random,
    employees: List[Employee],
    days: List[int],
    shifts_per_day: int,
    roles: List[str],
    demand: Dict[int, Dict[int, Dict[str, int]]],
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    min_rest_hours: int,
    max_consecutive_days: int,
) -> Dict[Tuple[int, int, int, str], int] | None:
    remaining_hours = {e.employee_id: e.max_weekly_hours for e in employees}
    assigned_days = {e.employee_id: set() for e in employees}
    last_shift_end = {e.employee_id: None for e in employees}
    last_shift_day = {e.employee_id: None for e in employees}
    last_shift_index = {e.employee_id: None for e in employees}
    last_worked_day = {e.employee_id: None for e in employees}
    consecutive_days = {e.employee_id: 0 for e in employees}
    assignments: Dict[Tuple[int, int, int, str], int] = {}
    opening_index, closing_index = opening_closing_shift_indices(
        shift_start_hours,
        shift_end_hours,
    )

    for day in days:
        for shift in range(shifts_per_day):
            for role in roles:
                required = demand[day][shift][role]
                duration = shift_duration_hours(
                    shift_start_hours,
                    shift_end_hours,
                    shift,
                )
                candidates = [
                    e
                    for e in employees
                    if role in e.roles
                    and e.availability[day][shift]
                    and day not in assigned_days[e.employee_id]
                    and remaining_hours[e.employee_id] >= duration
                    and _respects_temporal_rules(
                        e.employee_id,
                        day,
                        shift,
                        shift_start_hours,
                        shift_end_hours,
                        min_rest_hours,
                        max_consecutive_days,
                        opening_index,
                        closing_index,
                        last_shift_end,
                        last_shift_day,
                        last_shift_index,
                        last_worked_day,
                        consecutive_days,
                    )
                ]
                if len(candidates) < required:
                    return None

                rng.shuffle(candidates)
                selected = candidates[:required]
                for employee in selected:
                    assignments[(employee.employee_id, day, shift, role)] = 1
                    assigned_days[employee.employee_id].add(day)
                    remaining_hours[employee.employee_id] -= duration
                    _update_temporal_state(
                        employee.employee_id,
                        day,
                        shift,
                        shift_start_hours,
                        shift_end_hours,
                        last_shift_end,
                        last_shift_day,
                        last_shift_index,
                        last_worked_day,
                        consecutive_days,
                    )

    return assignments


def _respects_temporal_rules(
    employee_id: int,
    day: int,
    shift: int,
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    min_rest_hours: int,
    max_consecutive_days: int,
    opening_index: int,
    closing_index: int,
    last_shift_end: Dict[int, int | None],
    last_shift_day: Dict[int, int | None],
    last_shift_index: Dict[int, int | None],
    last_worked_day: Dict[int, int | None],
    consecutive_days: Dict[int, int],
) -> bool:
    start, _end = shift_start_end(shift_start_hours, shift_end_hours, day, shift)
    prior_end = last_shift_end[employee_id]
    if prior_end is not None and start - prior_end < min_rest_hours:
        return False

    prior_day = last_shift_day[employee_id]
    prior_shift = last_shift_index[employee_id]
    if (
        prior_day is not None
        and prior_shift == closing_index
        and day == prior_day + 1
        and shift == opening_index
    ):
        return False

    prior_work_day = last_worked_day[employee_id]
    if prior_work_day is None:
        projected_consecutive = 1
    elif prior_work_day == day - 1:
        projected_consecutive = consecutive_days[employee_id] + 1
    else:
        projected_consecutive = 1
    if projected_consecutive > max_consecutive_days:
        return False

    return True


def _update_temporal_state(
    employee_id: int,
    day: int,
    shift: int,
    shift_start_hours: List[int],
    shift_end_hours: List[int],
    last_shift_end: Dict[int, int | None],
    last_shift_day: Dict[int, int | None],
    last_shift_index: Dict[int, int | None],
    last_worked_day: Dict[int, int | None],
    consecutive_days: Dict[int, int],
) -> None:
    _start, end = shift_start_end(shift_start_hours, shift_end_hours, day, shift)
    last_shift_end[employee_id] = end
    last_shift_day[employee_id] = day
    last_shift_index[employee_id] = shift
    prior_work_day = last_worked_day[employee_id]
    if prior_work_day is None or prior_work_day != day - 1:
        consecutive_days[employee_id] = 1
    else:
        consecutive_days[employee_id] += 1
    last_worked_day[employee_id] = day
