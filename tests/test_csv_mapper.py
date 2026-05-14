from __future__ import annotations

import json

import pytest

from workforce_scheduling.csv_mapper import (
    CsvMappingError,
    CsvMappingValidationError,
    csv_mapping_report,
    mapping_confidence,
    normalize_header,
    suggest_demand_column_mapping,
    suggest_employee_column_mapping,
    suggest_shift_column_mapping,
    validate_mapping,
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
