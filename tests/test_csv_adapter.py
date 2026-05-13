from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

from workforce_scheduling.csv_adapter import (
    CsvAdapterError,
    METRIC_FIELDS,
    ROSTER_OUTPUT_HEADER,
    csv_rows_from_solve_response,
    payload_from_csv_files,
    problem_data_from_csv_files,
    write_roster_solution_csv,
    write_solve_response_csv,
)
from workforce_scheduling.schemas import solve_payload
from workforce_scheduling.solve import solve


def _write_csv_fixture(directory: Path) -> tuple[Path, Path, Path]:
    employees_csv = directory / "employees.csv"
    shifts_csv = directory / "shifts.csv"
    demand_csv = directory / "demand.csv"

    employees_csv.write_text(
        "\n".join(
            [
                (
                    "employee_id,name,roles,hourly_cost,max_weekly_hours,"
                    "available_day0_shift0,available_day0_shift1,"
                    "available_day1_shift0,available_day1_shift1"
                ),
                "0,Asha,worker|supervisor,20,40,true,true,true,false",
                "1,Ravi,worker,15,40,true,true,true,true",
                "2,Meera,worker,18,40,true,false,true,true",
            ]
        )
        + "\n"
    )
    shifts_csv.write_text(
        "\n".join(
            [
                "shift,shift_name,start_hour,end_hour",
                "0,morning,8,16",
                "1,evening,16,24",
            ]
        )
        + "\n"
    )
    demand_csv.write_text(
        "\n".join(
            [
                "day,shift,role,required",
                "0,0,worker,1",
                "0,1,supervisor,1",
                "1,0,worker,1",
                "1,1,worker,1",
            ]
        )
        + "\n"
    )
    return employees_csv, shifts_csv, demand_csv


def test_csv_adapter_builds_problem_data_and_writes_roster(tmp_path: Path) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    roster_csv = tmp_path / "roster.csv"

    data = problem_data_from_csv_files(
        employees_csv,
        shifts_csv,
        demand_csv,
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1000,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)
    write_roster_solution_csv(roster_csv, data, result.assignments, result.shortages)

    assert data.roles == ["worker", "supervisor"]
    assert data.days == [0, 1]
    assert data.shifts == ["morning", "evening"]
    assert data.shift_start_hours == [8, 16]
    assert data.shift_end_hours == [16, 24]
    assert data.min_rest_hours == 8
    assert data.max_consecutive_days == 5
    assert data.shortage_penalty == 1000
    assert data.employees[0].availability == [[True, True], [True, False]]
    assert result.metrics.status == "OPTIMAL"
    assert result.objective_breakdown.total_shortage == 0

    rows = list(csv.DictReader(roster_csv.open()))
    assert len(rows) == 12
    assert list(rows[0].keys()) == ROSTER_OUTPUT_HEADER
    assignment_rows = [
        row for row in rows if row["record_type"] == "assignment"
    ]
    shortage_rows = [row for row in rows if row["record_type"] == "shortage"]
    assert len(assignment_rows) == 4
    assert len(shortage_rows) == 8
    assert {row["shift"] for row in assignment_rows} <= {"0", "1"}
    assert {row["shift_name"] for row in assignment_rows} <= {"morning", "evening"}
    assert {row["name"] for row in assignment_rows} <= {"Asha", "Ravi", "Meera"}
    assert all(row["status"] == "assigned" for row in assignment_rows)
    assert all(row["value"] == "1" for row in assignment_rows)
    assert all(row["message"] == "" for row in assignment_rows)
    assert all(row["status"] == "unfilled" for row in shortage_rows)
    assert all(row["value"] == "0" for row in shortage_rows)
    assert all(row["message"] == "" for row in shortage_rows)


def test_cli_solves_from_three_csv_files_and_writes_roster(tmp_path: Path) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    roster_csv = tmp_path / "roster.csv"

    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [os.getcwd(), env.get("PYTHONPATH", "")]
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "workforce_scheduling.cli",
            "--employees-csv",
            str(employees_csv),
            "--shifts-csv",
            str(shifts_csv),
            "--demand-csv",
            str(demand_csv),
            "--roster-csv",
            str(roster_csv),
            "--time-limit",
            "5",
            "--seed",
            "1",
            "--min-rest-hours",
            "8",
            "--max-consecutive-days",
            "5",
            "--shortage-penalty",
            "1000",
        ],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "CSV roster written to:" in completed.stdout
    rows = list(csv.DictReader(roster_csv.open()))
    assert len(rows) == 24
    assert list(rows[0].keys()) == ROSTER_OUTPUT_HEADER
    metric_rows = [row for row in rows if row["record_type"] == "metric"]
    assert [row["status"] for row in metric_rows] == METRIC_FIELDS
    assert metric_rows[0]["value"] == "OPTIMAL"
    assert {
        row["status"]: row["value"]
        for row in metric_rows
        if row["status"]
        in {
            "total_shortage",
            "labor_cost_value",
            "workload_spread",
            "validation_violation_count",
        }
    } == {
        "total_shortage": "0",
        "labor_cost_value": "544",
        "workload_spread": "8",
        "validation_violation_count": "0",
    }
    assert not any(row["record_type"] == "summary" for row in rows)


def test_checked_in_csv_examples_parse_and_solve(tmp_path: Path) -> None:
    examples_dir = Path("examples/csv")
    roster_csv = tmp_path / "roster.csv"

    data = problem_data_from_csv_files(
        examples_dir / "employees.csv",
        examples_dir / "shifts.csv",
        examples_dir / "demand.csv",
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1000,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)
    write_roster_solution_csv(roster_csv, data, result.assignments, result.shortages)

    rows = list(csv.DictReader(roster_csv.open()))
    assert result.metrics.status == "OPTIMAL"
    assert result.objective_breakdown.total_shortage == 0
    assert list(rows[0].keys()) == ROSTER_OUTPUT_HEADER
    assert len([row for row in rows if row["record_type"] == "shortage"]) == 8


def test_csv_adapter_rejects_missing_explicit_availability_column(
    tmp_path: Path,
) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    employees_csv.write_text(
        "\n".join(
            [
                (
                    "employee_id,name,roles,hourly_cost,max_weekly_hours,"
                    "available_day0_shift0,available_day0_shift1,"
                    "available_day1_shift0"
                ),
                "0,Asha,worker|supervisor,20,40,true,true,true",
            ]
        )
        + "\n"
    )

    try:
        problem_data_from_csv_files(
            employees_csv,
            shifts_csv,
            demand_csv,
            min_rest_hours=8,
            max_consecutive_days=5,
            shortage_penalty=1000,
        )
    except CsvAdapterError as exc:
        assert "missing available_day1_shift1" in str(exc)
    else:
        raise AssertionError("Expected CsvAdapterError")


def test_csv_adapter_rejects_missing_shift_name(tmp_path: Path) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    shifts_csv.write_text(
        "\n".join(
            [
                "shift,shift_name,start_hour,end_hour",
                "0,morning,8,16",
                "1,,16,24",
            ]
        )
        + "\n"
    )

    try:
        problem_data_from_csv_files(
            employees_csv,
            shifts_csv,
            demand_csv,
            min_rest_hours=8,
            max_consecutive_days=5,
            shortage_penalty=1000,
        )
    except CsvAdapterError as exc:
        assert "missing shift_name" in str(exc)
    else:
        raise AssertionError("Expected CsvAdapterError")


def test_csv_adapter_rejects_non_consecutive_shift_ids(tmp_path: Path) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    shifts_csv.write_text(
        "\n".join(
            [
                "shift,shift_name,start_hour,end_hour",
                "0,morning,8,16",
                "2,evening,16,24",
            ]
        )
        + "\n"
    )

    try:
        problem_data_from_csv_files(
            employees_csv,
            shifts_csv,
            demand_csv,
            min_rest_hours=8,
            max_consecutive_days=5,
            shortage_penalty=1000,
        )
    except CsvAdapterError as exc:
        assert "consecutive zero-based" in str(exc)
    else:
        raise AssertionError("Expected CsvAdapterError")


def test_csv_adapter_payload_uses_explicit_solver_settings(
    tmp_path: Path,
) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    payload = payload_from_csv_files(
        employees_csv,
        shifts_csv,
        demand_csv,
        min_rest_hours=9,
        max_consecutive_days=4,
        shortage_penalty=1234,
        time_limit_sec=3.5,
        seed=7,
        use_warm_start=True,
    )

    assert payload["problem"]["min_rest_hours"] == 9
    assert payload["problem"]["max_consecutive_days"] == 4
    assert payload["problem"]["shortage_penalty"] == 1234
    assert payload["options"] == {
        "time_limit_sec": 3.5,
        "seed": 7,
        "use_warm_start": True,
        "response_mode": "debug",
    }


def test_csv_rows_from_solve_response_include_metric_rows_and_names(
    tmp_path: Path,
) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    response_payload = solve_payload(
        payload_from_csv_files(
            employees_csv,
            shifts_csv,
            demand_csv,
            min_rest_hours=8,
            max_consecutive_days=5,
            shortage_penalty=1000,
            time_limit_sec=5.0,
            seed=1,
            use_warm_start=False,
        )
    )

    rows = csv_rows_from_solve_response(
        response_payload,
        employee_names={0: "Asha", 1: "Ravi", 2: "Meera"},
        shift_names={0: "morning", 1: "evening"},
    )

    assert response_payload["ok"] is True
    metric_rows = [row for row in rows if row["record_type"] == "metric"]
    assert [row["status"] for row in metric_rows] == METRIC_FIELDS
    assert metric_rows[0]["value"] == "OPTIMAL"
    assert metric_rows[0]["message"] == "Solver metric: status"
    business_metric_rows = [
        row for row in metric_rows if row["status"] in {
            "total_shortage",
            "labor_cost_value",
            "workload_spread",
            "validation_violation_count",
        }
    ]
    assert [row["message"] for row in business_metric_rows] == [
        "Business metric: total_shortage",
        "Business metric: labor_cost_value",
        "Business metric: workload_spread",
        "Business metric: validation_violation_count",
    ]
    assert not any(row["record_type"] == "summary" for row in rows)
    assignment_rows = [
        row for row in rows if row["record_type"] == "assignment"
    ]
    shortage_rows = [row for row in rows if row["record_type"] == "shortage"]
    assert len(assignment_rows) == 4
    assert len(shortage_rows) == 8
    assert {row["name"] for row in assignment_rows} <= {
        "Asha",
        "Ravi",
        "Meera",
    }
    assert {row["shift_name"] for row in assignment_rows} <= {
        "morning",
        "evening",
    }
    assert all(row["value"] == 0 for row in shortage_rows)
    assert all(row["message"] == "" for row in shortage_rows)


def test_write_solve_response_csv_writes_error_rows(tmp_path: Path) -> None:
    output_csv = tmp_path / "response.csv"
    write_solve_response_csv(
        {
            "ok": False,
            "error": {
                "type": "SchemaValidationError",
                "message": "bad request",
            },
        },
        output_csv,
    )

    rows = list(csv.DictReader(output_csv.open()))
    assert list(rows[0].keys()) == ROSTER_OUTPUT_HEADER
    assert rows == [
        {
            "record_type": "error",
            "employee_id": "",
            "name": "",
            "day": "",
            "shift": "",
            "shift_name": "",
            "role": "",
            "status": "SchemaValidationError",
            "value": "",
            "message": "bad request",
        }
    ]


def test_csv_rows_from_solve_response_include_validation_rows() -> None:
    rows = csv_rows_from_solve_response(
        {
            "ok": True,
            "result": {
                "metrics": {"status": "OPTIMAL", "objective_value": 0.0},
                "assignments": [],
                "shortages": [],
                "violations": ["Employee 1 exceeds weekly hours"],
            },
        }
    )

    assert rows == [
        {
            "record_type": "metric",
            "employee_id": "",
            "name": "",
            "day": "",
            "shift": "",
            "shift_name": "",
            "role": "",
            "status": "status",
            "value": "OPTIMAL",
            "message": "Solver metric: status",
        },
        {
            "record_type": "metric",
            "employee_id": "",
            "name": "",
            "day": "",
            "shift": "",
            "shift_name": "",
            "role": "",
            "status": "objective_value",
            "value": 0.0,
            "message": "Solver metric: objective_value",
        },
        {
            "record_type": "metric",
            "employee_id": "",
            "name": "",
            "day": "",
            "shift": "",
            "shift_name": "",
            "role": "",
            "status": "validation_violation_count",
            "value": 1,
            "message": "Business metric: validation_violation_count",
        },
        {
            "record_type": "validation",
            "employee_id": "",
            "name": "",
            "day": "",
            "shift": "",
            "shift_name": "",
            "role": "",
            "status": "violation",
            "value": "",
            "message": "Employee 1 exceeds weekly hours",
        },
    ]


def test_csv_adapter_ignores_legacy_global_settings_in_shifts_csv(
    tmp_path: Path,
) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    shifts_csv.write_text(
        "\n".join(
            [
                (
                    "shift,shift_name,start_hour,end_hour,"
                    "min_rest_hours,max_consecutive_days,shortage_penalty"
                ),
                "0,morning,8,16,0,0,-1",
                "1,evening,16,24,0,0,-1",
            ]
        )
        + "\n"
    )

    data = problem_data_from_csv_files(
        employees_csv,
        shifts_csv,
        demand_csv,
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1000,
    )

    assert data.min_rest_hours == 8
    assert data.max_consecutive_days == 5
    assert data.shortage_penalty == 1000


def test_csv_roster_output_includes_shortage_records(tmp_path: Path) -> None:
    employees_csv, shifts_csv, demand_csv = _write_csv_fixture(tmp_path)
    roster_csv = tmp_path / "roster.csv"
    demand_csv.write_text(
        "\n".join(
            [
                "day,shift,role,required",
                "0,0,worker,1",
                "0,1,supervisor,1",
                "1,0,worker,1",
                "1,1,worker,2",
            ]
        )
        + "\n"
    )

    data = problem_data_from_csv_files(
        employees_csv,
        shifts_csv,
        demand_csv,
        min_rest_hours=8,
        max_consecutive_days=5,
        shortage_penalty=1000,
    )
    result = solve(data, time_limit_sec=5.0, seed=1)
    write_roster_solution_csv(roster_csv, data, result.assignments, result.shortages)

    rows = list(csv.DictReader(roster_csv.open()))
    shortage_rows = [row for row in rows if row["record_type"] == "shortage"]

    assert list(rows[0].keys()) == ROSTER_OUTPUT_HEADER
    positive_shortage_rows = [
        row for row in shortage_rows if row["value"] != "0"
    ]
    zero_shortage_rows = [row for row in shortage_rows if row["value"] == "0"]

    assert len(shortage_rows) == 8
    assert len(positive_shortage_rows) == 1
    assert len(zero_shortage_rows) == 7
    positive_shortage = positive_shortage_rows[0]
    assert positive_shortage["employee_id"] == ""
    assert positive_shortage["name"] == ""
    assert positive_shortage["day"] == "1"
    assert positive_shortage["shift"] in {"0", "1"}
    assert positive_shortage["shift_name"] in {"morning", "evening"}
    assert positive_shortage["role"] == "worker"
    assert positive_shortage["status"] == "unfilled"
    assert positive_shortage["value"] == "1"
    assert positive_shortage["message"] == "Unfilled demand for 1 worker slot(s)"
    assert all(row["status"] == "unfilled" for row in zero_shortage_rows)
    assert all(row["message"] == "" for row in zero_shortage_rows)
