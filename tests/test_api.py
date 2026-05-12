from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

import httpx

from workforce_scheduling.api import app, solve_job_store
from workforce_scheduling.jobs import SOLVE_JOB_MAX_WORKERS, solve_job_executor


def _api_request(
    method: str,
    path: str,
    *,
    json_payload: object | None = None,
    content: str | None = None,
    headers: Dict[str, str] | None = None,
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
            "solve_jobs": "POST /solve-jobs",
            "solve_job_status": "GET /solve-jobs/{job_id}",
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
        },
        "response_envelope": {
            "success": {"ok": True, "result": "SolveResult payload"},
            "error": {"ok": False, "error": {"type": "string", "message": "string"}},
        },
        "job_execution": {
            "backend": "in_memory_thread_pool",
            "max_workers": 2,
        },
    }


def test_solve_job_executor_is_bounded() -> None:
    assert SOLVE_JOB_MAX_WORKERS == 2
    assert solve_job_executor._max_workers == SOLVE_JOB_MAX_WORKERS


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
        },
    }
