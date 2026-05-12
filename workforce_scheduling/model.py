from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
from ortools.sat.python import cp_model

from .data import ProblemData, opening_closing_shift_indices, shift_start_end


@dataclass
class ConstraintMetadata:
    counts: Dict[str, int]

    def add(self, name: str, count: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + count


@dataclass
class ModelArtifacts:
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar]
    shortages: Dict[Tuple[int, int, str], cp_model.IntVar]
    constraint_metadata: Dict[str, int]


def build_model(
    data: ProblemData,
) -> Tuple[cp_model.CpModel, ModelArtifacts]:
    model = cp_model.CpModel()
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar] = {}
    metadata = ConstraintMetadata(counts={})
    role_skipped = 0
    availability_skipped = 0

    for employee in data.employees:
        for day in data.days:
            for shift in range(len(data.shifts)):
                for role in data.roles:
                    if role not in employee.roles:
                        role_skipped += 1
                        continue
                    if not employee.availability[day][shift]:
                        availability_skipped += 1
                        continue
                    var = model.NewBoolVar(
                        f"assign_e{employee.employee_id}_d{day}_s{shift}_r{role}"
                    )
                    assignments[(employee.employee_id, day, shift, role)] = var

    metadata.add("role_qualification", role_skipped)
    metadata.add("availability", availability_skipped)

    employee_day_vars, employee_shift_vars = _index_assignment_vars(assignments)

    _add_one_shift_per_day(model, data, employee_day_vars, metadata)
    _add_weekly_hours(model, data, assignments, metadata)
    _add_min_rest_constraints(
        model,
        data,
        employee_shift_vars,
        metadata,
    )
    _add_closing_to_opening_constraints(
        model,
        data,
        employee_shift_vars,
        metadata,
    )
    _add_max_consecutive_days_constraints(
        model,
        data,
        employee_day_vars,
        metadata,
    )
    shortages = _add_coverage_constraints(
        model,
        data,
        assignments,
        metadata,
    )
    _add_cost_objective(model, data, assignments, shortages)
    _add_hints(model, data, assignments)

    return model, ModelArtifacts(
        assignments=assignments,
        shortages=shortages,
        constraint_metadata=metadata.counts,
    )


def _index_assignment_vars(
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar]
) -> Tuple[
    Dict[Tuple[int, int], List[cp_model.IntVar]],
    Dict[Tuple[int, int, int], List[cp_model.IntVar]],
]:
    employee_day_vars: Dict[Tuple[int, int], List[cp_model.IntVar]] = {}
    employee_shift_vars: Dict[Tuple[int, int, int], List[cp_model.IntVar]] = {}
    for (employee_id, day, shift, _role), var in assignments.items():
        employee_day_vars.setdefault((employee_id, day), []).append(var)
        employee_shift_vars.setdefault((employee_id, day, shift), []).append(var)
    return employee_day_vars, employee_shift_vars


def _add_one_shift_per_day(
    model: cp_model.CpModel,
    data: ProblemData,
    employee_day_vars: Dict[Tuple[int, int], List[cp_model.IntVar]],
    metadata: ConstraintMetadata,
) -> None:
    for employee in data.employees:
        for day in data.days:
            vars_for_day = employee_day_vars.get((employee.employee_id, day), [])
            if vars_for_day:
                model.Add(sum(vars_for_day) <= 1)
                metadata.add("one_shift_per_day")


def _add_weekly_hours(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    metadata: ConstraintMetadata,
) -> None:
    for employee in data.employees:
        vars_for_employee = [
            assignments[(employee.employee_id, day, shift, role)]
            for day in data.days
            for shift in range(len(data.shifts))
            for role in data.roles
            if (employee.employee_id, day, shift, role) in assignments
        ]
        if vars_for_employee:
            model.Add(
                sum(vars_for_employee) * data.shift_length_hours
                <= employee.max_weekly_hours
            )
            metadata.add("weekly_hours")


def _add_min_rest_constraints(
    model: cp_model.CpModel,
    data: ProblemData,
    employee_shift_vars: Dict[Tuple[int, int, int], List[cp_model.IntVar]],
    metadata: ConstraintMetadata,
) -> None:
    shift_instances: List[Tuple[int, int, int, int]] = []
    for day in data.days:
        for shift in range(len(data.shifts)):
            start, end = shift_start_end(
                data.shift_start_hours,
                data.shift_end_hours,
                day,
                shift,
            )
            shift_instances.append((day, shift, start, end))
    shift_instances.sort(key=lambda item: item[2])

    for employee in data.employees:
        for idx, (day_a, shift_a, _start_a, end_a) in enumerate(shift_instances):
            vars_a = employee_shift_vars.get((employee.employee_id, day_a, shift_a), [])
            if not vars_a:
                continue
            for day_b, shift_b, start_b, _end_b in shift_instances[idx + 1 :]:
                vars_b = employee_shift_vars.get(
                    (employee.employee_id, day_b, shift_b),
                    [],
                )
                if not vars_b:
                    continue
                rest_hours = start_b - end_a
                if rest_hours < data.min_rest_hours:
                    model.Add(sum(vars_a) + sum(vars_b) <= 1)
                    metadata.add("minimum_rest")


def _add_closing_to_opening_constraints(
    model: cp_model.CpModel,
    data: ProblemData,
    employee_shift_vars: Dict[Tuple[int, int, int], List[cp_model.IntVar]],
    metadata: ConstraintMetadata,
) -> None:
    opening_index, closing_index = opening_closing_shift_indices(
        data.shift_start_hours,
        data.shift_end_hours,
    )
    for employee in data.employees:
        for day in data.days[:-1]:
            closing_vars = employee_shift_vars.get(
                (employee.employee_id, day, closing_index),
                [],
            )
            opening_vars = employee_shift_vars.get(
                (employee.employee_id, day + 1, opening_index),
                [],
            )
            if closing_vars and opening_vars:
                model.Add(sum(closing_vars) + sum(opening_vars) <= 1)
                metadata.add("closing_to_opening")


def _add_max_consecutive_days_constraints(
    model: cp_model.CpModel,
    data: ProblemData,
    employee_day_vars: Dict[Tuple[int, int], List[cp_model.IntVar]],
    metadata: ConstraintMetadata,
) -> None:
    window_size = data.max_consecutive_days + 1
    if window_size <= 1:
        return

    for employee in data.employees:
        for start_day in range(len(data.days) - window_size + 1):
            window_days = data.days[start_day : start_day + window_size]
            vars_for_window: List[cp_model.LinearExpr] = []
            for day in window_days:
                day_vars = employee_day_vars.get((employee.employee_id, day), [])
                if day_vars:
                    vars_for_window.append(sum(day_vars))
            if vars_for_window:
                model.Add(sum(vars_for_window) <= data.max_consecutive_days)
                metadata.add("max_consecutive_days")


def _add_coverage_constraints(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    metadata: ConstraintMetadata,
) -> Dict[Tuple[int, int, str], cp_model.IntVar]:
    shortages: Dict[Tuple[int, int, str], cp_model.IntVar] = {}
    for day in data.days:
        for shift in range(len(data.shifts)):
            for role in data.roles:
                vars_for_slot = [
                    assignments[(employee.employee_id, day, shift, role)]
                    for employee in data.employees
                    if (employee.employee_id, day, shift, role) in assignments
                ]
                required = data.demand[day][shift][role]
                shortage = model.NewIntVar(
                    0,
                    required,
                    f"shortage_d{day}_s{shift}_r{role}",
                )
                model.Add(sum(vars_for_slot) + shortage == required)
                shortages[(day, shift, role)] = shortage
                metadata.add("staffing_coverage_soft")
    return shortages


def _add_cost_objective(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    shortages: Dict[Tuple[int, int, str], cp_model.IntVar],
) -> None:
    terms: List[cp_model.LinearExpr] = []
    employee_index = {e.employee_id: e for e in data.employees}
    for (employee_id, _day, _shift, _role), var in assignments.items():
        employee = employee_index[employee_id]
        terms.append(var * employee.hourly_cost * data.shift_length_hours)
    for shortage in shortages.values():
        terms.append(shortage * data.shortage_penalty)
    if terms:
        model.Minimize(sum(terms))


def _add_hints(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
) -> None:
    for key, value in data.hint_assignments.items():
        if key in assignments:
            model.AddHint(assignments[key], value)
