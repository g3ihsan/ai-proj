from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from .data import Employee, ProblemData, validate_problem_data
from .schemas import solve_request_to_payload
from .solve import Assignment


DEFAULT_MIN_REST_HOURS = 8
DEFAULT_MAX_CONSECUTIVE_DAYS = 5
DEFAULT_SHORTAGE_PENALTY = 1000
ROSTER_OUTPUT_HEADER = [
    "record_type",
    "employee_id",
    "name",
    "day",
    "shift",
    "shift_name",
    "role",
    "status",
    "value",
    "message",
]


class CsvAdapterError(ValueError):
    pass


def problem_data_from_csv_files(
    employees_csv: str | Path,
    shifts_csv: str | Path,
    demand_csv: str | Path,
    *,
    min_rest_hours: int,
    max_consecutive_days: int,
    shortage_penalty: int,
) -> ProblemData:
    shift_records = _read_records(shifts_csv, "shifts")
    demand_records = _read_records(demand_csv, "demand")

    shifts, shift_start_hours, shift_end_hours = _parse_shifts(shift_records)
    shift_indices = {shift: idx for idx, shift in enumerate(shifts)}
    demand, days, demand_roles = _parse_demand(
        demand_records,
        shift_indices,
        len(shifts),
    )

    employees = _parse_employees(
        _read_records(employees_csv, "employees"),
        num_days=len(days),
        num_shifts=len(shifts),
    )
    roles = _ordered_unique(
        role
        for role in [
            *demand_roles,
            *(role for employee in employees for role in employee.roles),
        ]
    )
    _ensure_all_roles_present(demand, days, len(shifts), roles)

    data = ProblemData(
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
        hint_assignments={},
    )
    errors = validate_problem_data(data)
    if errors:
        raise CsvAdapterError("; ".join(errors))
    return data


def payload_from_csv_files(
    employees_csv: str | Path,
    shifts_csv: str | Path,
    demand_csv: str | Path,
    *,
    min_rest_hours: int,
    max_consecutive_days: int,
    shortage_penalty: int,
    time_limit_sec: float,
    seed: int,
    use_warm_start: bool,
) -> Dict[str, Any]:
    data = problem_data_from_csv_files(
        employees_csv,
        shifts_csv,
        demand_csv,
        min_rest_hours=min_rest_hours,
        max_consecutive_days=max_consecutive_days,
        shortage_penalty=shortage_penalty,
    )
    return solve_request_to_payload(
        data,
        time_limit_sec=time_limit_sec,
        seed=seed,
        use_warm_start=use_warm_start,
    )


def write_roster_solution_csv(
    path: str | Path,
    data: ProblemData,
    assignments: List[Assignment],
    shortages: Dict[Tuple[int, int, str], int],
) -> None:
    response_payload = {
        "ok": True,
        "result": {
            "assignments": [
                {
                    "employee_id": assignment.employee_id,
                    "day": assignment.day,
                    "shift": assignment.shift,
                    "role": assignment.role,
                }
                for assignment in assignments
            ],
            "shortages": [
                {
                    "day": day,
                    "shift": shift,
                    "role": role,
                    "shortage_count": shortage_count,
                }
                for (day, shift, role), shortage_count in shortages.items()
            ],
            "violations": [],
        },
    }
    write_solve_response_csv(
        response_payload,
        path,
        employee_names={
            employee.employee_id: employee.name
            for employee in data.employees
        },
        shift_names={shift: name for shift, name in enumerate(data.shifts)},
    )


def csv_rows_from_solve_response(
    response_payload: dict,
    employee_names: dict[int, str] | None = None,
    shift_names: dict[int, str] | None = None,
) -> list[dict]:
    employee_names = employee_names or {}
    shift_names = shift_names or {}
    rows: list[dict] = []

    if not response_payload.get("ok", False):
        error = response_payload.get("error", {})
        rows.append(
            _csv_row(
                record_type="error",
                status=str(error.get("type", "error")),
                message=str(error.get("message", "")),
            )
        )
        return rows

    result = response_payload.get("result", {})
    metrics = result.get("metrics", {})
    if metrics:
        rows.append(
            _csv_row(
                record_type="summary",
                status=str(metrics.get("status", "")),
                value=_csv_value(metrics.get("objective_value")),
                message="Solver status and objective value",
            )
        )

    for assignment in sorted(
        result.get("assignments", []),
        key=lambda item: (
            int(item["day"]),
            int(item["shift"]),
            str(item["role"]),
            int(item["employee_id"]),
        ),
    ):
        employee_id = int(assignment["employee_id"])
        shift = int(assignment["shift"])
        rows.append(
            _csv_row(
                record_type="assignment",
                employee_id=employee_id,
                name=employee_names.get(employee_id, ""),
                day=int(assignment["day"]),
                shift=shift,
                shift_name=shift_names.get(shift, ""),
                role=str(assignment["role"]),
                status="assigned",
                value=1,
            )
        )

    for shortage in sorted(
        result.get("shortages", []),
        key=lambda item: (
            int(item["day"]),
            int(item["shift"]),
            str(item["role"]),
        ),
    ):
        shortage_count = int(shortage["shortage_count"])
        if shortage_count <= 0:
            continue
        shift = int(shortage["shift"])
        role = str(shortage["role"])
        rows.append(
            _csv_row(
                record_type="shortage",
                day=int(shortage["day"]),
                shift=shift,
                shift_name=shift_names.get(shift, ""),
                role=role,
                status="unfilled",
                value=shortage_count,
                message=f"Unfilled demand for {shortage_count} {role} slot(s)",
            )
        )

    for violation in result.get("violations", []):
        rows.append(
            _csv_row(
                record_type="validation",
                status="violation",
                message=str(violation),
            )
        )

    return rows


def write_solve_response_csv(
    response_payload: dict,
    path: str | Path,
    employee_names: dict[int, str] | None = None,
    shift_names: dict[int, str] | None = None,
) -> None:
    rows = csv_rows_from_solve_response(
        response_payload,
        employee_names=employee_names,
        shift_names=shift_names,
    )
    with open(path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ROSTER_OUTPUT_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def _csv_row(
    *,
    record_type: str,
    employee_id: int | str = "",
    name: str = "",
    day: int | str = "",
    shift: int | str = "",
    shift_name: str = "",
    role: str = "",
    status: str = "",
    value: int | float | str = "",
    message: str = "",
) -> Dict[str, Any]:
    return {
        "record_type": record_type,
        "employee_id": employee_id,
        "name": name,
        "day": day,
        "shift": shift,
        "shift_name": shift_name,
        "role": role,
        "status": status,
        "value": value,
        "message": message,
    }


def _csv_value(value: Any) -> int | float | str:
    return "" if value is None else value


def _read_records(path: str | Path, label: str) -> List[Mapping[str, str]]:
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise CsvAdapterError(f"{label}.csv must contain a header row")
        records = [dict(row) for row in reader]
    if not records:
        raise CsvAdapterError(f"{label}.csv must contain at least one row")
    return records


def _parse_shifts(
    records: List[Mapping[str, str]],
) -> Tuple[List[str], List[int], List[int]]:
    parsed: List[Tuple[int, str, int, int]] = []
    seen_ids: set[int] = set()
    seen_names: set[str] = set()
    explicit_shift_names = any(_has_value(record, "shift_name") for record in records)
    for row_number, record in enumerate(records, start=2):
        if explicit_shift_names:
            shift_id = _required_int(record, "shift", "shifts", row_number)
        else:
            shift_id = len(parsed)
        if shift_id < 0:
            raise CsvAdapterError(f"shifts row {row_number} shift must be non-negative")
        if shift_id in seen_ids:
            raise CsvAdapterError(f"Duplicate shift {shift_id}")
        seen_ids.add(shift_id)

        shift_name = (
            _shift_name(record, row_number)
            if explicit_shift_names
            else _required(record, "shift", "shifts", row_number)
        )
        if shift_name in seen_names:
            raise CsvAdapterError(f"Duplicate shift_name {shift_name}")
        seen_names.add(shift_name)

        parsed.append(
            (
                shift_id,
                shift_name,
                _required_int(record, "start_hour", "shifts", row_number),
                _required_int(record, "end_hour", "shifts", row_number),
            )
        )

    parsed.sort(key=lambda item: item[0])
    expected_ids = list(range(len(parsed)))
    actual_ids = [shift_id for shift_id, _, _, _ in parsed]
    if actual_ids != expected_ids:
        raise CsvAdapterError(
            "shifts.csv shift ids must be consecutive zero-based integers"
        )

    shifts = [shift_name for _, shift_name, _, _ in parsed]
    starts = [start for _, _, start, _ in parsed]
    ends = [end for _, _, _, end in parsed]
    return shifts, starts, ends


def _parse_demand(
    records: List[Mapping[str, str]],
    shift_indices: Dict[str, int],
    num_shifts: int,
) -> Tuple[Dict[int, Dict[int, Dict[str, int]]], List[int], List[str]]:
    max_day = -1
    parsed_records: List[Tuple[int, int, str, int]] = []
    roles: List[str] = []
    seen: set[Tuple[int, int, str]] = set()
    for row_number, record in enumerate(records, start=2):
        day = _required_int(record, "day", "demand", row_number)
        if day < 0:
            raise CsvAdapterError(f"demand row {row_number} day must be non-negative")
        shift = _parse_shift_reference(
            _required(record, "shift", "demand", row_number),
            shift_indices,
        )
        role = _required(record, "role", "demand", row_number)
        required = _required_int(record, "required", "demand", row_number)
        if required < 0:
            raise CsvAdapterError(
                f"demand row {row_number} required must be non-negative"
            )
        key = (day, shift, role)
        if key in seen:
            raise CsvAdapterError(f"Duplicate demand record {key}")
        seen.add(key)
        parsed_records.append((day, shift, role, required))
        roles.append(role)
        max_day = max(max_day, day)

    if max_day < 0:
        raise CsvAdapterError("demand.csv must contain at least one day")
    days = list(range(max_day + 1))
    demand = {
        day: {shift: {} for shift in range(num_shifts)}
        for day in days
    }
    for day, shift, role, required in parsed_records:
        demand[day][shift][role] = required
    return demand, days, _ordered_unique(roles)


def _parse_employees(
    records: List[Mapping[str, str]],
    num_days: int,
    num_shifts: int,
) -> List[Employee]:
    employees: List[Employee] = []
    seen: set[int] = set()
    for row_number, record in enumerate(records, start=2):
        employee_id = _required_int(record, "employee_id", "employees", row_number)
        if employee_id in seen:
            raise CsvAdapterError(f"Duplicate employee_id {employee_id}")
        seen.add(employee_id)
        roles = tuple(_split_pipe(_required(record, "roles", "employees", row_number)))
        if not roles:
            raise CsvAdapterError(f"employees row {row_number} roles must not be empty")
        employees.append(
            Employee(
                employee_id=employee_id,
                name=_required(record, "name", "employees", row_number),
                roles=roles,
                hourly_cost=_required_int(
                    record,
                    "hourly_cost",
                    "employees",
                    row_number,
                ),
                max_weekly_hours=_required_int(
                    record,
                    "max_weekly_hours",
                    "employees",
                    row_number,
                ),
                availability=_parse_employee_availability(
                    record,
                    num_days,
                    num_shifts,
                    row_number,
                ),
            )
        )
    return employees


def _parse_employee_availability(
    record: Mapping[str, str],
    num_days: int,
    num_shifts: int,
    row_number: int,
) -> List[List[bool]]:
    expected_fields = [
        f"available_day{day}_shift{shift}"
        for day in range(num_days)
        for shift in range(num_shifts)
    ]
    if all(_has_value(record, field) for field in expected_fields):
        return [
            [
                _parse_bool(
                    _required(
                        record,
                        f"available_day{day}_shift{shift}",
                        "employees",
                        row_number,
                    ),
                    row_number,
                )
                for shift in range(num_shifts)
            ]
            for day in range(num_days)
        ]

    if _has_value(record, "availability"):
        return _parse_compact_availability(
            _required(record, "availability", "employees", row_number),
            num_days,
            num_shifts,
            row_number,
        )

    missing = next(
        field for field in expected_fields if not _has_value(record, field)
    )
    raise CsvAdapterError(f"employees row {row_number} missing {missing}")


def _parse_compact_availability(
    value: str,
    num_days: int,
    num_shifts: int,
    row_number: int,
) -> List[List[bool]]:
    rows = value.split(";")
    if len(rows) != num_days:
        raise CsvAdapterError(
            f"employees row {row_number} availability must contain {num_days} day rows"
        )
    availability: List[List[bool]] = []
    for day, row in enumerate(rows):
        cells = row.split("|")
        if len(cells) != num_shifts:
            raise CsvAdapterError(
                f"employees row {row_number} availability day {day} "
                f"must contain {num_shifts} shift values"
            )
        availability.append([_parse_bool(cell, row_number) for cell in cells])
    return availability


def _ensure_all_roles_present(
    demand: Dict[int, Dict[int, Dict[str, int]]],
    days: List[int],
    num_shifts: int,
    roles: List[str],
) -> None:
    for day in days:
        for shift in range(num_shifts):
            for role in roles:
                demand[day][shift].setdefault(role, 0)


def _parse_shift_reference(value: str, shift_indices: Dict[str, int]) -> int:
    if value in shift_indices:
        return shift_indices[value]
    try:
        shift = int(value)
    except ValueError as exc:
        raise CsvAdapterError(f"Unknown shift {value}") from exc
    if shift < 0 or shift >= len(shift_indices):
        raise CsvAdapterError(f"Unknown shift {value}")
    return shift


def _shift_name(record: Mapping[str, str], row_number: int) -> str:
    if _has_value(record, "shift_name"):
        return _required(record, "shift_name", "shifts", row_number)
    raise CsvAdapterError(f"shifts row {row_number} missing shift_name")


def _parse_bool(value: str, row_number: int) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise CsvAdapterError(
        f"employees row {row_number} availability values must be booleans"
    )


def _has_value(record: Mapping[str, str], field: str) -> bool:
    value = record.get(field)
    return value is not None and value.strip() != ""


def _split_pipe(value: str) -> List[str]:
    return [item.strip() for item in value.split("|") if item.strip()]


def _ordered_unique(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _required(
    record: Mapping[str, str],
    field: str,
    file_label: str,
    row_number: int,
) -> str:
    value = record.get(field)
    if value is None or value.strip() == "":
        raise CsvAdapterError(f"{file_label} row {row_number} missing {field}")
    return value.strip()


def _required_int(
    record: Mapping[str, str],
    field: str,
    file_label: str,
    row_number: int,
) -> int:
    value = _required(record, field, file_label, row_number)
    try:
        return int(value)
    except ValueError as exc:
        raise CsvAdapterError(
            f"{file_label} row {row_number} {field} must be an integer"
        ) from exc
