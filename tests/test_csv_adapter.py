from __future__ import annotations

import csv
import os
import subprocess
import sys
from pathlib import Path

from workforce_scheduling.csv_adapter import (
    CsvAdapterError,
    payload_from_csv_files,
    problem_data_from_csv_files,
    write_roster_solution_csv,
)
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
    assert len(rows) == 4
    assert {row["shift"] for row in rows} <= {"morning", "evening"}
    assert {row["employee_name"] for row in rows} <= {"Asha", "Ravi", "Meera"}
    assert all(row["shortage_count"] == "0" for row in rows)


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
    assert len(rows) == 4


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
    }


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
