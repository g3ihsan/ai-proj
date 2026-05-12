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
from .warm_start import with_warm_start_hints


SCHEMA_VERSION = 1


class SchemaValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SolveOptions:
    time_limit_sec: float = 10.0
    seed: int = 1
    use_warm_start: bool = False


@dataclass(frozen=True)
class SolveRequest:
    problem: ProblemData
    options: SolveOptions


def solve_request_to_payload(
    data: ProblemData,
    *,
    time_limit_sec: float = 10.0,
    seed: int = 1,
    use_warm_start: bool = False,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "problem": problem_data_to_payload(data),
        "options": asdict(
            SolveOptions(
                time_limit_sec=time_limit_sec,
                seed=seed,
                use_warm_start=use_warm_start,
            )
        ),
    }


def parse_solve_request(payload: Mapping[str, Any]) -> SolveRequest:
    payload = _require_mapping(payload, "Solve request")
    schema_version = payload.get("schema_version", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise SchemaValidationError(
            f"Unsupported schema_version {schema_version}; expected {SCHEMA_VERSION}"
        )

    problem_payload = payload.get("problem")
    if not isinstance(problem_payload, Mapping):
        raise SchemaValidationError("Solve request must contain a problem object")
    options_payload = payload.get("options", {})
    if not isinstance(options_payload, Mapping):
        raise SchemaValidationError("Solve request options must be an object")

    options = SolveOptions(
        time_limit_sec=_float_option(options_payload, "time_limit_sec", 10.0),
        seed=_int_option(options_payload, "seed", 1),
        use_warm_start=_bool_option(options_payload, "use_warm_start", False),
    )
    try:
        problem = problem_data_from_payload(problem_payload)
    except SchemaValidationError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise SchemaValidationError(f"Invalid problem payload: {exc}") from exc

    return SolveRequest(problem=problem, options=options)


def solve_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    try:
        request = parse_solve_request(payload)
        problem = (
            with_warm_start_hints(request.problem)
            if request.options.use_warm_start
            else request.problem
        )
        result = solve(
            problem,
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


def _bool_option(
    payload: Mapping[str, Any],
    key: str,
    default: bool,
) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    raise SchemaValidationError(f"Solve option {key} must be a boolean")


def _float_option(
    payload: Mapping[str, Any],
    key: str,
    default: float,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise SchemaValidationError(f"Solve option {key} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(
            f"Solve option {key} must be numeric"
        ) from exc


def _int_option(
    payload: Mapping[str, Any],
    key: str,
    default: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise SchemaValidationError(f"Solve option {key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SchemaValidationError(
            f"Solve option {key} must be an integer"
        ) from exc


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SchemaValidationError(f"{label} must be an object")
    return value


def _required(payload: Mapping[str, Any], key: str, location: str) -> Any:
    if key not in payload:
        raise SchemaValidationError(f"Missing {location}.{key}")
    return payload[key]


def _require_list(value: Any, label: str) -> List[Any]:
    if not isinstance(value, list):
        raise SchemaValidationError(f"{label} must be a list")
    return value


def _bool_value(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    raise SchemaValidationError(f"{label} values must be booleans")


def _employee_records(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    employees = _require_list(
        _required(payload, "employees", "problem"),
        "problem.employees",
    )
    return [
        _require_mapping(employee, "employee record")
        for employee in employees
    ]


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
    payload = _require_mapping(payload, "problem")
    roles = [
        str(role)
        for role in _require_list(
            _required(payload, "roles", "problem"),
            "problem.roles",
        )
    ]
    days = [
        int(day)
        for day in _require_list(_required(payload, "days", "problem"), "problem.days")
    ]
    shifts = [
        str(shift)
        for shift in _require_list(
            _required(payload, "shifts", "problem"),
            "problem.shifts",
        )
    ]
    employees = [
        Employee(
            employee_id=int(_required(employee, "employee_id", "employee")),
            name=str(_required(employee, "name", "employee")),
            roles=tuple(
                str(role)
                for role in _require_list(
                    _required(employee, "roles", "employee"),
                    "employee.roles",
                )
            ),
            hourly_cost=int(_required(employee, "hourly_cost", "employee")),
            max_weekly_hours=int(
                _required(employee, "max_weekly_hours", "employee")
            ),
            availability=[
                [_bool_value(available, "employee.availability") for available in day]
                for day in _require_list(
                    _required(employee, "availability", "employee"),
                    "employee.availability",
                )
            ],
        )
        for employee in _employee_records(payload)
    ]

    demand = {
        day: {
            shift: {role: 0 for role in roles}
            for shift in range(len(shifts))
        }
        for day in days
    }
    seen_demand_keys: set[Tuple[int, int, str]] = set()
    demand_records = _require_list(payload.get("demand", []), "problem.demand")
    for record in demand_records:
        record = _require_mapping(record, "demand record")
        day = int(record["day"])
        shift = int(record["shift"])
        role = str(record["role"])
        key = (day, shift, role)
        if key in seen_demand_keys:
            raise SchemaValidationError(f"Duplicate demand record {key}")
        if day not in demand or shift not in demand[day] or role not in roles:
            raise SchemaValidationError(f"Demand record references unknown slot {key}")
        seen_demand_keys.add(key)
        demand[day][shift][role] = int(record["required"])

    hint_assignments: Dict[Tuple[int, int, int, str], int] = {}
    hint_records = _require_list(
        payload.get("hint_assignments", []),
        "problem.hint_assignments",
    )
    for record in hint_records:
        record = _require_mapping(record, "hint assignment record")
        key = (
            int(record["employee_id"]),
            int(record["day"]),
            int(record["shift"]),
            str(record["role"]),
        )
        if key in hint_assignments:
            raise SchemaValidationError(f"Duplicate hint assignment record {key}")
        hint_assignments[key] = int(record.get("value", 1))

    return ProblemData(
        employees=employees,
        roles=roles,
        days=days,
        shifts=shifts,
        shift_start_hours=[
            int(hour)
            for hour in _require_list(
                _required(payload, "shift_start_hours", "problem"),
                "problem.shift_start_hours",
            )
        ],
        shift_end_hours=[
            int(hour)
            for hour in _require_list(
                _required(payload, "shift_end_hours", "problem"),
                "problem.shift_end_hours",
            )
        ],
        min_rest_hours=int(_required(payload, "min_rest_hours", "problem")),
        max_consecutive_days=int(
            _required(payload, "max_consecutive_days", "problem")
        ),
        shortage_penalty=int(_required(payload, "shortage_penalty", "problem")),
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
