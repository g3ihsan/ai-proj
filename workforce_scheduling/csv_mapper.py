from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence


CSV_MAPPING_CONTRACT_VERSION = 1
CSV_TYPE_EMPLOYEES = "employees"
CSV_TYPE_DEMAND = "demand"
CSV_TYPE_SHIFTS = "shifts"
MAPPING_STATUS_MAPPED = "mapped"
MAPPING_STATUS_MISSING = "missing"
APPLY_ACTION_RENAME = "rename_column"
APPLY_ACTION_PRESERVE = "preserve_column"
APPLY_ACTION_REVIEW = "requires_review"
DAY_NAME_TOKENS = {
    "mon",
    "monday",
    "tue",
    "tues",
    "tuesday",
    "wed",
    "weds",
    "wednesday",
    "thu",
    "thur",
    "thurs",
    "thursday",
    "fri",
    "friday",
    "sat",
    "saturday",
    "sun",
    "sunday",
}
AVAILABILITY_TOKENS = {
    "avail",
    "availability",
    "available",
    "works",
    "work",
    "can",
}

EMPLOYEE_CANONICAL_FIELDS = (
    "employee_id",
    "name",
    "roles",
    "hourly_cost",
    "max_weekly_hours",
    "availability",
)
DEMAND_CANONICAL_FIELDS = ("day", "shift", "role", "required")
SHIFT_CANONICAL_FIELDS = ("shift", "shift_name", "start_hour", "end_hour")

FIELD_ALIASES = {
    CSV_TYPE_EMPLOYEES: {
        "employee_id": (
            "employee_id",
            "employeeid",
            "employee_number",
            "employee_no",
            "employee_num",
            "employee_code",
            "emp_id",
            "empid",
            "emp_no",
            "emp_number",
            "staff_id",
            "staff_number",
            "staff_no",
            "worker_id",
            "person_id",
            "team_member_id",
            "member_id",
            "resource_id",
            "id",
        ),
        "name": (
            "name",
            "employee_name",
            "staff_name",
            "worker_name",
            "full_name",
            "person",
            "team_member",
            "employee",
            "resource_name",
            "member_name",
        ),
        "roles": (
            "roles",
            "role",
            "job_role",
            "job_roles",
            "job_title",
            "job_titles",
            "position",
            "positions",
            "position_title",
            "skills",
            "skill",
            "qualifications",
            "qualified_roles",
            "capabilities",
            "certifications",
        ),
        "hourly_cost": (
            "hourly_cost",
            "hourly_rate",
            "hourly_wage",
            "hourly_pay",
            "hourly",
            "labor_rate",
            "labour_rate",
            "pay_rate",
            "base_rate",
            "wage",
            "rate",
            "cost",
            "cost_per_hour",
            "rate_per_hour",
        ),
        "max_weekly_hours": (
            "max_weekly_hours",
            "maximum_weekly_hours",
            "max_week_hours",
            "max_hours_week",
            "max_weekly_hrs",
            "weekly_max_hours",
            "weekly_hours_limit",
            "weekly_hour_limit",
            "max_hours",
            "max_hrs",
            "hours_cap",
            "hrs_cap",
            "weekly_cap",
            "weekly_limit",
        ),
        "availability": (
            "availability",
            "available",
            "avail",
            "schedule_availability",
            "work_availability",
            "availability_matrix",
        ),
    },
    CSV_TYPE_DEMAND: {
        "day": ("day", "day_index", "day_id", "day_no", "date", "weekday"),
        "shift": (
            "shift",
            "shift_id",
            "shift_name",
            "shift_no",
            "period",
            "time_slot",
            "slot",
        ),
        "role": (
            "role",
            "job_role",
            "position",
            "skill",
            "required_role",
            "staff_role",
            "coverage_role",
        ),
        "required": (
            "required",
            "required_staff",
            "staff_required",
            "required_count",
            "required_headcount",
            "demand",
            "needed",
            "people_needed",
            "workers_needed",
            "headcount",
            "staff_count",
            "min_staff",
            "minimum_staff",
            "count",
            "quantity",
            "qty",
        ),
    },
    CSV_TYPE_SHIFTS: {
        "shift": ("shift", "shift_id", "shift_no", "shift_number", "id"),
        "shift_name": (
            "shift_name",
            "name",
            "label",
            "shift_label",
            "shift_title",
            "period_name",
        ),
        "start_hour": (
            "start_hour",
            "start_hr",
            "start",
            "start_time",
            "starts_at",
            "begin_hour",
            "begin_hr",
            "begin",
            "from",
            "from_hour",
        ),
        "end_hour": (
            "end_hour",
            "end_hr",
            "end",
            "end_time",
            "ends_at",
            "finish_hour",
            "finish_hr",
            "finish",
            "to",
            "to_hour",
        ),
    },
}

TOKEN_HINTS = {
    "employee_id": (
        {"employee", "id"},
        {"employee", "number"},
        {"employee", "code"},
        {"emp", "id"},
        {"emp", "number"},
        {"staff", "id"},
        {"staff", "number"},
        {"resource", "id"},
    ),
    "name": ({"name"}, {"full", "name"}),
    "roles": ({"role"}, {"roles"}, {"skill"}, {"skills"}, {"job", "title"}),
    "hourly_cost": (
        {"hourly", "cost"},
        {"hourly", "rate"},
        {"hourly", "pay"},
        {"pay", "rate"},
        {"cost", "hour"},
        {"rate", "hour"},
    ),
    "max_weekly_hours": (
        {"max", "weekly", "hours"},
        {"maximum", "weekly", "hours"},
        {"weekly", "max", "hours"},
        {"weekly", "hours", "limit"},
        {"hours", "cap"},
        {"hrs", "cap"},
    ),
    "day": ({"day"}, {"day", "index"}, {"date"}),
    "shift": ({"shift"}, {"shift", "id"}, {"time", "slot"}),
    "role": ({"role"}, {"job", "role"}, {"position"}, {"skill"}),
    "required": (
        {"required"},
        {"staff", "required"},
        {"required", "headcount"},
        {"people", "needed"},
        {"workers", "needed"},
        {"headcount"},
        {"min", "staff"},
    ),
    "shift_name": ({"shift", "name"}, {"shift", "label"}, {"name"}),
    "start_hour": (
        {"start", "hour"},
        {"start", "hr"},
        {"start", "time"},
        {"begin", "hour"},
        {"from", "hour"},
    ),
    "end_hour": (
        {"end", "hour"},
        {"end", "hr"},
        {"end", "time"},
        {"finish", "hour"},
        {"to", "hour"},
    ),
    "availability": ({"availability"}, {"available"}, {"avail"}),
}


class CsvMappingError(ValueError):
    pass


class CsvMappingValidationError(CsvMappingError):
    pass


def normalize_header(header: str) -> str:
    if not isinstance(header, str):
        raise CsvMappingError("CSV header must be a string")
    normalized = re.sub(r"[^a-z0-9]+", "_", header.strip().lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def mapping_confidence(header: str, canonical_field: str, csv_type: str) -> float:
    normalized = normalize_header(header)
    if _is_availability_header(normalized) and canonical_field == "availability":
        return 1.0
    aliases = FIELD_ALIASES.get(csv_type, {}).get(canonical_field, ())
    if normalized == canonical_field:
        return 1.0
    if normalized in aliases:
        return 0.95
    compact = normalized.replace("_", "")
    if compact in aliases:
        return 0.9
    header_tokens = set(normalized.split("_"))
    for token_set in TOKEN_HINTS.get(canonical_field, ()):
        if token_set <= header_tokens:
            return 0.75
    return 0.0


def suggest_employee_column_mapping(headers: Sequence[str]) -> dict[str, Any]:
    return _suggest_column_mapping(
        headers,
        csv_type=CSV_TYPE_EMPLOYEES,
        canonical_fields=EMPLOYEE_CANONICAL_FIELDS,
    )


def suggest_demand_column_mapping(headers: Sequence[str]) -> dict[str, Any]:
    return _suggest_column_mapping(
        headers,
        csv_type=CSV_TYPE_DEMAND,
        canonical_fields=DEMAND_CANONICAL_FIELDS,
    )


def suggest_shift_column_mapping(headers: Sequence[str]) -> dict[str, Any]:
    return _suggest_column_mapping(
        headers,
        csv_type=CSV_TYPE_SHIFTS,
        canonical_fields=SHIFT_CANONICAL_FIELDS,
    )


def validate_mapping(mapping_report: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(mapping_report, Mapping):
        raise CsvMappingValidationError("mapping report must be an object")
    csv_type = mapping_report.get("csv_type")
    if csv_type not in {CSV_TYPE_EMPLOYEES, CSV_TYPE_DEMAND, CSV_TYPE_SHIFTS}:
        raise CsvMappingValidationError("mapping report csv_type is invalid")
    missing_fields = mapping_report.get("missing_fields")
    if not isinstance(missing_fields, list):
        raise CsvMappingValidationError("mapping report missing_fields must be a list")
    if missing_fields:
        raise CsvMappingValidationError(
            f"{csv_type} mapping missing required field(s): "
            + ", ".join(str(field) for field in missing_fields)
        )
    return dict(mapping_report)


def csv_mapping_preview(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any] | None = None,
    mapping_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    headers = _validated_headers(headers)
    apply_plan = build_apply_plan(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    source_report = _column_mapping_report(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    return {
        "type": "csv_mapping_preview",
        "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
        "status": apply_plan["status"],
        "csv_type": csv_type,
        "headers": headers,
        "mapping": source_report,
        "apply_plan": apply_plan,
        "uses_external_llm": False,
        "will_mutate_files": False,
        "will_solve": False,
    }


def preview_column_renames(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any] | None = None,
    mapping_report: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    headers = _validated_headers(headers)
    canonical_fields = _canonical_fields_for_csv_type(csv_type)
    resolved_mapping = _resolved_mapping(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    rename_actions: list[dict[str, Any]] = []

    for field in canonical_fields:
        source_headers = resolved_mapping.get(field, [])
        if not source_headers:
            continue
        if field == "availability":
            for source_header in source_headers:
                normalized = normalize_header(source_header)
                target_header = _availability_target_header(normalized)
                status = (
                    APPLY_ACTION_REVIEW
                    if target_header is None
                    else _rename_status(source_header, target_header)
                )
                action = {
                    "canonical_field": field,
                    "source_header": source_header,
                    "target_header": target_header,
                    "normalized_source_header": normalized,
                    "action": status,
                }
                if status == APPLY_ACTION_REVIEW:
                    action["reason"] = (
                        "Availability header needs explicit day/shift index mapping "
                        "before csv_adapter.py can parse it."
                    )
                rename_actions.append(action)
            continue

        source_header = source_headers[0]
        target_header = field
        rename_actions.append(
            {
                "canonical_field": field,
                "source_header": source_header,
                "target_header": target_header,
                "normalized_source_header": normalize_header(source_header),
                "action": _rename_status(source_header, target_header),
            }
        )

    return rename_actions


def build_apply_plan(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any] | None = None,
    mapping_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    headers = _validated_headers(headers)
    canonical_fields = _canonical_fields_for_csv_type(csv_type)
    resolved_mapping = _resolved_mapping(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    column_renames = preview_column_renames(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    used_headers = {
        source_header
        for source_headers in resolved_mapping.values()
        for source_header in source_headers
    }
    missing_fields = [
        field for field in canonical_fields if not resolved_mapping.get(field)
    ]
    unresolved_actions = [
        action
        for action in column_renames
        if action["action"] == APPLY_ACTION_REVIEW
    ]
    target_headers_by_source = {
        action["source_header"]: action["target_header"]
        for action in column_renames
        if action["target_header"] is not None
    }
    canonical_headers_after_apply = [
        target_headers_by_source.get(header, header)
        for header in headers
    ]
    warnings = _apply_plan_warnings(
        csv_type=csv_type,
        missing_fields=missing_fields,
        column_renames=column_renames,
    )
    status = (
        "complete"
        if not missing_fields and not unresolved_actions and not _duplicate_targets(column_renames)
        else "needs_review"
    )
    return {
        "type": "csv_mapping_apply_plan",
        "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
        "status": status,
        "csv_type": csv_type,
        "will_mutate_files": False,
        "will_solve": False,
        "column_renames": column_renames,
        "canonical_headers_after_apply": canonical_headers_after_apply,
        "missing_fields": missing_fields,
        "unmapped_headers": [
            header for header in headers if header not in used_headers
        ],
        "warnings": warnings,
    }


def validate_apply_plan(apply_plan: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(apply_plan, Mapping):
        raise CsvMappingValidationError("apply plan must be an object")
    if apply_plan.get("type") != "csv_mapping_apply_plan":
        raise CsvMappingValidationError("apply plan type is invalid")
    csv_type = apply_plan.get("csv_type")
    _canonical_fields_for_csv_type(csv_type)
    if apply_plan.get("will_mutate_files") is not False:
        raise CsvMappingValidationError("apply plan must not mutate files")
    if apply_plan.get("will_solve") is not False:
        raise CsvMappingValidationError("apply plan must not run the solver")
    column_renames = apply_plan.get("column_renames")
    if not isinstance(column_renames, list):
        raise CsvMappingValidationError("apply plan column_renames must be a list")
    duplicate_targets = _duplicate_targets(column_renames)
    if duplicate_targets:
        raise CsvMappingValidationError(
            "apply plan has duplicate target header(s): "
            + ", ".join(duplicate_targets)
        )
    if apply_plan.get("status") != "complete":
        missing_fields = apply_plan.get("missing_fields")
        if isinstance(missing_fields, list) and missing_fields:
            raise CsvMappingValidationError(
                f"{csv_type} apply plan missing required field(s): "
                + ", ".join(str(field) for field in missing_fields)
            )
        raise CsvMappingValidationError("apply plan requires review before applying")
    return dict(apply_plan)


def csv_mapping_report(
    *,
    employee_headers: Sequence[str] | None = None,
    demand_headers: Sequence[str] | None = None,
    shift_headers: Sequence[str] | None = None,
) -> dict[str, Any]:
    files: dict[str, Any] = {}
    if employee_headers is not None:
        files[CSV_TYPE_EMPLOYEES] = suggest_employee_column_mapping(employee_headers)
    if demand_headers is not None:
        files[CSV_TYPE_DEMAND] = suggest_demand_column_mapping(demand_headers)
    if shift_headers is not None:
        files[CSV_TYPE_SHIFTS] = suggest_shift_column_mapping(shift_headers)
    if not files:
        raise CsvMappingValidationError("at least one CSV header list is required")
    status = "complete" if all(file["valid"] for file in files.values()) else "needs_review"
    return {
        "type": "csv_mapping_report",
        "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
        "status": status,
        "uses_external_llm": False,
        "files": files,
    }


def _canonical_fields_for_csv_type(csv_type: Any) -> tuple[str, ...]:
    if csv_type == CSV_TYPE_EMPLOYEES:
        return EMPLOYEE_CANONICAL_FIELDS
    if csv_type == CSV_TYPE_DEMAND:
        return DEMAND_CANONICAL_FIELDS
    if csv_type == CSV_TYPE_SHIFTS:
        return SHIFT_CANONICAL_FIELDS
    raise CsvMappingValidationError(f"Unsupported CSV mapping csv_type {csv_type}")


def _column_mapping_report(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any] | None,
    mapping_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if mapping is not None and mapping_report is not None:
        raise CsvMappingValidationError(
            "Provide either mapping or mapping_report, not both"
        )
    if mapping is not None:
        return _explicit_mapping_report(csv_type=csv_type, headers=headers, mapping=mapping)
    if mapping_report is not None:
        return _extract_column_mapping_report(csv_type, mapping_report)
    if csv_type == CSV_TYPE_EMPLOYEES:
        return suggest_employee_column_mapping(headers)
    if csv_type == CSV_TYPE_DEMAND:
        return suggest_demand_column_mapping(headers)
    if csv_type == CSV_TYPE_SHIFTS:
        return suggest_shift_column_mapping(headers)
    raise CsvMappingValidationError(f"Unsupported CSV mapping csv_type {csv_type}")


def _resolved_mapping(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any] | None,
    mapping_report: Mapping[str, Any] | None,
) -> dict[str, list[str]]:
    source_report = _column_mapping_report(
        csv_type=csv_type,
        headers=headers,
        mapping=mapping,
        mapping_report=mapping_report,
    )
    resolved: dict[str, list[str]] = {}
    for field in _canonical_fields_for_csv_type(csv_type):
        field_mapping = source_report.get("mapping", {}).get(field, {})
        if field_mapping.get("status") != MAPPING_STATUS_MAPPED:
            resolved[field] = []
            continue
        if field == "availability":
            resolved[field] = list(field_mapping.get("source_headers", []))
        else:
            source_header = field_mapping.get("source_header")
            resolved[field] = [source_header] if isinstance(source_header, str) else []
        for source_header in resolved[field]:
            if source_header not in headers:
                raise CsvMappingValidationError(
                    f"mapping field {field} references unknown header {source_header}"
                )
    return resolved


def _explicit_mapping_report(
    *,
    csv_type: str,
    headers: Sequence[str],
    mapping: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        raise CsvMappingValidationError("mapping must be an object")
    canonical_fields = _canonical_fields_for_csv_type(csv_type)
    normalized_headers = {header: normalize_header(header) for header in headers}
    unknown_fields = sorted(str(field) for field in mapping if field not in canonical_fields)
    if unknown_fields:
        raise CsvMappingValidationError(
            "mapping contains unsupported field(s): " + ", ".join(unknown_fields)
        )

    field_mappings: dict[str, Any] = {}
    assigned_headers: set[str] = set()
    for field in canonical_fields:
        if field not in mapping:
            field_mappings[field] = {
                "status": MAPPING_STATUS_MISSING,
                "source_header": None,
                "normalized_header": None,
                "confidence": 0.0,
            }
            continue
        value = mapping[field]
        source_headers = (
            _required_header_list(value, headers, field)
            if field == "availability"
            else [_required_header_value(value, headers, field)]
        )
        for source_header in source_headers:
            if source_header in assigned_headers:
                raise CsvMappingValidationError(
                    f"mapping source header {source_header} is assigned more than once"
                )
            assigned_headers.add(source_header)
        if field == "availability":
            field_mappings[field] = {
                "status": MAPPING_STATUS_MAPPED,
                "source_headers": source_headers,
                "normalized_headers": [
                    normalized_headers[source_header] for source_header in source_headers
                ],
                "confidence": 1.0,
            }
        else:
            source_header = source_headers[0]
            field_mappings[field] = {
                "status": MAPPING_STATUS_MAPPED,
                "source_header": source_header,
                "normalized_header": normalized_headers[source_header],
                "confidence": 1.0,
            }

    missing_fields = [
        field
        for field, field_mapping in field_mappings.items()
        if field_mapping["status"] == MAPPING_STATUS_MISSING
    ]
    return {
        "type": "csv_column_mapping",
        "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
        "csv_type": csv_type,
        "canonical_fields": list(canonical_fields),
        "mapping": field_mappings,
        "missing_fields": missing_fields,
        "unmapped_headers": [
            header for header in headers if header not in assigned_headers
        ],
        "warnings": _mapping_warnings(csv_type, field_mappings),
        "valid": not missing_fields,
        "uses_external_llm": False,
    }


def _extract_column_mapping_report(
    csv_type: str,
    mapping_report: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(mapping_report, Mapping):
        raise CsvMappingValidationError("mapping_report must be an object")
    if mapping_report.get("type") == "csv_mapping_report":
        files = mapping_report.get("files")
        if not isinstance(files, Mapping) or csv_type not in files:
            raise CsvMappingValidationError(
                f"mapping_report does not contain csv_type {csv_type}"
            )
        file_report = files[csv_type]
        if not isinstance(file_report, Mapping):
            raise CsvMappingValidationError("mapping_report file entry must be an object")
        return dict(file_report)
    if mapping_report.get("type") == "csv_column_mapping":
        if mapping_report.get("csv_type") != csv_type:
            raise CsvMappingValidationError("mapping_report csv_type does not match")
        return dict(mapping_report)
    raise CsvMappingValidationError("mapping_report type is invalid")


def _required_header_value(value: Any, headers: Sequence[str], field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CsvMappingValidationError(f"mapping field {field} must be a header string")
    value = value.strip()
    if value not in headers:
        raise CsvMappingValidationError(
            f"mapping field {field} references unknown header {value}"
        )
    return value


def _required_header_list(
    value: Any,
    headers: Sequence[str],
    field: str,
) -> list[str]:
    if isinstance(value, str):
        return [_required_header_value(value, headers, field)]
    if isinstance(value, Sequence):
        source_headers = [
            _required_header_value(source_header, headers, field)
            for source_header in value
        ]
        if not source_headers:
            raise CsvMappingValidationError(
                f"mapping field {field} must include at least one header"
            )
        return source_headers
    raise CsvMappingValidationError(
        f"mapping field {field} must be a header string or list"
    )


def _availability_target_header(normalized_header: str) -> str | None:
    for pattern in (
        r"(?:available|avail)_day_?(?P<day>\d+)_shift_?(?P<shift>\d+)",
        r"(?:available|avail)_d_?(?P<day>\d+)_s_?(?P<shift>\d+)",
    ):
        match = re.fullmatch(pattern, normalized_header)
        if match:
            return (
                f"available_day{int(match.group('day'))}"
                f"_shift{int(match.group('shift'))}"
            )
    if normalized_header in FIELD_ALIASES[CSV_TYPE_EMPLOYEES]["availability"]:
        return "availability"
    return None


def _rename_status(source_header: str, target_header: str) -> str:
    if normalize_header(source_header) == target_header:
        return APPLY_ACTION_PRESERVE
    return APPLY_ACTION_RENAME


def _apply_plan_warnings(
    *,
    csv_type: str,
    missing_fields: Sequence[str],
    column_renames: Sequence[Mapping[str, Any]],
) -> list[str]:
    warnings: list[str] = []
    if missing_fields:
        warnings.append(
            "Apply plan is incomplete until missing canonical fields are mapped."
        )
    if any(action.get("action") == APPLY_ACTION_REVIEW for action in column_renames):
        warnings.append(
            "One or more availability headers need explicit day/shift indexes "
            "before csv_adapter.py can parse them."
        )
    duplicate_targets = _duplicate_targets(column_renames)
    if duplicate_targets:
        warnings.append(
            "Multiple source headers map to the same target header(s): "
            + ", ".join(duplicate_targets)
        )
    if csv_type == CSV_TYPE_EMPLOYEES and any(
        action.get("target_header") == "availability" for action in column_renames
    ):
        warnings.append(
            "Compact availability must still match the expected day/shift matrix."
        )
    return warnings


def _duplicate_targets(column_renames: Sequence[Mapping[str, Any]]) -> list[str]:
    targets = [
        action.get("target_header")
        for action in column_renames
        if isinstance(action.get("target_header"), str)
    ]
    return sorted(_duplicates(targets))


def _suggest_column_mapping(
    headers: Sequence[str],
    *,
    csv_type: str,
    canonical_fields: Sequence[str],
) -> dict[str, Any]:
    headers = _validated_headers(headers)
    normalized_headers = {header: normalize_header(header) for header in headers}
    assigned_headers: set[str] = set()
    mapping: dict[str, Any] = {}

    for field in canonical_fields:
        if field == "availability":
            availability_headers = [
                header
                for header in headers
                if _is_availability_header(normalized_headers[header])
            ]
            if availability_headers:
                assigned_headers.update(availability_headers)
                mapping[field] = {
                    "status": MAPPING_STATUS_MAPPED,
                    "source_headers": availability_headers,
                    "normalized_headers": [
                        normalized_headers[header] for header in availability_headers
                    ],
                    "confidence": 1.0,
                }
                continue

        match = _best_header_match(headers, assigned_headers, field, csv_type)
        if match is None:
            mapping[field] = {
                "status": MAPPING_STATUS_MISSING,
                "source_header": None,
                "normalized_header": None,
                "confidence": 0.0,
            }
            continue
        header, confidence = match
        assigned_headers.add(header)
        mapping[field] = {
            "status": MAPPING_STATUS_MAPPED,
            "source_header": header,
            "normalized_header": normalized_headers[header],
            "confidence": confidence,
        }

    missing_fields = [
        field
        for field, field_mapping in mapping.items()
        if field_mapping["status"] == MAPPING_STATUS_MISSING
    ]
    unmapped_headers = [
        header for header in headers if header not in assigned_headers
    ]
    warnings = _mapping_warnings(csv_type, mapping)
    return {
        "type": "csv_column_mapping",
        "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
        "csv_type": csv_type,
        "canonical_fields": list(canonical_fields),
        "mapping": mapping,
        "missing_fields": missing_fields,
        "unmapped_headers": unmapped_headers,
        "warnings": warnings,
        "valid": not missing_fields,
        "uses_external_llm": False,
    }


def _validated_headers(headers: Sequence[str]) -> list[str]:
    if isinstance(headers, str) or not isinstance(headers, Sequence):
        raise CsvMappingError("headers must be a sequence of strings")
    if not headers:
        raise CsvMappingValidationError("headers must not be empty")
    invalid = [header for header in headers if not isinstance(header, str)]
    if invalid:
        raise CsvMappingError("headers must contain only strings")
    normalized = [normalize_header(header) for header in headers]
    if any(not header for header in normalized):
        raise CsvMappingValidationError("headers must not contain empty values")
    duplicates = sorted(_duplicates(normalized))
    if duplicates:
        raise CsvMappingValidationError(
            "headers contain duplicate normalized values: " + ", ".join(duplicates)
        )
    return list(headers)


def _best_header_match(
    headers: Sequence[str],
    assigned_headers: set[str],
    field: str,
    csv_type: str,
) -> tuple[str, float] | None:
    candidates = [
        (header, mapping_confidence(header, field, csv_type))
        for header in headers
        if header not in assigned_headers
    ]
    candidates = [
        (header, confidence)
        for header, confidence in candidates
        if confidence > 0
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1], headers.index(item[0]), item[0]))
    return candidates[0]


def _is_availability_header(normalized_header: str) -> bool:
    return bool(
        re.fullmatch(r"available_day_?\d+_shift_?\d+", normalized_header)
        or re.fullmatch(r"avail_day_?\d+_shift_?\d+", normalized_header)
        or re.fullmatch(r"available_d_?\d+_s_?\d+", normalized_header)
        or re.fullmatch(r"avail_d_?\d+_s_?\d+", normalized_header)
        or _is_day_name_availability_header(normalized_header)
        or normalized_header in FIELD_ALIASES[CSV_TYPE_EMPLOYEES]["availability"]
    )


def _is_day_name_availability_header(normalized_header: str) -> bool:
    tokens = set(normalized_header.split("_"))
    if not tokens & DAY_NAME_TOKENS:
        return False
    if tokens & AVAILABILITY_TOKENS:
        return True
    return normalized_header.startswith(("available_", "avail_"))


def _mapping_warnings(csv_type: str, mapping: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    if csv_type == CSV_TYPE_EMPLOYEES:
        availability = mapping.get("availability", {})
        if (
            availability.get("status") == MAPPING_STATUS_MAPPED
            and availability.get("normalized_headers") == ["availability"]
        ):
            warnings.append(
                "Compact availability must still match the expected day/shift matrix."
            )
    return warnings


def _duplicates(values: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates
