from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import httpx
import pytest

from workforce_scheduling.api import (
    MAX_CSV_UPLOAD_BYTES,
    MAX_JSON_REQUEST_BYTES,
    app,
    solve_job_store,
)
from workforce_scheduling.jobs import (
    InMemorySolveJobStore,
    JobCapacityError,
    JobNotFoundError,
    MAX_ACTIVE_JOBS,
    MAX_RETAINED_JOBS,
    SOLVE_JOB_MAX_WORKERS,
    solve_job_executor,
)


def _api_request(
    method: str,
    path: str,
    *,
    json_payload: object | None = None,
    content: str | None = None,
    headers: Dict[str, str] | None = None,
    data: object | None = None,
    files: object | None = None,
) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(
                method,
                path,
                json=json_payload,
                content=content,
                headers=headers,
                data=data,
                files=files,
            )

    return asyncio.run(_request())


def _wait_for_terminal_job(status_url: str) -> httpx.Response:
    response = _api_request("GET", status_url)
    for _ in range(50):
        if response.json()["job"]["status"] in {"succeeded", "failed"}:
            return response
        time.sleep(0.05)
        response = _api_request("GET", status_url)
    return response


def _assert_utc_iso_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    return parsed


def _csv_upload_files() -> Dict[str, tuple[str, str, str]]:
    employees_csv = "\n".join(
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
    ) + "\n"
    shifts_csv = "\n".join(
        [
            "shift,shift_name,start_hour,end_hour",
            "0,morning,8,16",
            "1,evening,16,24",
        ]
    ) + "\n"
    demand_csv = "\n".join(
        [
            "day,shift,role,required",
            "0,0,worker,1",
            "0,1,supervisor,1",
            "1,0,worker,1",
            "1,1,worker,1",
        ]
    ) + "\n"
    return {
        "employees_csv": ("employees.csv", employees_csv, "text/csv"),
        "shifts_csv": ("shifts.csv", shifts_csv, "text/csv"),
        "demand_csv": ("demand.csv", demand_csv, "text/csv"),
    }


def test_api_metadata_endpoint_reports_contract_without_solving() -> None:
    response = _api_request("GET", "/metadata")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "service": "workforce_scheduling_solver",
        "schema_version": 1,
        "endpoints": {
            "health": "GET /health",
            "metadata": "GET /metadata",
            "solve": "POST /solve",
            "solve_csv": "POST /solve-csv",
            "solve_jobs": "POST /solve-jobs",
            "solve_job_status": "GET /solve-jobs/{job_id}",
            "viewer": "GET /viewer/",
            "viewer_example_employees_csv": (
                "GET /viewer/examples/employees.csv"
            ),
            "viewer_example_shifts_csv": "GET /viewer/examples/shifts.csv",
            "viewer_example_demand_csv": "GET /viewer/examples/demand.csv",
        },
        "csv_upload": {
            "file_fields": ["employees_csv", "shifts_csv", "demand_csv"],
            "response_media_type": "text/csv",
        },
        "solve_options": {
            "time_limit_sec": {
                "type": "number",
                "exclusive_minimum": 0,
                "maximum": 30.0,
                "default": 10.0,
            },
            "seed": {
                "type": "integer",
                "default": 1,
            },
            "use_warm_start": {
                "type": "boolean",
                "default": False,
            },
            "response_mode": {
                "type": "string",
                "allowed": ["compact", "standard", "debug"],
                "default": "debug",
            },
        },
        "response_envelope": {
            "success": {"ok": True, "result": "SolveResult payload"},
            "error": {
                "ok": False,
                "error": {
                    "type": "string",
                    "message": "string",
                    "request_id": "string",
                },
            },
        },
        "job_execution": {
            "backend": "in_memory_thread_pool",
            "max_workers": 2,
            "max_active_jobs": 10,
            "max_retained_jobs": 100,
        },
        "request_limits": {
            "max_json_request_bytes": 1_000_000,
            "max_csv_upload_bytes": 1_000_000,
        },
    }
    assert response.headers["x-request-id"]


def test_api_serves_static_roster_viewer() -> None:
    redirect_response = _api_request("GET", "/viewer")
    assert redirect_response.status_code == 307
    assert redirect_response.headers["location"] == "/viewer/"

    response = _api_request("GET", "/viewer/")

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["content-type"].startswith("text/html")
    assert "Roster Viewer" in response.text
    assert 'id="response-mode"' in response.text
    assert 'data-tab="issues"' in response.text
    assert "./app.js" in response.text

    app_js_response = _api_request("GET", "/viewer/app.js")
    assert app_js_response.status_code == 200
    assert app_js_response.headers["content-type"].startswith(
        "application/javascript"
    )
    assert "loadDemoCsvs" in app_js_response.text
    assert "applySelectedResponseMode" in app_js_response.text
    assert "responseError" in app_js_response.text
    assert "setBusy" in app_js_response.text
    assert "Solving JSON..." in app_js_response.text
    assert "Polling job..." in app_js_response.text
    assert "activateTab(\"issues\")" in app_js_response.text

    styles_response = _api_request("GET", "/viewer/styles.css")
    assert styles_response.status_code == 200
    assert styles_response.headers["content-type"].startswith("text/css")
    assert ".helper-text" in styles_response.text
    assert ".compact-field" in styles_response.text
    assert ".status-dot.busy" in styles_response.text


def test_api_serves_viewer_example_csv_files() -> None:
    expected_headers = {
        "/viewer/examples/employees.csv": (
            "employee_id,name,roles,hourly_cost,max_weekly_hours,"
        ),
        "/viewer/examples/shifts.csv": "shift,shift_name,start_hour,end_hour",
        "/viewer/examples/demand.csv": "day,shift,role,required",
    }

    for path, expected_header in expected_headers.items():
        response = _api_request("GET", path)

        assert response.status_code == 200
        assert response.headers["x-request-id"]
        assert response.headers["content-type"].startswith("text/csv")
        assert response.text.startswith(expected_header)


def test_api_preserves_incoming_request_id() -> None:
    response = _api_request(
        "GET",
        "/health",
        headers={"X-Request-ID": "req-123"},
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"] == "req-123"


def test_solve_job_executor_is_bounded() -> None:
    assert SOLVE_JOB_MAX_WORKERS == 2
    assert solve_job_executor._max_workers == SOLVE_JOB_MAX_WORKERS


def test_solve_job_store_prunes_oldest_terminal_jobs_at_retention_limit() -> None:
    store = InMemorySolveJobStore()
    first_job = store.create()
    store.mark_failed(first_job.job_id, {"type": "Error", "message": "first"})

    retained_jobs = []
    for index in range(MAX_RETAINED_JOBS - 1):
        job = store.create()
        store.mark_failed(
            job.job_id,
            {"type": "Error", "message": f"terminal-{index}"},
        )
        retained_jobs.append(job)

    new_job = store.create()

    assert store.retained_count() == MAX_RETAINED_JOBS
    with pytest.raises(JobNotFoundError):
        store.get(first_job.job_id)
    assert store.get(retained_jobs[0].job_id).status == "failed"
    assert store.get(new_job.job_id).status == "queued"


def test_solve_job_store_rejects_new_job_when_active_capacity_is_full() -> None:
    store = InMemorySolveJobStore()
    for index in range(MAX_ACTIVE_JOBS):
        job = store.create()
        if index % 2 == 0:
            store.mark_running(job.job_id)

    with pytest.raises(JobCapacityError) as exc_info:
        store.create()

    assert str(exc_info.value) == (
        f"In-memory solve job capacity is full at {MAX_ACTIVE_JOBS} active jobs"
    )
    assert store.active_count() == MAX_ACTIVE_JOBS
    assert store.retained_count() == MAX_ACTIVE_JOBS


def test_solve_job_store_terminal_jobs_do_not_count_against_active_capacity() -> None:
    store = InMemorySolveJobStore()
    for index in range(MAX_ACTIVE_JOBS):
        job = store.create()
        store.mark_failed(job.job_id, {"type": "Error", "message": str(index)})

    for _ in range(MAX_ACTIVE_JOBS):
        store.create()

    assert store.active_count() == MAX_ACTIVE_JOBS
    assert store.retained_count() == MAX_ACTIVE_JOBS * 2


def test_api_solve_job_boundary_returns_429_when_active_capacity_is_full() -> None:
    solve_job_store.clear()
    try:
        for _ in range(MAX_ACTIVE_JOBS):
            solve_job_store.create()

        response = _api_request(
            "POST",
            "/solve-jobs",
            json_payload={"options": {"seed": 1}},
        )

        assert response.status_code == 429
        response_payload = response.json()
        assert response_payload["ok"] is False
        assert response_payload["error"] == {
            "type": "JobCapacityError",
            "message": (
                f"In-memory solve job capacity is full at {MAX_ACTIVE_JOBS} "
                "active jobs"
            ),
            "request_id": response.headers["x-request-id"],
        }
    finally:
        solve_job_store.clear()


def test_api_solve_csv_endpoint_returns_roster_csv() -> None:
    response = _api_request(
        "POST",
        "/solve-csv",
        data={
            "min_rest_hours": "8",
            "max_consecutive_days": "5",
            "shortage_penalty": "1000",
            "time_limit_sec": "5",
            "seed": "1",
            "use_warm_start": "false",
        },
        files=_csv_upload_files(),
    )

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="roster.csv"'
    )
    rows = list(csv.DictReader(io.StringIO(response.text)))

    assert rows[0]["record_type"] == "metric"
    assert rows[0]["status"] == "status"
    assert rows[0]["value"] == "OPTIMAL"
    assert len([row for row in rows if row["record_type"] == "assignment"]) == 4
    assert len([row for row in rows if row["record_type"] == "shortage"]) == 8
    assert {
        row["name"]
        for row in rows
        if row["record_type"] == "assignment"
    } <= {"Asha", "Ravi", "Meera"}
    assert {
        row["shift_name"]
        for row in rows
        if row["record_type"] == "assignment"
    } <= {"morning", "evening"}


def test_api_solve_csv_endpoint_returns_error_envelope_for_invalid_csv() -> None:
    files = _csv_upload_files()
    files["shifts_csv"] = (
        "shifts.csv",
        "shift,shift_name,start_hour,end_hour\n0,morning,8,16\n1,,16,24\n",
        "text/csv",
    )

    response = _api_request(
        "POST",
        "/solve-csv",
        data={
            "min_rest_hours": "8",
            "max_consecutive_days": "5",
            "shortage_penalty": "1000",
            "time_limit_sec": "5",
            "seed": "1",
        },
        files=files,
    )

    assert response.status_code == 400
    assert response.json() == {
        "ok": False,
        "error": {
            "type": "CsvAdapterError",
            "message": "shifts row 3 missing shift_name",
            "request_id": response.headers["x-request-id"],
        },
    }


def test_api_solve_csv_endpoint_rejects_oversized_upload() -> None:
    files = _csv_upload_files()
    files["employees_csv"] = (
        "employees.csv",
        "x" * (MAX_CSV_UPLOAD_BYTES + 1),
        "text/csv",
    )

    response = _api_request(
        "POST",
        "/solve-csv",
        data={
            "min_rest_hours": "8",
            "max_consecutive_days": "5",
            "shortage_penalty": "1000",
            "time_limit_sec": "5",
            "seed": "1",
        },
        files=files,
    )

    assert response.status_code == 413
    assert response.json() == {
        "ok": False,
        "error": {
            "type": "CsvUploadTooLargeError",
            "message": f"employees_csv exceeds {MAX_CSV_UPLOAD_BYTES} bytes",
            "request_id": response.headers["x-request-id"],
        },
    }


def test_api_json_routes_reject_large_request_body() -> None:
    response = _api_request(
        "POST",
        "/solve",
        content=" " * (MAX_JSON_REQUEST_BYTES + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {
        "ok": False,
        "error": {
            "type": "RequestTooLargeError",
            "message": f"JSON request body exceeds {MAX_JSON_REQUEST_BYTES} bytes",
            "request_id": response.headers["x-request-id"],
        },
    }


def test_api_logs_request_and_solve_route_without_payloads(caplog) -> None:
    caplog.set_level(logging.INFO, logger="workforce_scheduling.api")

    response = _api_request(
        "POST",
        "/solve",
        json_payload={"options": {"seed": 1}},
        headers={"X-Request-ID": "log-request-1"},
    )

    assert response.status_code == 400
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "api_request method=POST path=/solve status_code=400 "
        "request_id=log-request-1 duration_ms="
        in message
        for message in messages
    )
    assert any(
        "solve_route route=solve method=POST path=/solve status_code=400 "
        "request_id=log-request-1 ok=False error_type=SchemaValidationError"
        in message
        for message in messages
    )


def test_api_solve_job_boundary_returns_submitted_job_and_result() -> None:
    solve_job_store.clear()
    fixture_path = Path(__file__).parent / "fixtures" / "solve_request_small.json"
    request_payload = json.loads(fixture_path.read_text())

    submit_response = _api_request(
        "POST",
        "/solve-jobs",
        json_payload=request_payload,
    )
    submit_payload = submit_response.json()

    assert submit_response.status_code == 202
    assert submit_payload["ok"] is True
    assert submit_payload["job"]["status"] == "queued"
    assert submit_payload["job"]["started_at"] is None
    assert submit_payload["job"]["finished_at"] is None
    assert submit_payload["job"]["duration_sec"] is None
    assert submit_payload["status_url"] == (
        f"/solve-jobs/{submit_payload['job']['job_id']}"
    )
    _assert_utc_iso_timestamp(submit_payload["job"]["created_at"])
    _assert_utc_iso_timestamp(submit_payload["job"]["updated_at"])

    status_response = _wait_for_terminal_job(submit_payload["status_url"])
    status_payload = status_response.json()
    finished_job = status_payload["job"]

    assert status_response.status_code == 200
    assert status_payload["ok"] is True
    assert finished_job["job_id"] == submit_payload["job"]["job_id"]
    assert finished_job["status"] == "succeeded"
    assert finished_job["result"]["metrics"]["status"] == "OPTIMAL"
    assert finished_job["result"]["objective_breakdown"]["total_shortage"] == 0
    started_at = _assert_utc_iso_timestamp(finished_job["started_at"])
    finished_at = _assert_utc_iso_timestamp(finished_job["finished_at"])
    assert finished_at >= started_at
    assert isinstance(finished_job["duration_sec"], float)
    assert finished_job["duration_sec"] >= 0


def test_api_solve_job_boundary_records_schema_errors_as_failed_jobs() -> None:
    solve_job_store.clear()

    submit_response = _api_request(
        "POST",
        "/solve-jobs",
        json_payload={"options": {"seed": 1}},
    )
    submit_payload = submit_response.json()
    status_response = _wait_for_terminal_job(submit_payload["status_url"])
    status_payload = status_response.json()

    assert submit_response.status_code == 202
    assert status_response.status_code == 200
    assert status_payload["job"]["status"] == "failed"
    _assert_utc_iso_timestamp(status_payload["job"]["started_at"])
    _assert_utc_iso_timestamp(status_payload["job"]["finished_at"])
    assert isinstance(status_payload["job"]["duration_sec"], float)
    assert status_payload["job"]["duration_sec"] >= 0
    assert status_payload["job"]["error"] == {
        "type": "SchemaValidationError",
        "message": "Solve request must contain a problem object",
    }


def test_api_solve_job_status_returns_error_for_unknown_job() -> None:
    response = _api_request("GET", "/solve-jobs/missing")

    assert response.status_code == 404
    assert response.json() == {
        "ok": False,
        "error": {
            "type": "JobNotFoundError",
            "message": "Unknown solve job missing",
            "request_id": response.headers["x-request-id"],
        },
    }
