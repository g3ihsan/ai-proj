from __future__ import annotations

import json

import pytest

from workforce_scheduling.csv_mapper import (
    CsvMappingError,
    CsvMappingValidationError,
    build_apply_plan,
    csv_mapping_report,
    csv_mapping_preview,
    csv_row_transformation_preview,
    mapping_confidence,
    normalize_header,
    preview_column_renames,
    preview_transformed_rows,
    suggest_demand_column_mapping,
    suggest_employee_column_mapping,
    suggest_shift_column_mapping,
    transform_row_with_apply_plan,
    validate_apply_plan,
    validate_mapping,
    validate_row_preview_request,
)


def test_normalize_header_is_deterministic_and_csv_safe() -> None:
    assert normalize_header(" Employee ID ") == "employee_id"
    assert normalize_header("Hourly Rate ($)") == "hourly_rate"
    assert normalize_header("Available Day 0 / Shift 1") == (
        "available_day_0_shift_1"
    )


def test_employee_mapper_suggests_common_messy_columns() -> None:
    report = suggest_employee_column_mapping(
        [
            "Staff ID",
            "Full Name",
            "Skills",
            "Hourly Rate",
            "Weekly Hours Limit",
            "Available Day0 Shift0",
            "Available Day0 Shift1",
            "Notes",
        ]
    )

    assert report["csv_type"] == "employees"
    assert report["valid"] is True
    assert report["uses_external_llm"] is False
    assert report["missing_fields"] == []
    assert report["mapping"]["employee_id"]["source_header"] == "Staff ID"
    assert report["mapping"]["name"]["source_header"] == "Full Name"
    assert report["mapping"]["roles"]["source_header"] == "Skills"
    assert report["mapping"]["hourly_cost"]["source_header"] == "Hourly Rate"
    assert report["mapping"]["max_weekly_hours"]["source_header"] == (
        "Weekly Hours Limit"
    )
    assert report["mapping"]["availability"]["source_headers"] == [
        "Available Day0 Shift0",
        "Available Day0 Shift1",
    ]
    assert report["unmapped_headers"] == ["Notes"]
    validate_mapping(report)
    json.dumps(report, sort_keys=True)


def test_employee_mapper_supports_additional_header_variants() -> None:
    report = suggest_employee_column_mapping(
        [
            "Employee Number",
            "Resource Name",
            "Job Title",
            "Cost Per Hour",
            "Weekly Max Hours",
            "Avail D0 S0",
        ]
    )

    assert report["valid"] is True
    assert report["mapping"]["employee_id"]["source_header"] == "Employee Number"
    assert report["mapping"]["name"]["source_header"] == "Resource Name"
    assert report["mapping"]["roles"]["source_header"] == "Job Title"
    assert report["mapping"]["hourly_cost"]["source_header"] == "Cost Per Hour"
    assert report["mapping"]["max_weekly_hours"]["source_header"] == (
        "Weekly Max Hours"
    )
    assert report["mapping"]["availability"]["source_headers"] == ["Avail D0 S0"]


def test_employee_mapper_detects_day_name_availability_headers() -> None:
    report = suggest_employee_column_mapping(
        [
            "Staff ID",
            "Full Name",
            "Skills",
            "Hourly Rate",
            "Weekly Hours Limit",
            "Available Monday Morning",
            "Can Work Tue Evening",
            "Avail Fri Night",
        ]
    )

    assert report["valid"] is True
    assert report["mapping"]["availability"]["source_headers"] == [
        "Available Monday Morning",
        "Can Work Tue Evening",
        "Avail Fri Night",
    ]
    assert report["mapping"]["availability"]["normalized_headers"] == [
        "available_monday_morning",
        "can_work_tue_evening",
        "avail_fri_night",
    ]


def test_employee_mapper_supports_compact_availability_with_warning() -> None:
    report = suggest_employee_column_mapping(
        [
            "employee_id",
            "name",
            "roles",
            "hourly_cost",
            "max_weekly_hours",
            "availability",
        ]
    )

    assert report["valid"] is True
    assert report["mapping"]["availability"]["source_headers"] == ["availability"]
    assert report["warnings"] == [
        "Compact availability must still match the expected day/shift matrix."
    ]


def test_demand_mapper_suggests_common_messy_columns() -> None:
    report = suggest_demand_column_mapping(
        ["Day Index", "Shift Name", "Required Role", "Headcount"]
    )

    assert report["valid"] is True
    assert report["mapping"]["day"]["source_header"] == "Day Index"
    assert report["mapping"]["shift"]["source_header"] == "Shift Name"
    assert report["mapping"]["role"]["source_header"] == "Required Role"
    assert report["mapping"]["required"]["source_header"] == "Headcount"


def test_demand_mapper_supports_additional_header_variants() -> None:
    report = suggest_demand_column_mapping(
        ["Weekday", "Time Slot", "Coverage Role", "Workers Needed"]
    )

    assert report["valid"] is True
    assert report["mapping"]["day"]["source_header"] == "Weekday"
    assert report["mapping"]["shift"]["source_header"] == "Time Slot"
    assert report["mapping"]["role"]["source_header"] == "Coverage Role"
    assert report["mapping"]["required"]["source_header"] == "Workers Needed"


def test_shift_mapper_suggests_common_messy_columns_without_colliding_name() -> None:
    report = suggest_shift_column_mapping(
        ["Shift ID", "Shift Label", "Start Time", "End Time"]
    )

    assert report["valid"] is True
    assert report["mapping"]["shift"]["source_header"] == "Shift ID"
    assert report["mapping"]["shift_name"]["source_header"] == "Shift Label"
    assert report["mapping"]["start_hour"]["source_header"] == "Start Time"
    assert report["mapping"]["end_hour"]["source_header"] == "End Time"


def test_shift_mapper_supports_additional_header_variants() -> None:
    report = suggest_shift_column_mapping(
        ["Shift Number", "Period Name", "From Hour", "To Hour"]
    )

    assert report["valid"] is True
    assert report["mapping"]["shift"]["source_header"] == "Shift Number"
    assert report["mapping"]["shift_name"]["source_header"] == "Period Name"
    assert report["mapping"]["start_hour"]["source_header"] == "From Hour"
    assert report["mapping"]["end_hour"]["source_header"] == "To Hour"


def test_mapper_reports_missing_fields_for_review() -> None:
    report = suggest_employee_column_mapping(["Name", "Role", "Hourly Rate"])

    assert report["valid"] is False
    assert report["missing_fields"] == [
        "employee_id",
        "max_weekly_hours",
        "availability",
    ]
    with pytest.raises(CsvMappingValidationError) as exc_info:
        validate_mapping(report)

    assert str(exc_info.value) == (
        "employees mapping missing required field(s): "
        "employee_id, max_weekly_hours, availability"
    )


def test_csv_mapping_report_combines_files_and_status() -> None:
    report = csv_mapping_report(
        employee_headers=[
            "Emp ID",
            "Employee Name",
            "Job Roles",
            "Pay Rate",
            "Max Hours",
            "Availability",
        ],
        demand_headers=["Day", "Shift", "Role", "Demand"],
        shift_headers=["Shift", "Shift Name", "Start Hour", "End Hour"],
    )

    assert report["type"] == "csv_mapping_report"
    assert report["csv_mapping_contract_version"] == 1
    assert report["status"] == "complete"
    assert report["uses_external_llm"] is False
    assert sorted(report["files"]) == ["demand", "employees", "shifts"]
    json.dumps(report, sort_keys=True)


def test_csv_mapping_preview_builds_explicit_apply_plan_without_mutation() -> None:
    headers = [
        "Staff ID",
        "Full Name",
        "Skills",
        "Cost Per Hour",
        "Weekly Limit",
        "Available Day0 Shift0",
    ]
    preview = csv_mapping_preview(
        csv_type="employees",
        headers=headers,
        mapping={
            "employee_id": "Staff ID",
            "name": "Full Name",
            "roles": "Skills",
            "hourly_cost": "Cost Per Hour",
            "max_weekly_hours": "Weekly Limit",
            "availability": ["Available Day0 Shift0"],
        },
    )

    assert preview["type"] == "csv_mapping_preview"
    assert preview["status"] == "complete"
    assert preview["headers"] == headers
    assert headers == [
        "Staff ID",
        "Full Name",
        "Skills",
        "Cost Per Hour",
        "Weekly Limit",
        "Available Day0 Shift0",
    ]
    assert preview["uses_external_llm"] is False
    assert preview["will_mutate_files"] is False
    assert preview["will_solve"] is False
    apply_plan = preview["apply_plan"]
    assert apply_plan["type"] == "csv_mapping_apply_plan"
    assert apply_plan["can_apply"] is True
    assert apply_plan["reason"] == "ready"
    assert apply_plan["adapter_readiness"] == {
        "scope": "headers_only",
        "headers_ready_for_csv_adapter": True,
        "row_data_validated": False,
        "reason": "ready",
    }
    assert apply_plan["will_mutate_files"] is False
    assert apply_plan["will_solve"] is False
    assert apply_plan["canonical_headers_after_apply"] == [
        "employee_id",
        "name",
        "roles",
        "hourly_cost",
        "max_weekly_hours",
        "available_day0_shift0",
    ]
    assert apply_plan["missing_fields"] == []
    assert apply_plan["warnings"] == []
    assert apply_plan["column_renames"][0] == {
        "canonical_field": "employee_id",
        "source_header": "Staff ID",
        "target_header": "employee_id",
        "normalized_source_header": "staff_id",
        "action": "rename_column",
    }
    assert apply_plan["column_renames"][-1] == {
        "canonical_field": "availability",
        "source_header": "Available Day0 Shift0",
        "target_header": "available_day0_shift0",
        "normalized_source_header": "available_day0_shift0",
        "action": "preserve_column",
    }
    validate_apply_plan(apply_plan)
    json.dumps(preview, sort_keys=True)


def test_csv_mapping_preview_can_use_suggested_mapping_report() -> None:
    headers = ["Day Index", "Shift Name", "Required Role", "Headcount"]
    report = suggest_demand_column_mapping(headers)

    preview = csv_mapping_preview(
        csv_type="demand",
        headers=headers,
        mapping_report=report,
    )

    assert preview["status"] == "complete"
    assert preview["mapping"] == report
    assert preview["apply_plan"]["can_apply"] is True
    assert preview["apply_plan"]["reason"] == "ready"
    assert preview["apply_plan"]["canonical_headers_after_apply"] == [
        "day",
        "shift",
        "role",
        "required",
    ]


def test_csv_mapping_preview_marks_day_name_availability_for_review() -> None:
    preview = csv_mapping_preview(
        csv_type="employees",
        headers=[
            "Staff ID",
            "Full Name",
            "Skills",
            "Hourly Rate",
            "Weekly Hours Limit",
            "Available Monday Morning",
        ],
    )

    assert preview["status"] == "needs_review"
    assert preview["apply_plan"]["can_apply"] is False
    assert preview["apply_plan"]["reason"] == "requires_review"
    assert preview["apply_plan"]["adapter_readiness"] == {
        "scope": "headers_only",
        "headers_ready_for_csv_adapter": False,
        "row_data_validated": False,
        "reason": "requires_review",
    }
    availability_action = preview["apply_plan"]["column_renames"][-1]
    assert availability_action["canonical_field"] == "availability"
    assert availability_action["source_header"] == "Available Monday Morning"
    assert availability_action["target_header"] is None
    assert availability_action["action"] == "requires_review"
    assert preview["apply_plan"]["warnings"] == [
        (
            "One or more availability headers need explicit day/shift indexes "
            "before csv_adapter.py can parse them."
        )
    ]
    with pytest.raises(CsvMappingValidationError, match="requires review"):
        validate_apply_plan(preview["apply_plan"])


def test_csv_mapping_preview_reports_missing_fields_for_partial_mapping() -> None:
    apply_plan = build_apply_plan(
        csv_type="employees",
        headers=["Staff ID", "Full Name", "Cost Per Hour"],
        mapping={
            "employee_id": "Staff ID",
            "name": "Full Name",
            "hourly_cost": "Cost Per Hour",
        },
    )

    assert apply_plan["status"] == "needs_review"
    assert apply_plan["can_apply"] is False
    assert apply_plan["reason"] == "missing_required_fields"
    assert apply_plan["missing_fields"] == [
        "roles",
        "max_weekly_hours",
        "availability",
    ]
    assert apply_plan["unmapped_headers"] == []
    with pytest.raises(CsvMappingValidationError) as exc_info:
        validate_apply_plan(apply_plan)

    assert str(exc_info.value) == (
        "employees apply plan missing required field(s): "
        "roles, max_weekly_hours, availability"
    )


def test_csv_mapping_preview_reports_duplicate_target_reason() -> None:
    apply_plan = build_apply_plan(
        csv_type="employees",
        headers=[
            "Staff ID",
            "Full Name",
            "Skills",
            "Hourly Rate",
            "Weekly Hours Limit",
            "Avail D0 S0",
            "Available Day0 Shift0",
        ],
    )

    assert apply_plan["status"] == "needs_review"
    assert apply_plan["can_apply"] is False
    assert apply_plan["reason"] == "duplicate_target_headers"
    assert apply_plan["adapter_readiness"]["headers_ready_for_csv_adapter"] is False
    assert apply_plan["warnings"] == [
        "Multiple source headers map to the same target header(s): available_day0_shift0"
    ]
    with pytest.raises(CsvMappingValidationError, match="duplicate target"):
        validate_apply_plan(apply_plan)


def test_validate_apply_plan_rejects_inconsistent_reason_and_readiness() -> None:
    apply_plan = build_apply_plan(
        csv_type="demand",
        headers=["Day", "Shift", "Role", "Required"],
    )

    missing_can_apply = dict(apply_plan)
    missing_can_apply.pop("can_apply")
    with pytest.raises(CsvMappingValidationError, match="can_apply must be a boolean"):
        validate_apply_plan(missing_can_apply)

    invalid_reason = {**apply_plan, "reason": "unknown"}
    with pytest.raises(CsvMappingValidationError, match="reason is invalid"):
        validate_apply_plan(invalid_reason)

    complete_without_can_apply = {
        **apply_plan,
        "can_apply": False,
        "adapter_readiness": {
            **apply_plan["adapter_readiness"],
            "headers_ready_for_csv_adapter": False,
        },
    }
    with pytest.raises(
        CsvMappingValidationError,
        match="complete apply plan must set can_apply to true",
    ):
        validate_apply_plan(complete_without_can_apply)

    complete_with_wrong_reason = {
        **apply_plan,
        "reason": "requires_review",
        "adapter_readiness": {
            **apply_plan["adapter_readiness"],
            "reason": "requires_review",
        },
    }
    with pytest.raises(
        CsvMappingValidationError,
        match="complete apply plan reason must be ready",
    ):
        validate_apply_plan(complete_with_wrong_reason)

    wrong_readiness = {
        **apply_plan,
        "adapter_readiness": {
            **apply_plan["adapter_readiness"],
            "headers_ready_for_csv_adapter": False,
        },
    }
    with pytest.raises(
        CsvMappingValidationError,
        match="adapter readiness must match can_apply",
    ):
        validate_apply_plan(wrong_readiness)

    incomplete_with_can_apply = build_apply_plan(
        csv_type="employees",
        headers=["Staff ID"],
        mapping={"employee_id": "Staff ID"},
    )
    incomplete_with_can_apply = {
        **incomplete_with_can_apply,
        "can_apply": True,
        "adapter_readiness": {
            **incomplete_with_can_apply["adapter_readiness"],
            "headers_ready_for_csv_adapter": True,
        },
    }
    with pytest.raises(
        CsvMappingValidationError,
        match="incomplete apply plan must set can_apply to false",
    ):
        validate_apply_plan(incomplete_with_can_apply)


def test_csv_mapping_preview_validates_supplied_mapping_report() -> None:
    headers = ["Day", "Shift", "Role", "Required"]
    report = suggest_demand_column_mapping(headers)

    preview = csv_mapping_preview(
        csv_type="demand",
        headers=headers,
        mapping_report=report,
    )
    assert preview["apply_plan"]["reason"] == "ready"

    external_llm_report = {**report, "uses_external_llm": True}
    with pytest.raises(CsvMappingValidationError, match="external LLM"):
        csv_mapping_preview(
            csv_type="demand",
            headers=headers,
            mapping_report=external_llm_report,
        )

    invalid_version_report = {**report, "csv_mapping_contract_version": 999}
    with pytest.raises(CsvMappingValidationError, match="contract version"):
        csv_mapping_preview(
            csv_type="demand",
            headers=headers,
            mapping_report=invalid_version_report,
        )

    invalid_valid_report = {**report, "valid": "yes"}
    with pytest.raises(CsvMappingValidationError, match="valid must be a boolean"):
        csv_mapping_preview(
            csv_type="demand",
            headers=headers,
            mapping_report=invalid_valid_report,
        )

    mismatched_missing = {**report, "missing_fields": ["role"]}
    with pytest.raises(CsvMappingValidationError, match="missing_fields"):
        csv_mapping_preview(
            csv_type="demand",
            headers=headers,
            mapping_report=mismatched_missing,
        )

    duplicate_source_report = json.loads(json.dumps(report))
    duplicate_source_report["mapping"]["role"]["source_header"] = "Day"
    with pytest.raises(CsvMappingValidationError, match="assigned more than once"):
        csv_mapping_preview(
            csv_type="demand",
            headers=headers,
            mapping_report=duplicate_source_report,
        )


def test_csv_row_transformation_preview_maps_sample_rows_without_solving() -> None:
    headers = ["Staff ID", "Full Name", "Skills", "Cost Per Hour"]
    rows = [
        ["E1", "Asha", "worker|supervisor", "20"],
        ["E2", "Ravi", "worker", "18"],
    ]

    preview = csv_row_transformation_preview(
        csv_type="employees",
        headers=headers,
        rows=rows,
        mapping={
            "employee_id": "Staff ID",
            "name": "Full Name",
            "roles": "Skills",
            "hourly_cost": "Cost Per Hour",
        },
    )

    assert preview["type"] == "csv_row_transformation_preview"
    assert preview["status"] == "needs_review"
    assert preview["csv_type"] == "employees"
    assert preview["row_count"] == 2
    assert preview["previewed_row_count"] == 2
    assert preview["can_transform_rows"] is True
    assert preview["row_data_validated"] is True
    assert preview["row_semantics_validated"] is False
    assert preview["uses_external_llm"] is False
    assert preview["will_mutate_files"] is False
    assert preview["will_solve"] is False
    assert preview["apply_plan"]["reason"] == "missing_required_fields"
    assert preview["transformed_headers"] == [
        "employee_id",
        "name",
        "roles",
        "hourly_cost",
    ]
    assert preview["transformed_rows"][0] == {
        "row_index": 0,
        "source": {
            "Staff ID": "E1",
            "Full Name": "Asha",
            "Skills": "worker|supervisor",
            "Cost Per Hour": "20",
        },
        "transformed": {
            "employee_id": "E1",
            "name": "Asha",
            "roles": "worker|supervisor",
            "hourly_cost": "20",
        },
        "transformed_values": ["E1", "Asha", "worker|supervisor", "20"],
        "errors": [],
    }
    assert preview["errors"] == []
    json.dumps(preview, sort_keys=True)
    assert headers == ["Staff ID", "Full Name", "Skills", "Cost Per Hour"]
    assert rows == [
        ["E1", "Asha", "worker|supervisor", "20"],
        ["E2", "Ravi", "worker", "18"],
    ]


def test_csv_row_transformation_preview_uses_valid_apply_plan() -> None:
    headers = ["Day Index", "Shift Name", "Required Role", "Headcount"]
    rows = [["0", "morning", "worker", "2"]]
    apply_plan = build_apply_plan(csv_type="demand", headers=headers)

    preview = csv_row_transformation_preview(
        csv_type="demand",
        headers=headers,
        rows=rows,
        apply_plan=apply_plan,
    )

    assert preview["status"] == "complete"
    assert preview["can_transform_rows"] is True
    assert preview["apply_plan"] == apply_plan
    assert preview["transformed_headers"] == ["day", "shift", "role", "required"]
    assert preview["transformed_rows"][0]["transformed"] == {
        "day": "0",
        "shift": "morning",
        "role": "worker",
        "required": "2",
    }


def test_csv_row_transformation_preview_marks_review_required_availability() -> None:
    preview = csv_row_transformation_preview(
        csv_type="employees",
        headers=[
            "Staff ID",
            "Full Name",
            "Skills",
            "Hourly Rate",
            "Weekly Hours Limit",
            "Available Monday Morning",
        ],
        rows=[["E1", "Asha", "worker", "20", "40", "yes"]],
    )

    assert preview["status"] == "needs_review"
    assert preview["can_transform_rows"] is False
    assert preview["apply_plan"]["reason"] == "requires_review"
    assert preview["transformed_headers"][-1] == "Available Monday Morning"
    assert preview["transformed_rows"][0]["transformed"][
        "Available Monday Morning"
    ] == "yes"


def test_csv_row_transformation_preview_rejects_invalid_rows() -> None:
    with pytest.raises(CsvMappingValidationError, match="rows must not be empty"):
        csv_row_transformation_preview(
            csv_type="demand",
            headers=["Day", "Shift", "Role", "Required"],
            rows=[],
        )
    with pytest.raises(CsvMappingValidationError, match="row 0 has 1 cell"):
        csv_row_transformation_preview(
            csv_type="demand",
            headers=["Day", "Shift"],
            rows=[["0"]],
        )
    with pytest.raises(CsvMappingValidationError, match="row 0 must contain only strings"):
        csv_row_transformation_preview(
            csv_type="demand",
            headers=["Day"],
            rows=[[0]],  # type: ignore[list-item]
        )


def test_transform_row_with_apply_plan_reports_duplicate_targets() -> None:
    headers = [
        "Staff ID",
        "Full Name",
        "Skills",
        "Hourly Rate",
        "Weekly Hours Limit",
        "Avail D0 S0",
        "Available Day0 Shift0",
    ]
    apply_plan = build_apply_plan(csv_type="employees", headers=headers)

    transformed = transform_row_with_apply_plan(
        headers=headers,
        row=["E1", "Asha", "worker", "20", "40", "yes", "no"],
        apply_plan=apply_plan,
    )
    rows = preview_transformed_rows(
        headers=headers,
        rows=[["E1", "Asha", "worker", "20", "40", "yes", "no"]],
        apply_plan=apply_plan,
    )

    assert transformed["errors"] == [
        {
            "row_index": 0,
            "type": "duplicate_target_header",
            "source_header": "Available Day0 Shift0",
            "target_header": "available_day0_shift0",
            "message": "Row 0 maps more than one source column to available_day0_shift0",
        }
    ]
    assert rows == [transformed]


def test_validate_row_preview_request_rejects_invalid_payload() -> None:
    with pytest.raises(CsvMappingValidationError, match="must be an object"):
        validate_row_preview_request([])  # type: ignore[arg-type]
    with pytest.raises(CsvMappingValidationError, match="must include rows"):
        validate_row_preview_request(
            {"csv_type": "demand", "headers": ["Day"]}
        )


def test_preview_column_renames_rejects_invalid_explicit_mapping() -> None:
    with pytest.raises(CsvMappingValidationError, match="unsupported field"):
        preview_column_renames(
            csv_type="employees",
            headers=["Staff ID"],
            mapping={"unknown": "Staff ID"},
        )
    with pytest.raises(CsvMappingValidationError, match="unknown header Missing"):
        preview_column_renames(
            csv_type="employees",
            headers=["Staff ID"],
            mapping={"employee_id": "Missing"},
        )
    with pytest.raises(CsvMappingValidationError, match="not both"):
        preview_column_renames(
            csv_type="employees",
            headers=["Staff ID"],
            mapping={"employee_id": "Staff ID"},
            mapping_report=suggest_employee_column_mapping(
                ["Staff ID", "Name", "Role", "Rate", "Max Hours", "Availability"]
            ),
        )


def test_csv_mapping_report_marks_needs_review_when_any_file_is_incomplete() -> None:
    report = csv_mapping_report(
        employee_headers=["Name"],
        demand_headers=["Day", "Shift", "Role", "Required"],
    )

    assert report["status"] == "needs_review"
    assert report["files"]["employees"]["valid"] is False
    assert report["files"]["demand"]["valid"] is True


def test_mapping_confidence_is_deterministic() -> None:
    first = mapping_confidence("Hourly Rate", "hourly_cost", "employees")
    second = mapping_confidence("Hourly Rate", "hourly_cost", "employees")

    assert first == second == 0.95
    assert mapping_confidence("Unknown", "hourly_cost", "employees") == 0.0


def test_mapper_rejects_invalid_headers() -> None:
    with pytest.raises(CsvMappingValidationError):
        suggest_employee_column_mapping([])
    with pytest.raises(CsvMappingValidationError):
        suggest_employee_column_mapping(["Employee ID", "employee-id"])
    with pytest.raises(CsvMappingError):
        suggest_employee_column_mapping(["Employee ID", 5])  # type: ignore[list-item]
