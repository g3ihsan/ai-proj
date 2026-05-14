from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Mapping

from .schemas import RESPONSE_MODE_DEBUG, solve_payload, solve_result_to_payload
from .solve import SolveResult


EVIDENCE_FIELDS = {
    "assignment_explanations",
    "non_assignment_explanations",
    "shortage_explanations",
    "constraint_blockers",
    "decision_evidence_summary",
}


class ExplanationError(ValueError):
    pass


class ExplanationQueryError(ExplanationError):
    pass


class ExplanationTargetNotFoundError(ExplanationError):
    pass


def debug_payload_from_solve_result(result: SolveResult) -> Dict[str, Any]:
    return solve_result_to_payload(result, response_mode=RESPONSE_MODE_DEBUG)


def explain_summary(source: SolveResult | Mapping[str, Any]) -> Dict[str, Any]:
    result = _debug_result_payload(source)
    summary = result["decision_evidence_summary"]
    metrics = result.get("metrics", {})
    objective = result.get("objective_breakdown", {})
    assignment_count = int(summary.get("assignment_count", 0))
    total_shortage = int(summary.get("total_shortage", 0))

    return _explanation(
        explanation_type="summary_explanation",
        status=str(metrics.get("status", summary.get("status", ""))),
        title="Roster explanation summary",
        message=(
            f"The solver assigned {assignment_count} shifts with "
            f"{total_shortage} total shortages."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=list(summary.get("shortage_reason_codes", [])),
        details={
            "assignment_count": assignment_count,
            "total_shortage": total_shortage,
            "total_demand": summary.get("total_demand", 0),
            "demanded_slot_count": summary.get("demanded_slot_count", 0),
            "objective_priority": list(summary.get("objective_priority", [])),
            "objective_components": dict(summary.get("objective_components", {})),
            "blocker_counts": dict(summary.get("blocker_counts", {})),
            "objective_breakdown": dict(objective),
        },
        recommended_next_checks=_summary_next_checks(total_shortage),
    )


def explain_shortages(source: SolveResult | Mapping[str, Any]) -> Dict[str, Any]:
    result = _debug_result_payload(source)
    shortages = [
        _shortage_detail(explanation)
        for explanation in result.get("shortage_explanations", [])
    ]
    total_shortage = sum(int(item["shortage_count"]) for item in shortages)
    reason_codes = _sorted_reason_codes(
        reason_code
        for shortage in shortages
        for reason_code in shortage["reason_codes"]
    )

    return _explanation(
        explanation_type="shortage_explanations",
        status=str(result.get("metrics", {}).get("status", "")),
        title="Shortage explanation",
        message=(
            "No staffing shortages remain."
            if not shortages
            else f"The solver found {total_shortage} unfilled demanded slots."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=reason_codes,
        details={"shortages": shortages, "total_shortage": total_shortage},
        recommended_next_checks=(
            []
            if not shortages
            else [
                "Review blocker_counts for unavailable, role, rest, and hours constraints.",
                "Use shortage rows to decide whether demand, availability, or staffing data should change.",
            ]
        ),
    )


def explain_assignment(
    source: SolveResult | Mapping[str, Any],
    *,
    employee_id: int,
    day: int,
    shift: int,
    role: str,
) -> Dict[str, Any]:
    result = _debug_result_payload(source)
    assignment = _find_assignment_explanation(
        result,
        employee_id=employee_id,
        day=day,
        shift=shift,
        role=role,
        raise_if_missing=False,
    )
    if assignment is None:
        return _explain_non_assignment(
            result,
            employee_id=employee_id,
            day=day,
            shift=shift,
            role=role,
        )

    return _explanation(
        explanation_type="assignment_explanation",
        status=str(result.get("metrics", {}).get("status", "")),
        title="Assignment explanation",
        message=(
            f"Employee {employee_id} was assigned to day {day} shift {shift} "
            f"as {role}."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=list(assignment.get("reason_codes", [])),
        details={
            "assigned": True,
            "assignment": {
                "employee_id": employee_id,
                "day": day,
                "shift": shift,
                "role": role,
            },
            "shift_duration": assignment.get("shift_duration"),
            "labor_cost_contribution": assignment.get("labor_cost_contribution"),
            "employee_weekly_hours": assignment.get("employee_weekly_hours"),
            "validation_facts": {
                "available": assignment.get("available"),
                "qualified": assignment.get("qualified"),
                "within_weekly_hours": assignment.get("within_weekly_hours"),
                "rest_compatible": assignment.get("rest_compatible"),
            },
        },
        recommended_next_checks=[],
        extra_fields={"assigned": True},
    )


def explain_employee(
    source: SolveResult | Mapping[str, Any],
    *,
    employee_id: int,
) -> Dict[str, Any]:
    result = _debug_result_payload(source)
    assignments = [
        explanation
        for explanation in result.get("assignment_explanations", [])
        if int(explanation["employee_id"]) == employee_id
    ]
    non_assignments = [
        explanation
        for explanation in result.get("non_assignment_explanations", [])
        if int(explanation["employee_id"]) == employee_id
    ]
    if not assignments and not non_assignments:
        raise ExplanationTargetNotFoundError(
            f"No explanation evidence found for employee_id {employee_id}"
        )

    reason_codes = _sorted_reason_codes(
        reason_code
        for explanation in [*assignments, *non_assignments]
        for reason_code in explanation.get("reason_codes", [])
    )

    return _explanation(
        explanation_type="employee_explanation",
        status=str(result.get("metrics", {}).get("status", "")),
        title="Employee explanation",
        message=(
            f"Employee {employee_id} has {len(assignments)} assignments and "
            f"{len(non_assignments)} non-assignment explanations."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=reason_codes,
        details={
            "employee_id": employee_id,
            "assignments": [_assignment_detail(item) for item in assignments],
            "non_assignments": [
                _non_assignment_detail(item) for item in non_assignments
            ],
        },
        recommended_next_checks=_employee_next_checks(non_assignments),
    )


def explain_shift(
    source: SolveResult | Mapping[str, Any],
    *,
    day: int,
    shift: int,
    role: str | None = None,
) -> Dict[str, Any]:
    result = _debug_result_payload(source)
    assignment_explanations = [
        explanation
        for explanation in result.get("assignment_explanations", [])
        if int(explanation["day"]) == day
        and int(explanation["shift"]) == shift
        and (role is None or str(explanation["role"]) == role)
    ]
    non_assignments = [
        explanation
        for explanation in result.get("non_assignment_explanations", [])
        if int(explanation["day"]) == day
        and int(explanation["shift"]) == shift
        and (role is None or str(explanation["role"]) == role)
    ]
    shortages = [
        explanation
        for explanation in result.get("shortage_explanations", [])
        if int(explanation["day"]) == day
        and int(explanation["shift"]) == shift
        and (role is None or str(explanation["role"]) == role)
    ]
    demanded_slots = [
        diagnostic
        for diagnostic in result.get("demanded_slot_diagnostics", [])
        if int(diagnostic["day"]) == day
        and int(diagnostic["shift"]) == shift
        and (role is None or str(diagnostic["role"]) == role)
    ]
    if not demanded_slots:
        raise ExplanationTargetNotFoundError(
            f"No demanded slot evidence found for day {day} shift {shift}"
            + ("" if role is None else f" role {role}")
        )

    reason_codes = _sorted_reason_codes(
        reason_code
        for explanation in [*assignment_explanations, *non_assignments, *shortages]
        for reason_code in explanation.get("reason_codes", [])
    )
    shortage_count = sum(int(item.get("shortage_count", 0)) for item in shortages)

    return _explanation(
        explanation_type="shift_explanation",
        status=str(result.get("metrics", {}).get("status", "")),
        title="Shift explanation",
        message=(
            f"Day {day} shift {shift} has {len(assignment_explanations)} "
            f"assignments and {shortage_count} shortages."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=reason_codes,
        details={
            "day": day,
            "shift": shift,
            "role": role,
            "demanded_slots": demanded_slots,
            "assignments": [
                _assignment_detail(item) for item in assignment_explanations
            ],
            "non_assignments": [
                _non_assignment_detail(item) for item in non_assignments
            ],
            "shortages": [_shortage_detail(item) for item in shortages],
        },
        recommended_next_checks=(
            []
            if shortage_count == 0
            else ["Review shortage blocker counts for this shift."]
        ),
    )


def solve_request_to_explanation_payload(
    solve_request_payload: Mapping[str, Any],
    explainer: Callable[..., Dict[str, Any]],
    *,
    target: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    response_payload = solve_payload(_debug_solve_request_payload(solve_request_payload))
    if not response_payload.get("ok", False):
        return response_payload
    return {
        "ok": True,
        "result": explainer(response_payload["result"], **dict(target or {})),
    }


def _debug_result_payload(source: SolveResult | Mapping[str, Any]) -> Dict[str, Any]:
    if isinstance(source, SolveResult):
        return debug_payload_from_solve_result(source)
    result = dict(source)
    missing_fields = sorted(EVIDENCE_FIELDS - set(result))
    if missing_fields:
        raise ExplanationError(
            "Explanation helpers require a debug solve result payload with "
            f"Solver Evidence Layer fields; missing {missing_fields}"
        )
    return result


def _debug_solve_request_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    solve_request = deepcopy(dict(payload))
    options = dict(solve_request.get("options", {}))
    options["response_mode"] = RESPONSE_MODE_DEBUG
    solve_request["options"] = options
    return solve_request


def _explanation(
    *,
    explanation_type: str,
    status: str,
    title: str,
    message: str,
    evidence_contract_version: int,
    reason_codes: list[str],
    details: Dict[str, Any],
    recommended_next_checks: list[str],
    extra_fields: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload = {
        "type": explanation_type,
        "status": status,
        "title": title,
        "message": message,
        "evidence_contract_version": evidence_contract_version,
        "reason_codes": _sorted_reason_codes(reason_codes),
        "details": details,
        "recommended_next_checks": list(recommended_next_checks),
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _evidence_contract_version(result: Mapping[str, Any]) -> int:
    summary = result.get("decision_evidence_summary", {})
    return int(summary.get("evidence_contract_version", 1))


def _find_assignment_explanation(
    result: Mapping[str, Any],
    *,
    employee_id: int,
    day: int,
    shift: int,
    role: str,
    raise_if_missing: bool = True,
) -> Mapping[str, Any] | None:
    for explanation in result.get("assignment_explanations", []):
        if (
            int(explanation["employee_id"]) == employee_id
            and int(explanation["day"]) == day
            and int(explanation["shift"]) == shift
            and str(explanation["role"]) == role
        ):
            return explanation
    if not raise_if_missing:
        return None
    raise ExplanationTargetNotFoundError(
        "No assignment explanation found for "
        f"employee {employee_id}, day {day}, shift {shift}, role {role}"
    )


def _explain_non_assignment(
    result: Mapping[str, Any],
    *,
    employee_id: int,
    day: int,
    shift: int,
    role: str,
) -> Dict[str, Any]:
    non_assignment = _find_non_assignment_explanation(
        result,
        employee_id=employee_id,
        day=day,
        shift=shift,
        role=role,
    )
    assigned_employee_ids = list(non_assignment.get("assigned_employee_ids", []))
    reason_codes = list(non_assignment.get("reason_codes", []))
    return _explanation(
        explanation_type="non_assignment_explanation",
        status=str(result.get("metrics", {}).get("status", "")),
        title="Non-assignment explanation",
        message=(
            f"Employee {employee_id} was not assigned to day {day} shift {shift} "
            f"as {role}."
        ),
        evidence_contract_version=_evidence_contract_version(result),
        reason_codes=reason_codes,
        details={
            "assigned": False,
            "assignment": {
                "employee_id": employee_id,
                "day": day,
                "shift": shift,
                "role": role,
            },
            "assigned_employee_ids": assigned_employee_ids,
            "blocker_details": {
                "reason_codes": reason_codes,
                "assigned_employee_ids": assigned_employee_ids,
            },
        },
        recommended_next_checks=[
            "Review reason_codes to understand why this employee was not selected."
        ],
        extra_fields={"assigned": False},
    )


def _find_non_assignment_explanation(
    result: Mapping[str, Any],
    *,
    employee_id: int,
    day: int,
    shift: int,
    role: str,
) -> Mapping[str, Any]:
    for explanation in result.get("non_assignment_explanations", []):
        if (
            int(explanation["employee_id"]) == employee_id
            and int(explanation["day"]) == day
            and int(explanation["shift"]) == shift
            and str(explanation["role"]) == role
        ):
            return explanation
    raise ExplanationTargetNotFoundError(
        "No assignment or non-assignment explanation found for "
        f"employee {employee_id}, day {day}, shift {shift}, role {role}"
    )


def _assignment_detail(explanation: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "employee_id": int(explanation["employee_id"]),
        "day": int(explanation["day"]),
        "shift": int(explanation["shift"]),
        "role": str(explanation["role"]),
        "shift_duration": explanation.get("shift_duration"),
        "labor_cost_contribution": explanation.get("labor_cost_contribution"),
        "employee_weekly_hours": explanation.get("employee_weekly_hours"),
        "reason_codes": list(explanation.get("reason_codes", [])),
    }


def _non_assignment_detail(explanation: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "employee_id": int(explanation["employee_id"]),
        "day": int(explanation["day"]),
        "shift": int(explanation["shift"]),
        "role": str(explanation["role"]),
        "assigned_employee_ids": list(explanation.get("assigned_employee_ids", [])),
        "reason_codes": list(explanation.get("reason_codes", [])),
    }


def _shortage_detail(explanation: Mapping[str, Any]) -> Dict[str, Any]:
    shortage_count = int(explanation["shortage_count"])
    role = str(explanation["role"])
    return {
        "day": int(explanation["day"]),
        "shift": int(explanation["shift"]),
        "role": role,
        "required_count": int(explanation["required_count"]),
        "assigned_count": int(explanation["assigned_count"]),
        "shortage_count": shortage_count,
        "available_qualified_count": int(explanation["available_qualified_count"]),
        "assigned_employee_ids": list(explanation.get("assigned_employee_ids", [])),
        "blocker_counts": dict(explanation.get("blocker_counts", {})),
        "reason_codes": list(explanation.get("reason_codes", [])),
        "message": (
            f"Day {explanation['day']} shift {explanation['shift']} role {role} "
            f"has {shortage_count} unfilled slot(s)."
        ),
    }


def _summary_next_checks(total_shortage: int) -> list[str]:
    if total_shortage <= 0:
        return []
    return [
        "Review shortage explanations for slots with unfilled demand.",
        "Check whether availability, role coverage, rest windows, or weekly hours caused blockers.",
    ]


def _employee_next_checks(non_assignments: list[Mapping[str, Any]]) -> list[str]:
    if not non_assignments:
        return []
    return ["Review non-assignment reason codes for blocked or unselected slots."]


def _sorted_reason_codes(reason_codes) -> list[str]:
    return sorted({str(reason_code) for reason_code in reason_codes})
