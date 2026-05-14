from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple
from ortools.sat.python import cp_model

from .data import (
    Employee,
    ProblemData,
    opening_closing_shift_indices,
    shift_duration_hours,
    shift_start_end,
)
from .model import ConstraintRecord, build_model


ASSIGNED_AVAILABLE = "ASSIGNED_AVAILABLE"
ASSIGNED_QUALIFIED = "ASSIGNED_QUALIFIED"
ASSIGNED_WITHIN_HOURS = "ASSIGNED_WITHIN_HOURS"
ASSIGNED_REST_COMPATIBLE = "ASSIGNED_REST_COMPATIBLE"
ASSIGNED_COVERED_DEMAND = "ASSIGNED_COVERED_DEMAND"
ASSIGNED_COST_CONTRIBUTION = "ASSIGNED_COST_CONTRIBUTION"
BLOCKED_UNAVAILABLE = "BLOCKED_UNAVAILABLE"
BLOCKED_MISSING_ROLE = "BLOCKED_MISSING_ROLE"
BLOCKED_MAX_HOURS = "BLOCKED_MAX_HOURS"
BLOCKED_ONE_SHIFT_PER_DAY = "BLOCKED_ONE_SHIFT_PER_DAY"
BLOCKED_REST_RULE = "BLOCKED_REST_RULE"
BLOCKED_CLOSING_TO_OPENING = "BLOCKED_CLOSING_TO_OPENING"
BLOCKED_MAX_CONSECUTIVE_DAYS = "BLOCKED_MAX_CONSECUTIVE_DAYS"
BLOCKED_HIGHER_COST_THAN_SELECTED = "BLOCKED_HIGHER_COST_THAN_SELECTED"
NOT_SELECTED_BY_FINAL_OBJECTIVE = "NOT_SELECTED_BY_FINAL_OBJECTIVE"
SHORTAGE_INSUFFICIENT_AVAILABLE_QUALIFIED = (
    "SHORTAGE_INSUFFICIENT_AVAILABLE_QUALIFIED"
)
SHORTAGE_REST_CONFLICT = "SHORTAGE_REST_CONFLICT"
SHORTAGE_MAX_HOURS_LIMIT = "SHORTAGE_MAX_HOURS_LIMIT"
SHORTAGE_DEMAND_EXCEEDS_CAPACITY = "SHORTAGE_DEMAND_EXCEEDS_CAPACITY"

BLOCKER_REASON_CODE_MAP = {
    "missing_role": BLOCKED_MISSING_ROLE,
    "unavailable": BLOCKED_UNAVAILABLE,
    "already_working_that_day": BLOCKED_ONE_SHIFT_PER_DAY,
    "violates_minimum_rest": BLOCKED_REST_RULE,
    "violates_closing_to_opening": BLOCKED_CLOSING_TO_OPENING,
    "violates_max_consecutive_days": BLOCKED_MAX_CONSECUTIVE_DAYS,
    "exceeds_weekly_hours": BLOCKED_MAX_HOURS,
}


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
class FairnessMetrics:
    assigned_hours_per_employee: Dict[int, int]
    min_assigned_hours: int
    max_assigned_hours: int
    workload_spread: int
    weekend_assignments_per_employee: Dict[int, int]
    shift_counts_per_employee_shift: Dict[Tuple[int, int], int]


@dataclass
class ObjectiveBreakdown:
    total_shortage: int
    shortage_objective_value: int
    workload_fairness_value: int
    weekend_fairness_value: int
    shift_distribution_fairness_value: int
    fairness_objective_value: int
    labor_cost_value: int
    total_objective_value: int


@dataclass
class SlotCandidateAnalysis:
    # Post-solve marginal analysis for one demanded slot. This explains the
    # final roster locally; it is not a formal global infeasibility proof.
    day: int
    shift: int
    role: str
    required_count: int
    assigned_count: int
    shortage_count: int
    # Count of employees currently assignable to this slot after considering
    # the final solved schedule and hard scheduling rules.
    candidate_employee_count: int
    assigned_employee_ids: List[int]
    # Employees eligible by role and availability before final-schedule blockers.
    could_work_employee_ids: List[int]
    role_available_employee_ids: List[int]
    currently_assignable_employee_ids: List[int]
    blocked_employee_ids_by_reason: Dict[str, List[int]]


@dataclass
class AssignmentExplanation:
    employee_id: int
    day: int
    shift: int
    role: str
    shift_duration: int
    labor_cost_contribution: int
    employee_weekly_hours: int
    available: bool
    qualified: bool
    within_weekly_hours: bool
    rest_compatible: bool
    reason_codes: List[str]


@dataclass
class NonAssignmentExplanation:
    employee_id: int
    day: int
    shift: int
    role: str
    assigned_employee_ids: List[int]
    reason_codes: List[str]


@dataclass
class ShortageExplanation:
    day: int
    shift: int
    role: str
    required_count: int
    assigned_count: int
    shortage_count: int
    available_qualified_count: int
    assigned_employee_ids: List[int]
    blocker_counts: Dict[str, int]
    reason_codes: List[str]


@dataclass
class ConstraintBlockerSummary:
    blocker_counts: Dict[str, int]
    employee_ids_by_reason: Dict[str, List[int]]


@dataclass
class DecisionEvidenceSummary:
    evidence_contract_version: int
    source: str
    status: str
    assignment_count: int
    demanded_slot_count: int
    total_demand: int
    total_shortage: int
    objective_priority: List[str]
    objective_components: Dict[str, int]
    blocker_counts: Dict[str, int]
    shortage_reason_codes: List[str]


@dataclass
class SolveResult:
    metrics: SolverMetrics
    assignments: List[Assignment]
    shortages: Dict[Tuple[int, int, str], int]
    violations: List[str]
    constraint_metadata: Dict[str, int]
    objective_metadata: Dict[str, int]
    constraint_records: List[ConstraintRecord]
    fairness_metrics: FairnessMetrics
    objective_breakdown: ObjectiveBreakdown
    shortage_diagnostics: List[SlotCandidateAnalysis]
    demanded_slot_diagnostics: List[SlotCandidateAnalysis]
    assignment_explanations: List[AssignmentExplanation]
    non_assignment_explanations: List[NonAssignmentExplanation]
    shortage_explanations: List[ShortageExplanation]
    constraint_blockers: ConstraintBlockerSummary
    decision_evidence_summary: DecisionEvidenceSummary


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
    if data.hint_assignments:
        solver.parameters.repair_hint = True
        solver.parameters.hint_conflict_limit = 1000

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

    fairness_metrics = compute_fairness_metrics(data, assignments)
    objective_metadata = _objective_metadata(artifacts.constraint_metadata)
    objective_breakdown = compute_objective_breakdown(
        data,
        assignments,
        shortages,
        objective_metadata,
    )
    demanded_slot_diagnostics = compute_demanded_slot_diagnostics(
        data,
        assignments,
        shortages,
    )
    shortage_diagnostics = [
        diagnostic
        for diagnostic in demanded_slot_diagnostics
        if diagnostic.shortage_count > 0
    ]
    assignment_explanations = compute_assignment_explanations(data, assignments)
    non_assignment_explanations = compute_non_assignment_explanations(
        data,
        assignments,
        demanded_slot_diagnostics,
    )
    shortage_explanations = compute_shortage_explanations(
        demanded_slot_diagnostics,
    )
    constraint_blockers = compute_constraint_blockers(demanded_slot_diagnostics)
    decision_evidence_summary = compute_decision_evidence_summary(
        data,
        metrics,
        assignments,
        demanded_slot_diagnostics,
        objective_breakdown,
        constraint_blockers,
        shortage_explanations,
    )
    violations = []
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        violations = validate_solution(
            data,
            assignments,
            shortages,
            fairness_metrics,
        )

    return SolveResult(
        metrics=metrics,
        assignments=assignments,
        shortages=shortages,
        violations=violations,
        constraint_metadata=artifacts.constraint_metadata,
        objective_metadata=objective_metadata,
        constraint_records=artifacts.constraint_records,
        fairness_metrics=fairness_metrics,
        objective_breakdown=objective_breakdown,
        shortage_diagnostics=shortage_diagnostics,
        demanded_slot_diagnostics=demanded_slot_diagnostics,
        assignment_explanations=assignment_explanations,
        non_assignment_explanations=non_assignment_explanations,
        shortage_explanations=shortage_explanations,
        constraint_blockers=constraint_blockers,
        decision_evidence_summary=decision_evidence_summary,
    )


def validate_solution(
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int] | None = None,
    fairness_metrics: FairnessMetrics | None = None,
) -> List[str]:
    errors: List[str] = []
    employee_index: Dict[int, Employee] = {e.employee_id: e for e in data.employees}
    valid_days = set(data.days)
    valid_shifts = set(range(len(data.shifts)))
    valid_roles = set(data.roles)
    expected_shortage_keys = {
        (day, shift, role)
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
    }

    coverage: Dict[Tuple[int, int, str], int] = {}
    daily_work: Dict[Tuple[int, int], int] = {}
    weekly_hours: Dict[int, int] = {e.employee_id: 0 for e in data.employees}
    employee_shift_assignments: Dict[int, List[Tuple[int, int]]] = {
        e.employee_id: [] for e in data.employees
    }

    if shortages is not None:
        for key, value in shortages.items():
            if key not in expected_shortage_keys:
                errors.append(f"Invalid shortage key {key}")
            if not isinstance(value, int):
                errors.append(f"Invalid shortage value for key {key}: {value}")
            elif value < 0:
                errors.append(f"Shortage below zero for key {key}: {value}")

    for assignment in assignments:
        employee = employee_index.get(assignment.employee_id)
        if employee is None:
            errors.append(f"Unknown employee_id {assignment.employee_id}")
        if assignment.day not in valid_days:
            errors.append(f"Unknown day {assignment.day}")
        if assignment.shift not in valid_shifts:
            errors.append(f"Unknown shift {assignment.shift}")
        if assignment.role not in valid_roles:
            errors.append(f"Unknown role {assignment.role}")

        if (
            employee is None
            or assignment.day not in valid_days
            or assignment.shift not in valid_shifts
            or assignment.role not in valid_roles
        ):
            continue

        key = (assignment.day, assignment.shift, assignment.role)
        coverage[key] = coverage.get(key, 0) + 1

        daily_key = (assignment.employee_id, assignment.day)
        daily_work[daily_key] = daily_work.get(daily_key, 0) + 1
        if daily_work[daily_key] > 1:
            errors.append(
                f"Employee {assignment.employee_id} works multiple shifts on day {assignment.day}"
            )

        weekly_hours[assignment.employee_id] += shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            assignment.shift,
        )
        if weekly_hours[assignment.employee_id] > employee.max_weekly_hours:
            errors.append(
                f"Employee {assignment.employee_id} exceeds weekly hours"
            )

        if assignment.role not in employee.roles:
            errors.append(
                f"Employee {assignment.employee_id} assigned to unqualified role {assignment.role}"
            )

        if not _is_available(employee, assignment.day, assignment.shift):
            errors.append(
                f"Employee {assignment.employee_id} assigned outside availability matrix"
            )
        elif not employee.availability[assignment.day][assignment.shift]:
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
                if shortages is None:
                    continue
                reported = shortages.get((day, shift, role))
                if reported is None:
                    errors.append(
                        f"Missing shortage entry for day {day} shift {shift} role {role}"
                    )
                elif not isinstance(reported, int):
                    continue
                elif reported < 0:
                    continue
                elif reported != max(0, shortage_actual):
                    errors.append(
                        f"Shortage mismatch day {day} shift {shift} role {role}: {reported} != {max(0, shortage_actual)}"
                    )

    if fairness_metrics is not None:
        expected_metrics = compute_fairness_metrics(data, assignments)
        if fairness_metrics != expected_metrics:
            errors.append("Fairness metrics do not match assignments")

    return errors


def compute_fairness_metrics(
    data: ProblemData,
    assignments: List[Assignment],
) -> FairnessMetrics:
    employee_ids = [employee.employee_id for employee in data.employees]
    assigned_hours = {employee_id: 0 for employee_id in employee_ids}
    weekend_assignments = {employee_id: 0 for employee_id in employee_ids}
    shift_counts = {
        (employee_id, shift): 0
        for employee_id in employee_ids
        for shift in range(len(data.shifts))
    }
    valid_employee_ids = set(employee_ids)
    valid_days = set(data.days)
    valid_shifts = set(range(len(data.shifts)))

    for assignment in assignments:
        if (
            assignment.employee_id not in valid_employee_ids
            or assignment.day not in valid_days
            or assignment.shift not in valid_shifts
        ):
            continue
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            assignment.shift,
        )
        assigned_hours[assignment.employee_id] += duration
        if assignment.day in (5, 6):
            weekend_assignments[assignment.employee_id] += 1
        shift_counts[(assignment.employee_id, assignment.shift)] += 1

    if assigned_hours:
        min_assigned_hours = min(assigned_hours.values())
        max_assigned_hours = max(assigned_hours.values())
    else:
        min_assigned_hours = 0
        max_assigned_hours = 0

    return FairnessMetrics(
        assigned_hours_per_employee=assigned_hours,
        min_assigned_hours=min_assigned_hours,
        max_assigned_hours=max_assigned_hours,
        workload_spread=max_assigned_hours - min_assigned_hours,
        weekend_assignments_per_employee=weekend_assignments,
        shift_counts_per_employee_shift=shift_counts,
    )


def compute_objective_breakdown(
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int],
    objective_metadata: Dict[str, int],
) -> ObjectiveBreakdown:
    shortage_weight = objective_metadata.get("shortage_priority_weight", 0)
    fairness_weight = objective_metadata.get("fairness_priority_weight", 0)
    total_shortage = sum(shortages.values())
    workload_fairness_value = _workload_fairness_value(data, assignments)
    weekend_fairness_value = _weekend_fairness_value(data, assignments)
    shift_distribution_fairness_value = _shift_distribution_fairness_value(
        data,
        assignments,
    )
    raw_fairness_value = (
        workload_fairness_value
        + weekend_fairness_value
        + shift_distribution_fairness_value
    )
    labor_cost_value = _labor_cost_value(data, assignments)
    shortage_objective_value = total_shortage * shortage_weight
    fairness_objective_value = raw_fairness_value * fairness_weight

    return ObjectiveBreakdown(
        total_shortage=total_shortage,
        shortage_objective_value=shortage_objective_value,
        workload_fairness_value=workload_fairness_value,
        weekend_fairness_value=weekend_fairness_value,
        shift_distribution_fairness_value=shift_distribution_fairness_value,
        fairness_objective_value=fairness_objective_value,
        labor_cost_value=labor_cost_value,
        total_objective_value=(
            shortage_objective_value
            + fairness_objective_value
            + labor_cost_value
        ),
    )


def compute_demanded_slot_diagnostics(
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int],
) -> List[SlotCandidateAnalysis]:
    assigned_by_slot = _assignments_by_slot(assignments)
    assigned_by_employee = _assignments_by_employee(assignments)
    weekly_hours = _assigned_hours_by_employee(data, assignments)
    diagnostics: List[SlotCandidateAnalysis] = []

    for day in data.days:
        for shift in range(len(data.shifts)):
            for role in data.roles:
                required = data.demand[day][shift][role]
                if required <= 0:
                    continue
                assigned = assigned_by_slot.get((day, shift, role), [])
                assigned_employee_ids = sorted(
                    assignment.employee_id for assignment in assigned
                )
                blocked_by_reason: Dict[str, List[int]] = {}
                could_work_employee_ids: List[int] = []
                candidate_employee_ids: List[int] = []

                for employee in data.employees:
                    reasons = _blocking_reasons_for_slot(
                        data,
                        employee,
                        day,
                        shift,
                        role,
                        assigned_by_employee,
                        weekly_hours,
                    )
                    if "missing_role" not in reasons and "unavailable" not in reasons:
                        could_work_employee_ids.append(employee.employee_id)
                    if not reasons:
                        candidate_employee_ids.append(employee.employee_id)
                    for reason in reasons:
                        blocked_by_reason.setdefault(reason, []).append(
                            employee.employee_id
                        )

                diagnostics.append(
                    SlotCandidateAnalysis(
                        day=day,
                        shift=shift,
                        role=role,
                        required_count=required,
                        assigned_count=len(assigned),
                        shortage_count=shortages.get((day, shift, role), 0),
                        candidate_employee_count=len(candidate_employee_ids),
                        assigned_employee_ids=assigned_employee_ids,
                        could_work_employee_ids=sorted(could_work_employee_ids),
                        role_available_employee_ids=sorted(could_work_employee_ids),
                        currently_assignable_employee_ids=sorted(
                            candidate_employee_ids
                        ),
                        blocked_employee_ids_by_reason={
                            reason: sorted(employee_ids)
                            for reason, employee_ids in blocked_by_reason.items()
                        },
                    )
                )

    return diagnostics


def compute_assignment_explanations(
    data: ProblemData,
    assignments: List[Assignment],
) -> List[AssignmentExplanation]:
    # Solver-produced assignments are expected to reference valid employees and
    # shifts. validate_solution remains the defensive entry point for malformed
    # external assignment lists.
    employee_index = {employee.employee_id: employee for employee in data.employees}
    assigned_by_employee = _assignments_by_employee(assignments)
    weekly_hours = _assigned_hours_by_employee(data, assignments)
    explanations: List[AssignmentExplanation] = []

    for assignment in assignments:
        employee = employee_index[assignment.employee_id]
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            assignment.shift,
        )
        employee_assignments_without_current = [
            existing
            for existing in assigned_by_employee[assignment.employee_id]
            if existing != assignment
        ]
        explanations.append(
            AssignmentExplanation(
                employee_id=assignment.employee_id,
                day=assignment.day,
                shift=assignment.shift,
                role=assignment.role,
                shift_duration=duration,
                labor_cost_contribution=employee.hourly_cost * duration,
                employee_weekly_hours=weekly_hours[assignment.employee_id],
                available=_is_available(employee, assignment.day, assignment.shift)
                and employee.availability[assignment.day][assignment.shift],
                qualified=assignment.role in employee.roles,
                within_weekly_hours=(
                    weekly_hours[assignment.employee_id] <= employee.max_weekly_hours
                ),
                rest_compatible=_minimum_rest_compatible(
                    data,
                    employee_assignments_without_current,
                    assignment.day,
                    assignment.shift,
                ),
                reason_codes=_assignment_reason_codes(
                    available=(
                        _is_available(employee, assignment.day, assignment.shift)
                        and employee.availability[assignment.day][assignment.shift]
                    ),
                    qualified=assignment.role in employee.roles,
                    within_weekly_hours=(
                        weekly_hours[assignment.employee_id] <= employee.max_weekly_hours
                    ),
                    rest_compatible=_minimum_rest_compatible(
                        data,
                        employee_assignments_without_current,
                        assignment.day,
                        assignment.shift,
                    ),
                ),
            )
        )

    return explanations


def compute_non_assignment_explanations(
    data: ProblemData,
    assignments: List[Assignment],
    demanded_slot_diagnostics: List[SlotCandidateAnalysis],
) -> List[NonAssignmentExplanation]:
    assigned_by_employee = _assignments_by_employee(assignments)
    weekly_hours = _assigned_hours_by_employee(data, assignments)
    employee_index = {employee.employee_id: employee for employee in data.employees}
    explanations: List[NonAssignmentExplanation] = []

    for diagnostic in demanded_slot_diagnostics:
        assigned_employee_ids = set(diagnostic.assigned_employee_ids)
        selected_costs = [
            employee_index[employee_id].hourly_cost
            for employee_id in diagnostic.assigned_employee_ids
            if employee_id in employee_index
        ]
        selected_min_cost = min(selected_costs) if selected_costs else None

        for employee in sorted(data.employees, key=lambda item: item.employee_id):
            if employee.employee_id in assigned_employee_ids:
                continue
            blocker_reasons = _blocking_reasons_for_slot(
                data,
                employee,
                diagnostic.day,
                diagnostic.shift,
                diagnostic.role,
                assigned_by_employee,
                weekly_hours,
            )
            reason_codes = _blocker_reason_codes(blocker_reasons)
            if (
                not reason_codes
                and selected_min_cost is not None
                and employee.hourly_cost > selected_min_cost
            ):
                reason_codes = [BLOCKED_HIGHER_COST_THAN_SELECTED]
            if not reason_codes:
                reason_codes = [NOT_SELECTED_BY_FINAL_OBJECTIVE]
            explanations.append(
                NonAssignmentExplanation(
                    employee_id=employee.employee_id,
                    day=diagnostic.day,
                    shift=diagnostic.shift,
                    role=diagnostic.role,
                    assigned_employee_ids=list(diagnostic.assigned_employee_ids),
                    reason_codes=reason_codes,
                )
            )

    return explanations


def compute_shortage_explanations(
    demanded_slot_diagnostics: List[SlotCandidateAnalysis],
) -> List[ShortageExplanation]:
    explanations: List[ShortageExplanation] = []
    for diagnostic in demanded_slot_diagnostics:
        if diagnostic.shortage_count <= 0:
            continue
        blocker_counts = _mapped_blocker_counts(diagnostic)
        reason_codes = _shortage_reason_codes(diagnostic, blocker_counts)
        explanations.append(
            ShortageExplanation(
                day=diagnostic.day,
                shift=diagnostic.shift,
                role=diagnostic.role,
                required_count=diagnostic.required_count,
                assigned_count=diagnostic.assigned_count,
                shortage_count=diagnostic.shortage_count,
                available_qualified_count=len(diagnostic.role_available_employee_ids),
                assigned_employee_ids=list(diagnostic.assigned_employee_ids),
                blocker_counts=blocker_counts,
                reason_codes=reason_codes,
            )
        )
    return explanations


def compute_constraint_blockers(
    demanded_slot_diagnostics: List[SlotCandidateAnalysis],
) -> ConstraintBlockerSummary:
    counts: Dict[str, int] = {}
    employee_ids_by_reason: Dict[str, set[int]] = {}
    for diagnostic in demanded_slot_diagnostics:
        for lowercase_reason, employee_ids in (
            diagnostic.blocked_employee_ids_by_reason.items()
        ):
            reason_code = BLOCKER_REASON_CODE_MAP[lowercase_reason]
            counts[reason_code] = counts.get(reason_code, 0) + len(employee_ids)
            employee_ids_by_reason.setdefault(reason_code, set()).update(employee_ids)

    return ConstraintBlockerSummary(
        blocker_counts=dict(sorted(counts.items())),
        employee_ids_by_reason={
            reason_code: sorted(employee_ids)
            for reason_code, employee_ids in sorted(employee_ids_by_reason.items())
        },
    )


def compute_decision_evidence_summary(
    data: ProblemData,
    metrics: SolverMetrics,
    assignments: List[Assignment],
    demanded_slot_diagnostics: List[SlotCandidateAnalysis],
    objective_breakdown: ObjectiveBreakdown,
    constraint_blockers: ConstraintBlockerSummary,
    shortage_explanations: List[ShortageExplanation],
) -> DecisionEvidenceSummary:
    shortage_reason_codes = sorted(
        {
            reason_code
            for explanation in shortage_explanations
            for reason_code in explanation.reason_codes
        }
    )
    return DecisionEvidenceSummary(
        evidence_contract_version=1,
        source="cp_sat_solver_post_solve_evidence",
        status=metrics.status,
        assignment_count=len(assignments),
        demanded_slot_count=len(demanded_slot_diagnostics),
        total_demand=sum(
            data.demand[day][shift][role]
            for day in data.days
            for shift in range(len(data.shifts))
            for role in data.roles
        ),
        total_shortage=objective_breakdown.total_shortage,
        objective_priority=[
            "MINIMIZE_TOTAL_SHORTAGE",
            "MINIMIZE_FAIRNESS_PENALTY",
            "MINIMIZE_LABOR_COST",
        ],
        objective_components={
            "shortage_objective_value": (
                objective_breakdown.shortage_objective_value
            ),
            "fairness_objective_value": (
                objective_breakdown.fairness_objective_value
            ),
            "labor_cost_value": objective_breakdown.labor_cost_value,
            "total_objective_value": objective_breakdown.total_objective_value,
        },
        blocker_counts=dict(constraint_blockers.blocker_counts),
        shortage_reason_codes=shortage_reason_codes,
    )


def _blocking_reasons_for_slot(
    data: ProblemData,
    employee: Employee,
    day: int,
    shift: int,
    role: str,
    assigned_by_employee: Dict[int, List[Assignment]],
    weekly_hours: Dict[int, int],
) -> List[str]:
    reasons: List[str] = []
    employee_assignments = assigned_by_employee.get(employee.employee_id, [])
    assigned_same_slot = any(
        assignment.day == day
        and assignment.shift == shift
        and assignment.role == role
        for assignment in employee_assignments
    )

    if role not in employee.roles:
        reasons.append("missing_role")
    if not _is_available(employee, day, shift) or not employee.availability[day][shift]:
        reasons.append("unavailable")

    if not assigned_same_slot:
        if any(assignment.day == day for assignment in employee_assignments):
            reasons.append("already_working_that_day")
        if not _minimum_rest_compatible(data, employee_assignments, day, shift):
            reasons.append("violates_minimum_rest")
        if not _closing_to_opening_compatible(data, employee_assignments, day, shift):
            reasons.append("violates_closing_to_opening")
        if not _max_consecutive_days_compatible(
            data,
            employee_assignments,
            day,
        ):
            reasons.append("violates_max_consecutive_days")
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            shift,
        )
        if weekly_hours.get(employee.employee_id, 0) + duration > employee.max_weekly_hours:
            reasons.append("exceeds_weekly_hours")

    return reasons


def _assignment_reason_codes(
    *,
    available: bool,
    qualified: bool,
    within_weekly_hours: bool,
    rest_compatible: bool,
) -> List[str]:
    reason_codes: List[str] = []
    if available:
        reason_codes.append(ASSIGNED_AVAILABLE)
    if qualified:
        reason_codes.append(ASSIGNED_QUALIFIED)
    if within_weekly_hours:
        reason_codes.append(ASSIGNED_WITHIN_HOURS)
    if rest_compatible:
        reason_codes.append(ASSIGNED_REST_COMPATIBLE)
    reason_codes.append(ASSIGNED_COVERED_DEMAND)
    reason_codes.append(ASSIGNED_COST_CONTRIBUTION)
    return reason_codes


def _blocker_reason_codes(blocker_reasons: List[str]) -> List[str]:
    return [
        BLOCKER_REASON_CODE_MAP[reason]
        for reason in blocker_reasons
        if reason in BLOCKER_REASON_CODE_MAP
    ]


def _mapped_blocker_counts(diagnostic: SlotCandidateAnalysis) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for lowercase_reason, employee_ids in diagnostic.blocked_employee_ids_by_reason.items():
        reason_code = BLOCKER_REASON_CODE_MAP[lowercase_reason]
        counts[reason_code] = counts.get(reason_code, 0) + len(employee_ids)
    return dict(sorted(counts.items()))


def _shortage_reason_codes(
    diagnostic: SlotCandidateAnalysis,
    blocker_counts: Dict[str, int],
) -> List[str]:
    reason_codes: List[str] = []
    available_qualified_count = len(diagnostic.role_available_employee_ids)
    if available_qualified_count < diagnostic.required_count:
        reason_codes.append(SHORTAGE_INSUFFICIENT_AVAILABLE_QUALIFIED)
    if diagnostic.required_count > diagnostic.candidate_employee_count:
        reason_codes.append(SHORTAGE_DEMAND_EXCEEDS_CAPACITY)
    if blocker_counts.get(BLOCKED_REST_RULE, 0) or blocker_counts.get(
        BLOCKED_CLOSING_TO_OPENING,
        0,
    ):
        reason_codes.append(SHORTAGE_REST_CONFLICT)
    if blocker_counts.get(BLOCKED_MAX_HOURS, 0):
        reason_codes.append(SHORTAGE_MAX_HOURS_LIMIT)
    return reason_codes


def _assignments_by_slot(
    assignments: List[Assignment],
) -> Dict[Tuple[int, int, str], List[Assignment]]:
    by_slot: Dict[Tuple[int, int, str], List[Assignment]] = {}
    for assignment in assignments:
        by_slot.setdefault(
            (assignment.day, assignment.shift, assignment.role),
            [],
        ).append(assignment)
    return by_slot


def _assignments_by_employee(
    assignments: List[Assignment],
) -> Dict[int, List[Assignment]]:
    by_employee: Dict[int, List[Assignment]] = {}
    for assignment in assignments:
        by_employee.setdefault(assignment.employee_id, []).append(assignment)
    return by_employee


def _minimum_rest_compatible(
    data: ProblemData,
    assignments: List[Assignment],
    day: int,
    shift: int,
) -> bool:
    windows = [
        (
            *shift_start_end(
                data.shift_start_hours,
                data.shift_end_hours,
                assignment.day,
                assignment.shift,
            ),
            assignment.day,
            assignment.shift,
        )
        for assignment in assignments
    ]
    start, end = shift_start_end(
        data.shift_start_hours,
        data.shift_end_hours,
        day,
        shift,
    )
    windows.append((start, end, day, shift))
    windows.sort(key=lambda item: item[0])
    for idx in range(1, len(windows)):
        _prev_start, prev_end, _prev_day, _prev_shift = windows[idx - 1]
        current_start, _current_end, _current_day, _current_shift = windows[idx]
        if current_start - prev_end < data.min_rest_hours:
            return False
    return True


def _closing_to_opening_compatible(
    data: ProblemData,
    assignments: List[Assignment],
    day: int,
    shift: int,
) -> bool:
    opening_index, closing_index = opening_closing_shift_indices(
        data.shift_start_hours,
        data.shift_end_hours,
    )
    assigned_slots = {(assignment.day, assignment.shift) for assignment in assignments}
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
    assignments: List[Assignment],
    day: int,
) -> bool:
    worked_days = sorted({assignment.day for assignment in assignments} | {day})
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


def _objective_metadata(metadata: Dict[str, int]) -> Dict[str, int]:
    objective_keys = {
        "shortage_priority_weight",
        "fairness_priority_weight",
        "labor_cost_component_upper_bound",
        "workload_fairness_component_upper_bound",
        "weekend_fairness_component_upper_bound",
        "shift_distribution_fairness_component_upper_bound",
    }
    return {key: metadata[key] for key in objective_keys if key in metadata}


def _workload_fairness_value(
    data: ProblemData,
    assignments: List[Assignment],
) -> int:
    relevant_employee_ids = _employees_with_feasible_assignment_vars(data)
    if len(relevant_employee_ids) < 2:
        return 0
    assigned_hours = _assigned_hours_by_employee(data, assignments)
    values = [assigned_hours[employee_id] for employee_id in relevant_employee_ids]
    return max(values) - min(values)


def _weekend_fairness_value(
    data: ProblemData,
    assignments: List[Assignment],
) -> int:
    weekend_days = {day for day in data.days if day in (5, 6)}
    if not weekend_days:
        return 0
    relevant_employee_ids = _employees_with_feasible_assignment_vars(
        data,
        allowed_days=weekend_days,
    )
    if len(relevant_employee_ids) < 2:
        return 0
    counts = {employee_id: 0 for employee_id in relevant_employee_ids}
    for assignment in assignments:
        if (
            assignment.employee_id in counts
            and assignment.day in weekend_days
        ):
            counts[assignment.employee_id] += 1
    return max(counts.values()) - min(counts.values())


def _shift_distribution_fairness_value(
    data: ProblemData,
    assignments: List[Assignment],
) -> int:
    total_spread = 0
    for shift in range(len(data.shifts)):
        relevant_employee_ids = _employees_with_feasible_assignment_vars(
            data,
            allowed_shifts={shift},
        )
        if len(relevant_employee_ids) < 2:
            continue
        counts = {employee_id: 0 for employee_id in relevant_employee_ids}
        for assignment in assignments:
            if (
                assignment.employee_id in counts
                and assignment.shift == shift
            ):
                counts[assignment.employee_id] += 1
        total_spread += max(counts.values()) - min(counts.values())
    return total_spread


def _labor_cost_value(data: ProblemData, assignments: List[Assignment]) -> int:
    employee_index = {employee.employee_id: employee for employee in data.employees}
    total = 0
    for assignment in assignments:
        employee = employee_index.get(assignment.employee_id)
        if employee is None or not 0 <= assignment.shift < len(data.shifts):
            continue
        duration = shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            assignment.shift,
        )
        total += employee.hourly_cost * duration
    return total


def _assigned_hours_by_employee(
    data: ProblemData,
    assignments: List[Assignment],
) -> Dict[int, int]:
    assigned_hours = {employee.employee_id: 0 for employee in data.employees}
    valid_employee_ids = set(assigned_hours)
    for assignment in assignments:
        if (
            assignment.employee_id not in valid_employee_ids
            or assignment.shift < 0
            or assignment.shift >= len(data.shifts)
        ):
            continue
        assigned_hours[assignment.employee_id] += shift_duration_hours(
            data.shift_start_hours,
            data.shift_end_hours,
            assignment.shift,
        )
    return assigned_hours


def _employees_with_feasible_assignment_vars(
    data: ProblemData,
    allowed_days: set[int] | None = None,
    allowed_shifts: set[int] | None = None,
) -> List[int]:
    employee_ids: List[int] = []
    for employee in data.employees:
        has_variable = False
        for day in data.days:
            if allowed_days is not None and day not in allowed_days:
                continue
            if day < 0 or day >= len(employee.availability):
                continue
            for shift in range(len(data.shifts)):
                if allowed_shifts is not None and shift not in allowed_shifts:
                    continue
                if shift >= len(employee.availability[day]):
                    continue
                if employee.availability[day][shift] and _can_work_any_role(
                    employee,
                    data,
                ):
                    has_variable = True
                    break
            if has_variable:
                break
        if has_variable:
            employee_ids.append(employee.employee_id)
    return employee_ids


def _can_work_any_role(employee: Employee, data: ProblemData) -> bool:
    return any(role in data.roles for role in employee.roles)


def _is_available(employee: Employee, day: int, shift: int) -> bool:
    return (
        0 <= day < len(employee.availability)
        and 0 <= shift < len(employee.availability[day])
    )


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
