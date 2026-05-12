from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Tuple

from .data import Employee, ProblemData
from .solve import (
    Assignment,
    AssignmentExplanation,
    SlotCandidateAnalysis,
    SolveResult,
    solve,
)


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class SolveOptions:
    time_limit_sec: float = 10.0
    seed: int = 1


@dataclass(frozen=True)
class SolveRequest:
    problem: ProblemData
    options: SolveOptions


def solve_request_to_payload(
    data: ProblemData,
    *,
    time_limit_sec: float = 10.0,
    seed: int = 1,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "problem": problem_data_to_payload(data),
        "options": asdict(SolveOptions(time_limit_sec=time_limit_sec, seed=seed)),
    }


def parse_solve_request(payload: Mapping[str, Any]) -> SolveRequest:
    problem_payload = payload.get("problem")
    if not isinstance(problem_payload, Mapping):
        raise ValueError("Solve request must contain a problem object")

    options_payload = payload.get("options", {})
    if not isinstance(options_payload, Mapping):
        raise ValueError("Solve request options must be an object")

    options = SolveOptions(
        time_limit_sec=float(options_payload.get("time_limit_sec", 10.0)),
        seed=int(options_payload.get("seed", 1)),
    )
    return SolveRequest(
        problem=problem_data_from_payload(problem_payload),
        options=options,
    )


def solve_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        request = parse_solve_request(payload)
        result = solve(
            request.problem,
            time_limit_sec=request.options.time_limit_sec,
            seed=request.options.seed,
        )
    except Exception as exc:
        return error_payload(exc)

    return {
        "ok": True,
        "result": solve_result_to_payload(result),
    }


def error_payload(exc: Exception) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }


def problem_data_to_payload(data: ProblemData) -> Dict[str, Any]:
    return {
        "employees": [
            {
                "employee_id": employee.employee_id,
                "name": employee.name,
                "roles": list(employee.roles),
                "hourly_cost": employee.hourly_cost,
                "max_weekly_hours": employee.max_weekly_hours,
                "availability": [list(day) for day in employee.availability],
            }
            for employee in sorted(data.employees, key=lambda item: item.employee_id)
        ],
        "roles": list(data.roles),
        "days": list(data.days),
        "shifts": list(data.shifts),
        "shift_start_hours": list(data.shift_start_hours),
        "shift_end_hours": list(data.shift_end_hours),
        "min_rest_hours": data.min_rest_hours,
        "max_consecutive_days": data.max_consecutive_days,
        "shortage_penalty": data.shortage_penalty,
        "demand": _demand_records(data),
        "hint_assignments": _hint_assignment_records(data.hint_assignments),
    }


def problem_data_from_payload(payload: Mapping[str, Any]) -> ProblemData:
    roles = [str(role) for role in payload["roles"]]
    days = [int(day) for day in payload["days"]]
    shifts = [str(shift) for shift in payload["shifts"]]
    employees = [
        Employee(
            employee_id=int(employee["employee_id"]),
            name=str(employee["name"]),
            roles=tuple(str(role) for role in employee["roles"]),
            hourly_cost=int(employee["hourly_cost"]),
            max_weekly_hours=int(employee["max_weekly_hours"]),
            availability=[
                [bool(available) for available in day]
                for day in employee["availability"]
            ],
        )
        for employee in payload["employees"]
    ]

    demand = {
        day: {
            shift: {role: 0 for role in roles}
            for shift in range(len(shifts))
        }
        for day in days
    }
    seen_demand_keys: set[Tuple[int, int, str]] = set()
    for record in payload.get("demand", []):
        day = int(record["day"])
        shift = int(record["shift"])
        role = str(record["role"])
        key = (day, shift, role)
        if key in seen_demand_keys:
            raise ValueError(f"Duplicate demand record {key}")
        if day not in demand or shift not in demand[day] or role not in roles:
            raise ValueError(f"Demand record references unknown slot {key}")
        seen_demand_keys.add(key)
        demand[day][shift][role] = int(record["required"])

    hint_assignments: Dict[Tuple[int, int, int, str], int] = {}
    for record in payload.get("hint_assignments", []):
        key = (
            int(record["employee_id"]),
            int(record["day"]),
            int(record["shift"]),
            str(record["role"]),
        )
        if key in hint_assignments:
            raise ValueError(f"Duplicate hint assignment record {key}")
        hint_assignments[key] = int(record.get("value", 1))

    return ProblemData(
        employees=employees,
        roles=roles,
        days=days,
        shifts=shifts,
        shift_start_hours=[int(hour) for hour in payload["shift_start_hours"]],
        shift_end_hours=[int(hour) for hour in payload["shift_end_hours"]],
        min_rest_hours=int(payload["min_rest_hours"]),
        max_consecutive_days=int(payload["max_consecutive_days"]),
        shortage_penalty=int(payload["shortage_penalty"]),
        demand=demand,
        hint_assignments=hint_assignments,
    )


def solve_result_to_payload(result: SolveResult) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "metrics": asdict(result.metrics),
        "assignments": [
            _assignment_payload(assignment)
            for assignment in sorted(
                result.assignments,
                key=lambda item: (
                    item.employee_id,
                    item.day,
                    item.shift,
                    item.role,
                ),
            )
        ],
        "shortages": _shortage_records(result.shortages),
        "violations": list(result.violations),
        "constraint_metadata": dict(sorted(result.constraint_metadata.items())),
        "objective_metadata": dict(sorted(result.objective_metadata.items())),
        "constraint_records": [
            asdict(record) for record in result.constraint_records
        ],
        "fairness_metrics": {
            "assigned_hours_per_employee": [
                {"employee_id": employee_id, "assigned_hours": assigned_hours}
                for employee_id, assigned_hours in sorted(
                    result.fairness_metrics.assigned_hours_per_employee.items()
                )
            ],
            "min_assigned_hours": result.fairness_metrics.min_assigned_hours,
            "max_assigned_hours": result.fairness_metrics.max_assigned_hours,
            "workload_spread": result.fairness_metrics.workload_spread,
            "weekend_assignments_per_employee": [
                {
                    "employee_id": employee_id,
                    "weekend_assignment_count": assignment_count,
                }
                for employee_id, assignment_count in sorted(
                    result.fairness_metrics.weekend_assignments_per_employee.items()
                )
            ],
            "shift_counts_per_employee_shift": [
                {
                    "employee_id": employee_id,
                    "shift": shift,
                    "assignment_count": assignment_count,
                }
                for (employee_id, shift), assignment_count in sorted(
                    result.fairness_metrics.shift_counts_per_employee_shift.items()
                )
            ],
        },
        "objective_breakdown": asdict(result.objective_breakdown),
        "shortage_diagnostics": [
            _slot_candidate_payload(diagnostic)
            for diagnostic in result.shortage_diagnostics
        ],
        "demanded_slot_diagnostics": [
            _slot_candidate_payload(diagnostic)
            for diagnostic in result.demanded_slot_diagnostics
        ],
        "assignment_explanations": [
            _assignment_explanation_payload(explanation)
            for explanation in result.assignment_explanations
        ],
    }


def _demand_records(data: ProblemData) -> List[Dict[str, Any]]:
    return [
        {
            "day": day,
            "shift": shift,
            "role": role,
            "required": data.demand[day][shift][role],
        }
        for day in data.days
        for shift in range(len(data.shifts))
        for role in data.roles
    ]


def _hint_assignment_records(
    hint_assignments: Dict[Tuple[int, int, int, str], int],
) -> List[Dict[str, Any]]:
    return [
        {
            "employee_id": employee_id,
            "day": day,
            "shift": shift,
            "role": role,
            "value": value,
        }
        for (employee_id, day, shift, role), value in sorted(
            hint_assignments.items()
        )
    ]


def _shortage_records(
    shortages: Dict[Tuple[int, int, str], int],
) -> List[Dict[str, Any]]:
    return [
        {
            "day": day,
            "shift": shift,
            "role": role,
            "shortage_count": shortage_count,
        }
        for (day, shift, role), shortage_count in sorted(shortages.items())
    ]


def _assignment_payload(assignment: Assignment) -> Dict[str, Any]:
    return {
        "employee_id": assignment.employee_id,
        "day": assignment.day,
        "shift": assignment.shift,
        "role": assignment.role,
    }


def _slot_candidate_payload(diagnostic: SlotCandidateAnalysis) -> Dict[str, Any]:
    payload = asdict(diagnostic)
    payload["blocked_employee_ids_by_reason"] = {
        reason: list(employee_ids)
        for reason, employee_ids in sorted(
            diagnostic.blocked_employee_ids_by_reason.items()
        )
    }
    return payload


def _assignment_explanation_payload(
    explanation: AssignmentExplanation,
) -> Dict[str, Any]:
    return asdict(explanation)
