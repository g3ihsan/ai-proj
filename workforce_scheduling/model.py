from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
from ortools.sat.python import cp_model

from .data import (
    ProblemData,
    opening_closing_shift_indices,
    shift_duration_hours,
    shift_start_end,
    validate_problem_data,
)


@dataclass
class ConstraintRecord:
    family: str
    employee_id: int | None = None
    day: int | None = None
    shift: int | None = None
    role: str | None = None
    description: str = ""


@dataclass
class ConstraintMetadata:
    counts: Dict[str, int]
    records: List[ConstraintRecord]

    def add(self, name: str, count: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + count

    def record(
        self,
        family: str,
        *,
        employee_id: int | None = None,
        day: int | None = None,
        shift: int | None = None,
        role: str | None = None,
        description: str = "",
    ) -> None:
        self.add(family)
        self.records.append(
            ConstraintRecord(
                family=family,
                employee_id=employee_id,
                day=day,
                shift=shift,
                role=role,
                description=description,
            )
        )


@dataclass
class ModelArtifacts:
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar]
    shortages: Dict[Tuple[int, int, str], cp_model.IntVar]
    constraint_metadata: Dict[str, int]
    constraint_records: List[ConstraintRecord]


@dataclass
class FairnessTerms:
    terms: List[cp_model.IntVar]
    upper_bound: int
    workload_upper_bound: int
    weekend_upper_bound: int
    shift_distribution_upper_bound: int


def build_model(
    data: ProblemData,
) -> Tuple[cp_model.CpModel, ModelArtifacts]:
    data_errors = validate_problem_data(data)
    if data_errors:
        raise ValueError("; ".join(data_errors))

    model = cp_model.CpModel()
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar] = {}
    metadata = ConstraintMetadata(counts={}, records=[])
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
    metadata.add("assignment_variables", len(assignments))
    _add_shift_duration_metadata(data, metadata)

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
    _add_priority_objective(model, data, assignments, shortages, metadata)
    _add_hints(model, data, assignments)

    return model, ModelArtifacts(
        assignments=assignments,
        shortages=shortages,
        constraint_metadata=metadata.counts,
        constraint_records=metadata.records,
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
                metadata.record(
                    "one_shift_per_day",
                    employee_id=employee.employee_id,
                    day=day,
                    description="Employee may work at most one shift on this day.",
                )


def _add_weekly_hours(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    metadata: ConstraintMetadata,
) -> None:
    for employee in data.employees:
        terms = [
            assignments[(employee.employee_id, day, shift, role)]
            * shift_duration_hours(
                data.shift_start_hours,
                data.shift_end_hours,
                shift,
            )
            for day in data.days
            for shift in range(len(data.shifts))
            for role in data.roles
            if (employee.employee_id, day, shift, role) in assignments
        ]
        if terms:
            model.Add(sum(terms) <= employee.max_weekly_hours)
            metadata.record(
                "weekly_hours",
                employee_id=employee.employee_id,
                description=(
                    "Total assigned shift durations must not exceed "
                    "employee max weekly hours."
                ),
            )


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
                    metadata.record(
                        "minimum_rest",
                        employee_id=employee.employee_id,
                        day=day_a,
                        shift=shift_a,
                        description=(
                            f"Cannot work day {day_a} shift {shift_a} and "
                            f"day {day_b} shift {shift_b}; rest would be "
                            f"{rest_hours} hours."
                        ),
                    )


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
                metadata.record(
                    "closing_to_opening",
                    employee_id=employee.employee_id,
                    day=day,
                    shift=closing_index,
                    description=(
                        f"Cannot work closing shift {closing_index} on day {day} "
                        f"and opening shift {opening_index} on day {day + 1}."
                    ),
                )


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
                metadata.record(
                    "max_consecutive_days",
                    employee_id=employee.employee_id,
                    day=window_days[0],
                    description=(
                        f"At most {data.max_consecutive_days} worked days in "
                        f"window {window_days}."
                    ),
                )


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
                metadata.add("shortage_variables")
                metadata.record(
                    "staffing_coverage_soft",
                    day=day,
                    shift=shift,
                    role=role,
                    description=(
                        f"Coverage plus shortage must equal demand {required}."
                    ),
                )
    return shortages


def _add_priority_objective(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    shortages: Dict[Tuple[int, int, str], cp_model.IntVar],
    metadata: ConstraintMetadata,
) -> None:
    objective_terms: List[cp_model.LinearExpr] = []
    employee_index = {e.employee_id: e for e in data.employees}
    labor_cost_terms: List[cp_model.LinearExpr] = []
    labor_cost_upper_bound = _labor_cost_upper_bound(data, assignments)
    fairness = _add_fairness_terms(model, data, assignments, metadata)
    fairness_priority_weight = labor_cost_upper_bound + 1
    shortage_priority_weight = max(
        data.shortage_penalty,
        fairness_priority_weight * fairness.upper_bound + labor_cost_upper_bound + 1,
    )

    metadata.add("shortage_priority_weight", shortage_priority_weight)
    metadata.add("fairness_priority_weight", fairness_priority_weight)
    metadata.add("labor_cost_component_upper_bound", labor_cost_upper_bound)
    metadata.add(
        "workload_fairness_component_upper_bound",
        fairness.workload_upper_bound,
    )
    metadata.add(
        "weekend_fairness_component_upper_bound",
        fairness.weekend_upper_bound,
    )
    metadata.add(
        "shift_distribution_fairness_component_upper_bound",
        fairness.shift_distribution_upper_bound,
    )
    metadata.record(
        "objective_priority",
        description=(
            "Objective priority is total shortage first, fairness spread second, "
            "and labor cost last."
        ),
    )
    metadata.record(
        "labor_cost_component",
        description="Final tie-breaker objective: assigned duration times hourly cost.",
    )

    for (employee_id, _day, shift, _role), var in assignments.items():
        employee = employee_index[employee_id]
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            shift,
        )
        labor_cost_terms.append(var * employee.hourly_cost * duration)
    for shortage in shortages.values():
        objective_terms.append(shortage * shortage_priority_weight)
    for fairness_term in fairness.terms:
        objective_terms.append(fairness_term * fairness_priority_weight)
    objective_terms.extend(labor_cost_terms)
    if objective_terms:
        model.Minimize(sum(objective_terms))


def _add_fairness_terms(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
    metadata: ConstraintMetadata,
) -> FairnessTerms:
    employee_ids = [employee.employee_id for employee in data.employees]
    assignment_terms_by_employee = {
        employee_id: [
            var
            * shift_duration_hours(
                data.shift_start_hours,
                data.shift_end_hours,
                shift,
            )
            for (assigned_employee_id, _day, shift, _role), var in assignments.items()
            if assigned_employee_id == employee_id
        ]
        for employee_id in employee_ids
    }

    workload_spread, workload_upper_bound = _add_spread_term(
        model,
        "workload_hours",
        assignment_terms_by_employee,
        _max_assignable_hours(data),
    )
    terms: List[cp_model.IntVar] = []
    if workload_spread is not None:
        terms.append(workload_spread)
        metadata.record(
            "workload_fairness_component",
            description=(
                "Soft objective: minimize spread between maximum and minimum "
                "assigned hours for employees with feasible assignment variables."
            ),
        )

    weekend_days = {day for day in data.days if day in (5, 6)}
    weekend_terms_by_employee = {
        employee_id: [
            var
            for (assigned_employee_id, day, _shift, _role), var in assignments.items()
            if assigned_employee_id == employee_id and day in weekend_days
        ]
        for employee_id in employee_ids
    }
    weekend_spread, weekend_upper_bound = _add_spread_term(
        model,
        "weekend_assignments",
        weekend_terms_by_employee,
        len(weekend_days),
    )
    if weekend_spread is not None:
        terms.append(weekend_spread)
        metadata.record(
            "weekend_fairness_component",
            description=(
                "Soft objective: minimize spread of assignments on assumed "
                "weekend days 5 and 6."
            ),
        )

    shift_distribution_upper_bound = 0
    for shift in range(len(data.shifts)):
        shift_terms_by_employee = {
            employee_id: [
                var
                for (
                    assigned_employee_id,
                    _day,
                    assigned_shift,
                    _role,
                ), var in assignments.items()
                if assigned_employee_id == employee_id and assigned_shift == shift
            ]
            for employee_id in employee_ids
        }
        shift_spread, shift_upper_bound = _add_spread_term(
            model,
            f"shift_{shift}_distribution",
            shift_terms_by_employee,
            len(data.days),
        )
        shift_distribution_upper_bound += shift_upper_bound
        if shift_spread is not None:
            terms.append(shift_spread)
            metadata.record(
                "shift_distribution_fairness_component",
                shift=shift,
                description=(
                    f"Soft objective: minimize concentration spread for shift {shift}."
                ),
            )

    return FairnessTerms(
        terms=terms,
        upper_bound=(
            workload_upper_bound
            + weekend_upper_bound
            + shift_distribution_upper_bound
        ),
        workload_upper_bound=workload_upper_bound,
        weekend_upper_bound=weekend_upper_bound,
        shift_distribution_upper_bound=shift_distribution_upper_bound,
    )


def _add_spread_term(
    model: cp_model.CpModel,
    name: str,
    terms_by_employee: Dict[int, List[cp_model.LinearExpr]],
    value_upper_bound: int,
) -> Tuple[cp_model.IntVar | None, int]:
    relevant_terms = {
        employee_id: terms
        for employee_id, terms in terms_by_employee.items()
        if terms
    }
    if len(relevant_terms) < 2 or value_upper_bound <= 0:
        return None, 0

    values: List[cp_model.IntVar] = []
    for employee_id, terms in relevant_terms.items():
        value = model.NewIntVar(0, value_upper_bound, f"{name}_e{employee_id}")
        model.Add(value == sum(terms))
        values.append(value)

    max_value = model.NewIntVar(0, value_upper_bound, f"{name}_max")
    min_value = model.NewIntVar(0, value_upper_bound, f"{name}_min")
    spread = model.NewIntVar(0, value_upper_bound, f"{name}_spread")
    model.AddMaxEquality(max_value, values)
    model.AddMinEquality(min_value, values)
    model.Add(spread == max_value - min_value)
    return spread, value_upper_bound


def _max_assignable_hours(data: ProblemData) -> int:
    total_duration = sum(
        shift_duration_hours(data.shift_start_hours, data.shift_end_hours, shift)
        for shift in range(len(data.shifts))
    ) * len(data.days)
    max_employee_hours = max(
        (employee.max_weekly_hours for employee in data.employees),
        default=0,
    )
    return min(total_duration, max_employee_hours)


def _labor_cost_upper_bound(
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
) -> int:
    employee_index = {employee.employee_id: employee for employee in data.employees}
    upper_bound = 0
    for employee_id, _day, shift, _role in assignments:
        employee = employee_index[employee_id]
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            shift,
        )
        upper_bound += employee.hourly_cost * duration
    return upper_bound


def _add_shift_duration_metadata(
    data: ProblemData,
    metadata: ConstraintMetadata,
) -> None:
    durations = [
        shift_duration_hours(data.shift_start_hours, data.shift_end_hours, shift)
        for shift in range(len(data.shifts))
    ]
    if not durations:
        return
    metadata.add("shift_duration_hours_min", min(durations))
    metadata.add("shift_duration_hours_max", max(durations))


def _add_hints(
    model: cp_model.CpModel,
    data: ProblemData,
    assignments: Dict[Tuple[int, int, int, str], cp_model.IntVar],
) -> None:
    for key, value in data.hint_assignments.items():
        if key in assignments:
            model.AddHint(assignments[key], value)
