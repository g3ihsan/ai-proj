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

import workforce_scheduling.api as api_module
import workforce_scheduling.recommendations as recommendations_module
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
from workforce_scheduling.recommendations import (
    ScenarioEvaluationError,
    ScenarioValidationError,
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


def _small_solve_request() -> Dict[str, object]:
    fixture_path = Path(__file__).parent / "fixtures" / "solve_request_small.json"
    return json.loads(fixture_path.read_text())


def _non_demanded_shift_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 1}
    ]
    return request_payload


def _multi_role_shift_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["employees"] = [
        {
            "employee_id": 0,
            "name": "E0",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[True], [True]],
        },
        {
            "employee_id": 1,
            "name": "E1",
            "roles": ["supervisor"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[True], [True]],
        },
    ]
    request_payload["problem"]["roles"] = ["worker", "supervisor"]
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 1},
        {"day": 0, "shift": 0, "role": "supervisor", "required": 1},
    ]
    return request_payload


def _shortage_reduction_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["employees"] = [
        {
            "employee_id": 0,
            "name": "E0",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[True]],
        },
        {
            "employee_id": 1,
            "name": "E1",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[False]],
        },
    ]
    request_payload["problem"]["days"] = [0]
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 2}
    ]
    return request_payload


def _multi_recommendation_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["employees"] = [
        {
            "employee_id": 0,
            "name": "E0",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[True]],
        },
        {
            "employee_id": 1,
            "name": "E1",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[False]],
        },
        {
            "employee_id": 2,
            "name": "E2",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[False]],
        },
        {
            "employee_id": 3,
            "name": "E3",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[False]],
        },
    ]
    request_payload["problem"]["days"] = [0]
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 4}
    ]
    return request_payload


def _max_hours_recommendation_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["employees"] = [
        {
            "employee_id": 0,
            "name": "E0",
            "roles": ["worker"],
            "hourly_cost": 20,
            "max_weekly_hours": 8,
            "availability": [[True, False], [True, False]],
        }
    ]
    request_payload["problem"]["days"] = [0, 1]
    request_payload["problem"]["shifts"] = ["morning", "evening"]
    request_payload["problem"]["shift_start_hours"] = [8, 16]
    request_payload["problem"]["shift_end_hours"] = [16, 24]
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 1},
        {"day": 0, "shift": 1, "role": "worker", "required": 0},
        {"day": 1, "shift": 0, "role": "worker", "required": 1},
        {"day": 1, "shift": 1, "role": "worker", "required": 0},
    ]
    return request_payload


def _temporary_employee_recommendation_solve_request() -> Dict[str, object]:
    request_payload = _small_solve_request()
    request_payload["problem"]["roles"] = ["worker", "supervisor"]
    request_payload["problem"]["employees"] = [
        {
            "employee_id": 0,
            "name": "E0",
            "roles": ["supervisor"],
            "hourly_cost": 20,
            "max_weekly_hours": 40,
            "availability": [[True]],
        }
    ]
    request_payload["problem"]["days"] = [0]
    request_payload["problem"]["demand"] = [
        {"day": 0, "shift": 0, "role": "worker", "required": 1},
        {"day": 0, "shift": 0, "role": "supervisor", "required": 0},
    ]
    return request_payload


def _explanation_request(
    request_payload: Dict[str, object],
    target: Dict[str, object],
) -> Dict[str, object]:
    return {"solve_request": request_payload, "target": target}


def _assert_query_error(response: httpx.Response, message: str) -> None:
    response_payload = response.json()
    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"] == {
        "type": "ExplanationQueryError",
        "message": message,
        "request_id": response.headers["x-request-id"],
    }


def _stable_solve_output(result_payload: Dict[str, object]) -> Dict[str, object]:
    metrics = dict(result_payload["metrics"])
    # Wall time is intentionally operational telemetry, not deterministic
    # solver output. The remaining metrics are stable for this tiny fixture.
    metrics.pop("wall_time_sec", None)
    return {
        "assignments": result_payload["assignments"],
        "shortages": result_payload["shortages"],
        "objective_breakdown": result_payload["objective_breakdown"],
        "metrics": metrics,
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
            "explain_summary": "POST /explain/summary",
            "explain_shortages": "POST /explain/shortages",
            "explain_assignment": "POST /explain/assignment",
            "explain_employee": "POST /explain/employee",
            "explain_shift": "POST /explain/shift",
            "explain_narrate": "POST /explain/narrate",
            "assistant_ask": "POST /assistant/ask",
            "recommendations": "POST /recommendations",
            "recommend_what_if": "POST /recommend/what-if",
            "csv_mapping_suggest": "POST /csv/mapping/suggest",
            "csv_mapping_preview": "POST /csv/mapping/preview",
            "csv_row_transformation_preview": (
                "POST /csv/mapping/rows/preview"
            ),
            "csv_canonical_export_preview": (
                "POST /csv/mapping/export/preview"
            ),
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
        "csv_mapper": {
            "source": "Deterministic CSV header mapping suggestions and previews",
            "csv_mapping_contract_version": 1,
            "max_preview_rows": 20,
            "uses_external_llm": False,
            "response_shape": {
                "ok": True,
                "result": (
                    "CSV mapping report, preview, row preview, or export preview"
                ),
            },
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
        "explanation_endpoints": {
            "source": "Solver Evidence Layer debug payload",
            "uses_llm": False,
            "response_shape": {"ok": True, "result": "Explanation payload"},
        },
        "narration_endpoint": {
            "source": "Deterministic explanation payload",
            "default_provider": "fake",
            "uses_external_llm_by_default": False,
            "response_shape": {"ok": True, "result": "Narration payload"},
            "available_providers": [
                {
                    "name": "fake",
                    "uses_external_llm": False,
                }
            ],
        },
        "assistant_endpoint": {
            "source": "Deterministic explanation and narration helpers",
            "uses_external_llm_by_default": False,
            "supported_intents": [
                "summary",
                "shortages",
                "assignment",
                "employee",
                "shift",
                "recommendations",
            ],
            "response_shape": {"ok": True, "result": "Assistant response"},
        },
        "recommendation_engine": {
            "source": "Deterministic scenario solves",
            "recommendation_type": "what_if",
            "recommendation_contract_version": 1,
            "uses_external_llm": False,
            "supported_goals": ["reduce_shortages"],
            "supported_scenario_types": [
                "set_availability",
                "increase_employee_max_hours",
                "add_temporary_employee",
            ],
            "max_scenarios": 5,
            "max_recommendations": 5,
            "response_shape": {
                "ok": True,
                "result": "Scenario recommendation payload",
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
    assert "CSV Mapping Wizard" in response.text
    assert 'id="mapping-csv-type"' in response.text
    assert 'id="preview-export"' in response.text
    assert 'id="copy-canonical-csv"' in response.text
    assert 'id="download-canonical-csv"' in response.text
    assert 'id="export-safety-flags"' in response.text
    assert 'id="clear-mapping-wizard"' in response.text
    assert "Load Sample" in response.text
    assert "Suggest Mapping" in response.text
    assert "Preview Mapping" in response.text
    assert "Preview Rows" in response.text
    assert "Preview Export" in response.text
    assert "Copy CSV" in response.text
    assert "Download CSV" in response.text
    assert "Preview only. No backend file write or solve." in response.text
    assert "Clear Wizard" in response.text
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
    assert "invalidJsonError" in app_js_response.text
    assert "Invalid JSON:" in app_js_response.text
    assert "Response mode update failed" in app_js_response.text
    assert "suggestCsvMapping" in app_js_response.text
    assert "clearMappingWizard" in app_js_response.text
    assert "validateMappingHeaders" in app_js_response.text
    assert "validateMappingRows" in app_js_response.text
    assert "Name every header cell before previewing." in app_js_response.text
    assert "Fix row length before previewing rows or export." in app_js_response.text
    assert "No canonical CSV export preview yet." in app_js_response.text
    assert "previewCanonicalExport" in app_js_response.text
    assert "copyCanonicalCsv" in app_js_response.text
    assert "downloadCanonicalCsv" in app_js_response.text
    assert "missingCanonicalCsvPreviewError" in app_js_response.text
    assert "MissingCanonicalCsvPreview" in app_js_response.text
    assert "No canonical CSV export preview is available to copy." in app_js_response.text
    assert "No canonical CSV export preview is available to download." in app_js_response.text
    assert "Canonical CSV copy unavailable" in app_js_response.text
    assert "Canonical CSV download unavailable" in app_js_response.text
    assert "setExportSafetyFlags" in app_js_response.text
    assert "navigator.clipboard.writeText" in app_js_response.text
    assert "Canonical CSV copied." in app_js_response.text
    assert "Canonical CSV downloaded." in app_js_response.text
    assert "canonicalCsvDownloadFilename" in app_js_response.text
    assert "canonical-${elements.mappingCsvType.value}-preview.csv" in app_js_response.text
    assert "Will write files:" in app_js_response.text
    assert "Will mutate files:" in app_js_response.text
    assert "Will solve:" in app_js_response.text
    assert "Uses external LLM:" in app_js_response.text
    assert "Row semantics validated:" in app_js_response.text
    assert 'metricCard("Can export", canExport)' in app_js_response.text
    assert 'metricCard("Reason", reason)' in app_js_response.text
    assert "/csv/mapping/export/preview" in app_js_response.text
    assert "CSV export preview loaded" in app_js_response.text
    assert "CSV mapping wizard cleared." in app_js_response.text

    styles_response = _api_request("GET", "/viewer/styles.css")
    assert styles_response.status_code == 200
    assert styles_response.headers["content-type"].startswith("text/css")
    assert ".helper-text" in styles_response.text
    assert ".compact-field" in styles_response.text
    assert ".status-dot.busy" in styles_response.text
    assert ".wizard-summary" in styles_response.text
    assert ".preview-field-header" in styles_response.text
    assert ".compact-button" in styles_response.text
    assert ".compact-actions" in styles_response.text
    assert ".export-safety-flags" in styles_response.text
    assert ".preview-box" in styles_response.text


def test_api_csv_mapping_suggest_returns_deterministic_report() -> None:
    payload = {
        "employee_headers": [
            "Employee Number",
            "Resource Name",
            "Job Title",
            "Cost Per Hour",
            "Weekly Max Hours",
            "Avail D0 S0",
        ],
        "demand_headers": [
            "Weekday",
            "Time Slot",
            "Coverage Role",
            "Workers Needed",
        ],
        "shift_headers": ["Shift Number", "Period Name", "From Hour", "To Hour"],
    }

    first = _api_request(
        "POST",
        "/csv/mapping/suggest",
        json_payload=payload,
    )
    second = _api_request(
        "POST",
        "/csv/mapping/suggest",
        json_payload=payload,
    )
    response_payload = first.json()
    result_payload = response_payload["result"]

    assert first.status_code == 200
    assert first.headers["x-request-id"]
    assert response_payload["ok"] is True
    assert first.json() == second.json()
    assert result_payload["type"] == "csv_mapping_report"
    assert result_payload["csv_mapping_contract_version"] == 1
    assert result_payload["status"] == "complete"
    assert result_payload["uses_external_llm"] is False
    assert result_payload["files"]["employees"]["mapping"]["employee_id"][
        "source_header"
    ] == "Employee Number"
    assert result_payload["files"]["employees"]["mapping"]["availability"][
        "source_headers"
    ] == ["Avail D0 S0"]
    assert result_payload["files"]["demand"]["mapping"]["required"][
        "source_header"
    ] == "Workers Needed"
    assert result_payload["files"]["shifts"]["mapping"]["shift_name"][
        "source_header"
    ] == "Period Name"
    json.dumps(response_payload, sort_keys=True)


def test_api_csv_mapping_suggest_accepts_single_dataset_request() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/suggest",
        json_payload={
            "csv_type": "employees",
            "headers": [
                "Staff ID",
                "Full Name",
                "Skills",
                "Hourly Rate",
                "Weekly Hours Limit",
                "Available Monday Morning",
            ],
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["type"] == "csv_mapping_report"
    assert result_payload["status"] == "complete"
    assert sorted(result_payload["files"]) == ["employees"]
    assert result_payload["files"]["employees"]["mapping"]["availability"][
        "source_headers"
    ] == ["Available Monday Morning"]


def test_api_csv_mapping_suggest_reports_needs_review_without_solving() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/suggest",
        json_payload={
            "employee_headers": ["Employee Name", "Role"],
            "demand_headers": ["Day", "Shift", "Role", "Required"],
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "needs_review"
    assert result_payload["files"]["employees"]["valid"] is False
    assert result_payload["files"]["employees"]["missing_fields"] == [
        "employee_id",
        "hourly_cost",
        "max_weekly_hours",
        "availability",
    ]
    assert "shifts" not in result_payload["files"]


@pytest.mark.parametrize(
    ("json_payload", "error_type", "message"),
    [
        ({}, "CsvMappingValidationError", "at least one CSV header list is required"),
        (
            {"employee_headers": []},
            "CsvMappingValidationError",
            "headers must not be empty",
        ),
        (
            {"employee_headers": ["Employee ID", "employee-id"]},
            "CsvMappingValidationError",
            "headers contain duplicate normalized values: employee_id",
        ),
        (
            {"employee_headers": "Employee ID"},
            "CsvMappingError",
            "headers must be a sequence of strings",
        ),
        (
            {"csv_type": "employees"},
            "CsvMappingValidationError",
            "CSV mapping request must include headers",
        ),
        (
            {"csv_type": "unknown", "headers": ["x"]},
            "CsvMappingValidationError",
            "Unsupported CSV mapping csv_type unknown",
        ),
    ],
)
def test_api_csv_mapping_suggest_rejects_invalid_headers(
    json_payload: Dict[str, object],
    error_type: str,
    message: str,
) -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/suggest",
        json_payload=json_payload,
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"] == {
        "type": error_type,
        "message": message,
        "request_id": response.headers["x-request-id"],
    }


def test_api_csv_mapping_suggest_rejects_oversized_json() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/suggest",
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


def test_api_csv_mapping_preview_returns_deterministic_apply_plan() -> None:
    payload = {
        "csv_type": "employees",
        "headers": [
            "Staff ID",
            "Full Name",
            "Skills",
            "Cost Per Hour",
            "Weekly Limit",
            "Available Day0 Shift0",
        ],
        "mapping": {
            "employee_id": "Staff ID",
            "name": "Full Name",
            "roles": "Skills",
            "hourly_cost": "Cost Per Hour",
            "max_weekly_hours": "Weekly Limit",
            "availability": ["Available Day0 Shift0"],
        },
    }

    first = _api_request("POST", "/csv/mapping/preview", json_payload=payload)
    second = _api_request("POST", "/csv/mapping/preview", json_payload=payload)
    response_payload = first.json()
    result_payload = response_payload["result"]

    assert first.status_code == 200
    assert first.headers["x-request-id"]
    assert response_payload["ok"] is True
    assert first.json() == second.json()
    assert result_payload["type"] == "csv_mapping_preview"
    assert result_payload["status"] == "complete"
    assert result_payload["uses_external_llm"] is False
    assert result_payload["will_mutate_files"] is False
    assert result_payload["will_solve"] is False
    assert result_payload["apply_plan"]["can_apply"] is True
    assert result_payload["apply_plan"]["reason"] == "ready"
    assert result_payload["apply_plan"]["adapter_readiness"] == {
        "scope": "headers_only",
        "headers_ready_for_csv_adapter": True,
        "row_data_validated": False,
        "reason": "ready",
    }
    assert result_payload["apply_plan"]["canonical_headers_after_apply"] == [
        "employee_id",
        "name",
        "roles",
        "hourly_cost",
        "max_weekly_hours",
        "available_day0_shift0",
    ]
    assert result_payload["apply_plan"]["column_renames"][0]["action"] == (
        "rename_column"
    )
    json.dumps(response_payload, sort_keys=True)


def test_api_csv_mapping_preview_can_infer_mapping_without_solving() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/preview",
        json_payload={
            "csv_type": "demand",
            "headers": [
                "Weekday",
                "Time Slot",
                "Coverage Role",
                "Workers Needed",
            ],
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "complete"
    assert result_payload["apply_plan"]["can_apply"] is True
    assert result_payload["apply_plan"]["reason"] == "ready"
    assert result_payload["apply_plan"]["canonical_headers_after_apply"] == [
        "day",
        "shift",
        "role",
        "required",
    ]
    assert result_payload["apply_plan"]["will_solve"] is False


def test_api_csv_mapping_preview_marks_day_name_availability_for_review() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/preview",
        json_payload={
            "csv_type": "employees",
            "headers": [
                "Staff ID",
                "Full Name",
                "Skills",
                "Hourly Rate",
                "Weekly Hours Limit",
                "Available Monday Morning",
            ],
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "needs_review"
    assert result_payload["apply_plan"]["can_apply"] is False
    assert result_payload["apply_plan"]["reason"] == "requires_review"
    assert result_payload["apply_plan"]["adapter_readiness"][
        "headers_ready_for_csv_adapter"
    ] is False
    availability_action = result_payload["apply_plan"]["column_renames"][-1]
    assert availability_action["action"] == "requires_review"
    assert availability_action["target_header"] is None
    assert result_payload["apply_plan"]["canonical_headers_after_apply"][-1] == (
        "Available Monday Morning"
    )


@pytest.mark.parametrize(
    ("json_payload", "message"),
    [
        ({}, "CSV mapping preview request csv_type must be a string"),
        (
            {"csv_type": "employees"},
            "CSV mapping preview request must include headers",
        ),
        (
            {"csv_type": "unknown", "headers": ["x"]},
            "Unsupported CSV mapping csv_type unknown",
        ),
        (
            {
                "csv_type": "employees",
                "headers": ["Staff ID"],
                "mapping": {"employee_id": "Missing"},
            },
            "mapping field employee_id references unknown header Missing",
        ),
        (
            {
                "csv_type": "demand",
                "headers": ["Day"],
                "mapping_report": {
                    "type": "csv_column_mapping",
                    "csv_type": "demand",
                    "uses_external_llm": True,
                },
            },
            "mapping_report must not use an external LLM",
        ),
    ],
)
def test_api_csv_mapping_preview_rejects_invalid_requests(
    json_payload: Dict[str, object],
    message: str,
) -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/preview",
        json_payload=json_payload,
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"] == {
        "type": "CsvMappingValidationError",
        "message": message,
        "request_id": response.headers["x-request-id"],
    }


def test_api_csv_mapping_preview_does_not_change_solve_csv_behavior() -> None:
    preview_response = _api_request(
        "POST",
        "/csv/mapping/preview",
        json_payload={
            "csv_type": "employees",
            "headers": [
                "Staff ID",
                "Full Name",
                "Skills",
                "Hourly Rate",
                "Weekly Hours Limit",
                "Available Monday Morning",
            ],
        },
    )
    solve_csv_response = _api_request(
        "POST",
        "/solve-csv",
        files=_csv_upload_files(),
        data={
            "min_rest_hours": "8",
            "max_consecutive_days": "5",
            "shortage_penalty": "1000",
            "time_limit_sec": "5",
            "seed": "1",
            "use_warm_start": "false",
        },
    )

    assert preview_response.status_code == 200
    assert solve_csv_response.status_code == 200
    assert solve_csv_response.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(solve_csv_response.text)))
    assert rows[0]["record_type"] == "metric"


def test_api_csv_row_transformation_preview_returns_deterministic_rows() -> None:
    payload = {
        "csv_type": "employees",
        "headers": ["Staff ID", "Full Name", "Skills", "Cost Per Hour"],
        "rows": [
            ["E1", "Asha", "worker|supervisor", "20"],
            ["E2", "Ravi", "worker", "18"],
        ],
        "mapping": {
            "employee_id": "Staff ID",
            "name": "Full Name",
            "roles": "Skills",
            "hourly_cost": "Cost Per Hour",
        },
    }

    first = _api_request(
        "POST",
        "/csv/mapping/rows/preview",
        json_payload=payload,
    )
    second = _api_request(
        "POST",
        "/csv/mapping/rows/preview",
        json_payload=payload,
    )
    response_payload = first.json()
    result_payload = response_payload["result"]

    assert first.status_code == 200
    assert response_payload["ok"] is True
    assert first.json() == second.json()
    assert result_payload["type"] == "csv_row_transformation_preview"
    assert result_payload["status"] == "needs_review"
    assert result_payload["limits"] == {
        "max_preview_rows": 20,
        "row_limit_reached": False,
    }
    assert result_payload["can_transform_rows"] is True
    assert result_payload["row_shape_validated"] is True
    assert result_payload["row_data_validated"] is True
    assert result_payload["required_values_checked"] is True
    assert result_payload["required_value_errors"] == []
    assert result_payload["row_semantics_validated"] is False
    assert result_payload["will_mutate_files"] is False
    assert result_payload["will_solve"] is False
    assert result_payload["apply_plan"]["reason"] == "missing_required_fields"
    assert result_payload["transformed_rows"][0]["transformed"] == {
        "employee_id": "E1",
        "name": "Asha",
        "roles": "worker|supervisor",
        "hourly_cost": "20",
    }
    assert result_payload["transformed_rows"][0]["status"] == "ready"
    assert result_payload["transformed_rows"][0]["errors"] == []
    assert result_payload["transformed_rows"][1]["status"] == "ready"
    assert result_payload["transformed_rows"][1]["errors"] == []
    assert result_payload["errors"] == []
    json.dumps(response_payload, sort_keys=True)


def test_api_csv_row_transformation_preview_accepts_apply_plan() -> None:
    preview_response = _api_request(
        "POST",
        "/csv/mapping/preview",
        json_payload={
            "csv_type": "demand",
            "headers": ["Day Index", "Shift Name", "Required Role", "Headcount"],
        },
    )
    apply_plan = preview_response.json()["result"]["apply_plan"]

    response = _api_request(
        "POST",
        "/csv/mapping/rows/preview",
        json_payload={
            "csv_type": "demand",
            "headers": ["Day Index", "Shift Name", "Required Role", "Headcount"],
            "rows": [["0", "morning", "worker", "2"]],
            "apply_plan": apply_plan,
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "complete"
    assert result_payload["transformed_headers"] == ["day", "shift", "role", "required"]
    assert result_payload["transformed_rows"][0]["transformed"] == {
        "day": "0",
        "shift": "morning",
        "role": "worker",
        "required": "2",
    }
    assert result_payload["transformed_rows"][0]["status"] == "ready"
    assert result_payload["transformed_rows"][0]["errors"] == []
    assert result_payload["row_shape_validated"] is True
    assert result_payload["required_values_checked"] is True
    assert result_payload["required_value_errors"] == []


def test_api_csv_row_transformation_preview_reports_required_value_errors() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/rows/preview",
        json_payload={
            "csv_type": "demand",
            "headers": ["Day", "Shift", "Role", "Required"],
            "rows": [["0", "morning", "", "2"]],
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]
    expected_error = {
        "row_index": 0,
        "type": "missing_required_value",
        "field": "role",
        "target_header": "role",
        "message": "Row 0 missing required value for role",
    }

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["status"] == "needs_review"
    assert result_payload["can_transform_rows"] is False
    assert result_payload["row_shape_validated"] is True
    assert result_payload["row_data_validated"] is True
    assert result_payload["required_values_checked"] is True
    assert result_payload["required_value_errors"] == [expected_error]
    assert result_payload["errors"] == [expected_error]
    assert result_payload["transformed_rows"][0]["status"] == "needs_review"
    assert result_payload["transformed_rows"][0]["errors"] == [expected_error]
    assert result_payload["row_semantics_validated"] is False


def test_api_csv_canonical_export_preview_returns_deterministic_csv() -> None:
    payload = {
        "csv_type": "demand",
        "headers": ["Day Index", "Shift Name", "Required Role", "Headcount"],
        "rows": [["0", "morning", "worker", "2"]],
    }

    first = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload=payload,
    )
    second = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload=payload,
    )
    response_payload = first.json()
    result_payload = response_payload["result"]

    assert first.status_code == 200
    assert response_payload["ok"] is True
    assert first.json() == second.json()
    assert result_payload["type"] == "csv_canonical_export_preview"
    assert result_payload["status"] == "complete"
    assert result_payload["can_export"] is True
    assert result_payload["export_ready_reason"] == "ready"
    assert result_payload["canonical_headers"] == ["day", "shift", "role", "required"]
    assert result_payload["canonical_rows"] == [["0", "morning", "worker", "2"]]
    assert result_payload["csv_text"] == "day,shift,role,required\n0,morning,worker,2\n"
    assert result_payload["line_count"] == 2
    assert result_payload["row_preview"]["type"] == "csv_row_transformation_preview"
    assert result_payload["row_semantics_validated"] is False
    assert result_payload["will_mutate_files"] is False
    assert result_payload["will_write_files"] is False
    assert result_payload["will_solve"] is False
    assert result_payload["errors"] == []
    json.dumps(response_payload, sort_keys=True)


def test_api_csv_canonical_export_preview_reports_row_errors() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload={
            "csv_type": "demand",
            "headers": ["Day", "Shift", "Role", "Required"],
            "rows": [["0", "morning", "", "2"]],
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]
    expected_error = {
        "row_index": 0,
        "type": "missing_required_value",
        "field": "role",
        "target_header": "role",
        "message": "Row 0 missing required value for role",
    }

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["status"] == "needs_review"
    assert result_payload["can_export"] is False
    assert result_payload["export_ready_reason"] == "row_errors"
    assert result_payload["csv_text"] == "day,shift,role,required\n0,morning,,2\n"
    assert result_payload["errors"] == [expected_error]
    assert result_payload["row_preview"]["errors"] == [expected_error]


def test_api_csv_canonical_export_preview_reports_incomplete_mapping_reason() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload={
            "csv_type": "employees",
            "headers": ["Staff ID", "Full Name", "Skills", "Cost Per Hour"],
            "rows": [["E1", "Asha", "worker", "20"]],
            "mapping": {
                "employee_id": "Staff ID",
                "name": "Full Name",
                "roles": "Skills",
                "hourly_cost": "Cost Per Hour",
            },
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "needs_review"
    assert result_payload["can_export"] is False
    assert result_payload["export_ready_reason"] == "row_preview_needs_review"
    assert result_payload["row_preview"]["apply_plan"]["reason"] == (
        "missing_required_fields"
    )
    assert result_payload["errors"] == []


def test_api_csv_canonical_export_preview_escapes_csv_text() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload={
            "csv_type": "employees",
            "headers": [
                "Staff ID",
                "Full Name",
                "Skills",
                "Hourly Rate",
                "Weekly Hours Limit",
                "Available Day0 Shift0",
            ],
            "rows": [['E1', 'Asha, "Lead"\nNight', "worker", "20", "40", "yes"]],
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "complete"
    assert result_payload["can_export"] is True
    assert result_payload["export_ready_reason"] == "ready"
    assert result_payload["csv_text"] == (
        "employee_id,name,roles,hourly_cost,max_weekly_hours,available_day0_shift0\n"
        'E1,"Asha, ""Lead""\n'
        'Night",worker,20,40,yes\n'
    )
    assert result_payload["will_write_files"] is False


def test_api_csv_canonical_export_preview_rejects_invalid_request() -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/export/preview",
        json_payload={"csv_type": "demand", "headers": ["Day"]},
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"] == {
        "type": "CsvMappingValidationError",
        "message": "CSV export preview request must include rows",
        "request_id": response.headers["x-request-id"],
    }


@pytest.mark.parametrize(
    ("json_payload", "message"),
    [
        ({}, "CSV row preview request csv_type must be a string"),
        (
            {"csv_type": "demand", "headers": ["Day"]},
            "CSV row preview request must include rows",
        ),
        (
            {"csv_type": "demand", "headers": ["Day"], "rows": []},
            "rows must not be empty",
        ),
        (
            {"csv_type": "demand", "headers": ["Day"], "rows": [["0", "extra"]]},
            "row 0 has 2 cell(s), expected 1",
        ),
        (
            {
                "csv_type": "demand",
                "headers": ["Day"],
                "rows": [["0"] for _ in range(21)],
            },
            "rows must contain at most 20 row(s)",
        ),
    ],
)
def test_api_csv_row_transformation_preview_rejects_invalid_requests(
    json_payload: Dict[str, object],
    message: str,
) -> None:
    response = _api_request(
        "POST",
        "/csv/mapping/rows/preview",
        json_payload=json_payload,
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"] == {
        "type": "CsvMappingValidationError",
        "message": message,
        "request_id": response.headers["x-request-id"],
    }


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


def test_api_explain_summary_returns_deterministic_explanation() -> None:
    request_payload = _small_solve_request()
    request_payload["options"]["response_mode"] = "compact"

    response = _api_request("POST", "/explain/summary", json_payload=request_payload)
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "summary_explanation"
    assert result_payload["status"] == "OPTIMAL"
    assert result_payload["evidence_contract_version"] == 1
    assert result_payload["message"] == (
        "The solver assigned 2 shifts with 0 total shortages."
    )
    assert result_payload["details"]["assignment_count"] == 2
    assert result_payload["details"]["objective_breakdown"]["total_shortage"] == 0


def test_api_explain_shortages_returns_contract_payload() -> None:
    response = _api_request(
        "POST",
        "/explain/shortages",
        json_payload=_small_solve_request(),
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "shortage_explanations"
    assert result_payload["evidence_contract_version"] == 1
    assert "shortages" in result_payload["details"]
    assert "total_shortage" in result_payload["details"]
    assert result_payload["details"]["total_shortage"] == 0


def test_api_explain_assignment_uses_targeted_evidence() -> None:
    request_payload = _small_solve_request()

    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload=_explanation_request(
            request_payload,
            {
                "employee_id": 0,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        ),
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["type"] == "assignment_explanation"
    assert result_payload["details"]["assignment"] == {
        "employee_id": 0,
        "day": 0,
        "shift": 0,
        "role": "worker",
    }
    assert "ASSIGNED_AVAILABLE" in result_payload["reason_codes"]


def test_api_explain_assignment_returns_non_assignment_explanation() -> None:
    request_payload = _small_solve_request()

    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload=_explanation_request(
            request_payload,
            {
                "employee_id": 1,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        ),
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["type"] == "non_assignment_explanation"
    assert result_payload["assigned"] is False
    assert result_payload["reason_codes"]
    assert result_payload["details"]["assigned_employee_ids"] == [0]


def test_api_explain_employee_returns_contract_payload() -> None:
    response = _api_request(
        "POST",
        "/explain/employee",
        json_payload=_explanation_request(
            _small_solve_request(),
            {"employee_id": 0},
        ),
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "employee_explanation"
    assert result_payload["evidence_contract_version"] == 1
    assert result_payload["details"]["employee_id"] == 0
    assert "assignments" in result_payload["details"]
    assert "non_assignments" in result_payload["details"]
    assert result_payload["details"]["assignments"]


def test_api_explain_shift_returns_contract_payload() -> None:
    response = _api_request(
        "POST",
        "/explain/shift",
        json_payload=_explanation_request(
            _small_solve_request(),
            {"day": 0, "shift": 0},
        ),
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "shift_explanation"
    assert result_payload["evidence_contract_version"] == 1
    assert result_payload["details"]["day"] == 0
    assert result_payload["details"]["shift"] == 0
    assert "demanded_slots" in result_payload["details"]
    assert "assignments" in result_payload["details"]
    assert "non_assignments" in result_payload["details"]
    assert "shortages" in result_payload["details"]


def test_api_explain_shift_supports_optional_role_filter() -> None:
    response = _api_request(
        "POST",
        "/explain/shift",
        json_payload=_explanation_request(
            _multi_role_shift_solve_request(),
            {"day": 0, "shift": 0, "role": "worker"},
        ),
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["details"]["role"] == "worker"
    assert {
        slot["role"]
        for slot in result_payload["details"]["demanded_slots"]
    } == {"worker"}
    assert {
        assignment["role"]
        for assignment in result_payload["details"]["assignments"]
    } <= {"worker"}
    assert {
        non_assignment["role"]
        for non_assignment in result_payload["details"]["non_assignments"]
    } <= {"worker"}


def test_api_explanation_endpoints_return_json_serializable_payloads() -> None:
    request_payload = _small_solve_request()
    endpoint_payloads = [
        ("/explain/summary", request_payload),
        ("/explain/shortages", request_payload),
        (
            "/explain/assignment",
            _explanation_request(
                request_payload,
                {"employee_id": 0, "day": 0, "shift": 0, "role": "worker"},
            ),
        ),
        (
            "/explain/employee",
            _explanation_request(request_payload, {"employee_id": 0}),
        ),
        (
            "/explain/shift",
            _explanation_request(request_payload, {"day": 0, "shift": 0}),
        ),
    ]

    for endpoint, payload in endpoint_payloads:
        response = _api_request("POST", endpoint, json_payload=payload)

        assert response.status_code == 200, endpoint
        json.dumps(response.json(), sort_keys=True)


def test_api_explanation_endpoints_are_deterministic_for_same_request() -> None:
    request_payload = _small_solve_request()
    endpoint_payloads = [
        ("/explain/summary", request_payload),
        (
            "/explain/employee",
            _explanation_request(request_payload, {"employee_id": 0}),
        ),
        (
            "/explain/shift",
            _explanation_request(request_payload, {"day": 0, "shift": 0}),
        ),
    ]

    for endpoint, payload in endpoint_payloads:
        first = _api_request("POST", endpoint, json_payload=payload).json()
        second = _api_request("POST", endpoint, json_payload=payload).json()

        assert first == second, endpoint


def test_api_explanation_endpoints_do_not_change_debug_solve_output() -> None:
    request_payload = _small_solve_request()

    before = _api_request("POST", "/solve", json_payload=request_payload).json()
    explain_response = _api_request(
        "POST",
        "/explain/employee",
        json_payload=_explanation_request(request_payload, {"employee_id": 0}),
    ).json()
    after = _api_request("POST", "/solve", json_payload=request_payload).json()

    assert before["ok"] is True
    assert explain_response["ok"] is True
    assert after["ok"] is True
    assert _stable_solve_output(before["result"]) == _stable_solve_output(
        after["result"]
    )


def test_api_explain_narrate_returns_fake_grounded_narration() -> None:
    explanation_response = _api_request(
        "POST",
        "/explain/summary",
        json_payload=_small_solve_request(),
    ).json()
    explanation = explanation_response["result"]

    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={"explanation": explanation},
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "explanation_narration"
    assert result_payload["source_explanation_type"] == "summary_explanation"
    assert result_payload["status"] == "OPTIMAL"
    assert result_payload["evidence_contract_version"] == 1
    assert result_payload["provider"] == {
        "name": "fake",
        "uses_external_llm": False,
    }
    assert explanation["message"] in result_payload["message"]
    assert "deterministic solver evidence" in result_payload["message"]
    json.dumps(response_payload, sort_keys=True)


def test_api_explain_narrate_accepts_explanation_envelope() -> None:
    explanation_response = _api_request(
        "POST",
        "/explain/employee",
        json_payload=_explanation_request(
            _small_solve_request(),
            {"employee_id": 0},
        ),
    ).json()

    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload=explanation_response,
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["source_explanation_type"] == "employee_explanation"
    assert result_payload["provider"]["uses_external_llm"] is False


def test_api_explain_narrate_accepts_solve_request_and_kind() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": _small_solve_request(),
            "kind": "summary",
            "provider": "fake",
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["source_explanation_type"] == "summary_explanation"
    assert result_payload["status"] == "OPTIMAL"
    assert result_payload["provider"] == {
        "name": "fake",
        "uses_external_llm": False,
    }
    assert "The solver assigned 2 shifts with 0 total shortages." in (
        result_payload["message"]
    )


def test_api_explain_narrate_accepts_solve_request_kind_and_target() -> None:
    target = {
        "employee_id": 0,
        "day": 0,
        "shift": 0,
        "role": "worker",
    }
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": _small_solve_request(),
            "kind": "assignment",
            "target": target,
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["source_explanation_type"] == "assignment_explanation"
    assert result_payload["reason_codes"] == [
        "ASSIGNED_AVAILABLE",
        "ASSIGNED_COST_CONTRIBUTION",
        "ASSIGNED_COVERED_DEMAND",
        "ASSIGNED_QUALIFIED",
        "ASSIGNED_REST_COMPATIBLE",
        "ASSIGNED_WITHIN_HOURS",
    ]
    assert result_payload["source"] == {
        "mode": "solve_request",
        "kind": "assignment",
        "target": target,
    }


def test_api_explain_narrate_preserves_query_error_for_bad_target() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": _small_solve_request(),
            "kind": "assignment",
            "target": {
                "employee_id": True,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationQueryError"
    assert response_payload["error"]["message"] == (
        "Explanation target field employee_id must be an integer"
    )


def test_api_explain_narrate_preserves_target_not_found_error() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": _small_solve_request(),
            "kind": "assignment",
            "target": {
                "employee_id": 99,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        },
    )
    response_payload = response.json()

    assert response.status_code == 404
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationTargetNotFoundError"


def test_api_explain_narrate_preserves_schema_error_for_bad_solve_request() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": {"options": {"seed": 1}},
            "kind": "summary",
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "SchemaValidationError"
    assert response_payload["error"]["message"] == (
        "Solve request must contain a problem object"
    )


def test_api_explain_narrate_rejects_unknown_kind() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={
            "solve_request": _small_solve_request(),
            "kind": "unknown",
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationNarrationError"
    assert response_payload["error"]["message"] == (
        "Narration kind must be one of assignment, employee, shift, shortages, summary"
    )


def test_api_explain_narrate_rejects_unknown_provider() -> None:
    explanation = _api_request(
        "POST",
        "/explain/summary",
        json_payload=_small_solve_request(),
    ).json()["result"]

    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={"explanation": explanation, "provider": "external"},
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationNarrationError"
    assert response_payload["error"]["message"] == (
        "Unsupported narration provider external; only fake is configured"
    )


def test_api_explain_narrate_is_deterministic() -> None:
    explanation = _api_request(
        "POST",
        "/explain/shift",
        json_payload=_explanation_request(
            _small_solve_request(),
            {"day": 0, "shift": 0},
        ),
    ).json()["result"]
    request_payload = {"explanation": explanation}

    first = _api_request(
        "POST",
        "/explain/narrate",
        json_payload=request_payload,
    ).json()
    second = _api_request(
        "POST",
        "/explain/narrate",
        json_payload=request_payload,
    ).json()

    assert first == second


def test_api_explain_narrate_rejects_invalid_payload() -> None:
    response = _api_request(
        "POST",
        "/explain/narrate",
        json_payload={"explanation": {"type": "summary_explanation"}},
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationNarrationError"
    assert "explanation missing required field(s)" in response_payload["error"][
        "message"
    ]
    assert response_payload["error"]["request_id"] == response.headers["x-request-id"]


def test_api_assistant_ask_routes_summary_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain this roster",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "assistant_response"
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["intent"] == {
        "kind": "summary",
        "supported": True,
        "target": {},
    }
    assert result_payload["explanation"]["type"] == "summary_explanation"
    assert result_payload["narration"]["source_explanation_type"] == (
        "summary_explanation"
    )
    assert "The solver assigned 2 shifts with 0 total shortages." in (
        result_payload["message"]
    )


def test_api_assistant_ask_routes_assignment_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Why was employee 0 assigned to day 0 shift 0 as worker?",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"] == {
        "kind": "assignment",
        "supported": True,
        "target": {
            "employee_id": 0,
            "day": 0,
            "shift": 0,
            "role": "worker",
        },
    }
    assert result_payload["explanation"]["type"] == "assignment_explanation"
    assert result_payload["provider"]["uses_external_llm"] is False


def test_api_assistant_ask_routes_shortage_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Are there any staffing shortages?",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "shortages"
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["explanation"]["type"] == "shortage_explanations"
    assert "shortages" in result_payload["explanation"]["details"]


def test_api_assistant_ask_routes_recommendation_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What if we want to reduce staffing shortages?",
            "solve_request": _shortage_reduction_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "assistant_response"
    assert result_payload["status"] == "OPTIMAL"
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["intent"] == {
        "kind": "recommendations",
        "supported": True,
        "target": {},
    }
    assert result_payload["narration"] is None
    assert result_payload["explanation"] is None
    assert result_payload["provider"] == {
        "name": "deterministic_recommendation_engine",
        "uses_external_llm": False,
    }
    assert result_payload["grounding"] == {
        "source": "deterministic_scenario_recommendations",
        "goal": "reduce_shortages",
        "recommendation_type": "what_if",
        "recommendation_contract_version": 1,
        "supported_scenario_types": [
            "set_availability",
            "increase_employee_max_hours",
            "add_temporary_employee",
        ],
        "uses_external_llm": False,
        "changes_solver_behavior": False,
    }
    assert result_payload["recommendation"]["type"] == "scenario_recommendations"
    assert result_payload["recommendation"]["recommendation_type"] == "what_if"
    assert result_payload["recommendation"]["summary"]["recommendation_count"] == 1
    assert result_payload["recommendation"]["recommendations"][0]["comparison"][
        "shortage_reduction"
    ] == 1
    assert result_payload["recommendation"]["recommendations"][0][
        "explanation"
    ]["manager_next_checks"] == [
        "Confirm the employee is actually available for the slot.",
        "Confirm the change is operationally feasible before editing the roster.",
        "Confirm this change follows local staffing policy.",
    ]
    assert result_payload["recommendation"]["recommendations"][0]["grounding"] == {
        "source": "deterministic_scenario_solve",
        "scenario_id": "make_employee_1_available_day_0_shift_0_role_worker",
        "scenario_type": "set_availability",
        "baseline_total_shortage": 1,
        "scenario_total_shortage": 0,
        "shortage_reduction": 1,
        "uses_external_llm": False,
    }
    assert "Best recommendation:" in result_payload["message"]
    assert "Next check:" in result_payload["message"]
    json.dumps(response_payload, sort_keys=True)


def test_api_assistant_recommendations_passes_limits_to_engine() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What should I change to reduce shortages?",
            "solve_request": _multi_recommendation_solve_request(),
            "limits": {
                "max_scenarios": 3,
                "max_recommendations": 1,
            },
        },
    )
    result_payload = response.json()["result"]
    recommendation_payload = result_payload["recommendation"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "recommendations"
    assert recommendation_payload["limits"] == {
        "max_scenarios": 3,
        "max_recommendations": 1,
        "scenario_limit_reached": False,
        "recommendation_limit_reached": True,
    }
    assert recommendation_payload["summary"]["generated_scenario_count"] == 3
    assert recommendation_payload["summary"]["generated_recommendation_count"] == 3
    assert recommendation_payload["summary"]["recommendation_count"] == 1
    assert recommendation_payload["summary"]["discarded_recommendation_count"] == 2
    assert [
        item["reason"]
        for item in recommendation_payload["discarded_recommendations"]
    ] == ["MAX_RECOMMENDATION_LIMIT", "MAX_RECOMMENDATION_LIMIT"]


def test_api_assistant_ask_routes_employee_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain employee 0",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "employee"
    assert result_payload["intent"]["target"]["employee_id"] == 0
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["explanation"]["type"] == "employee_explanation"


def test_api_assistant_ask_routes_shift_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain day 0 shift 0",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "shift"
    assert result_payload["intent"]["target"] == {"day": 0, "shift": 0}
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["explanation"]["type"] == "shift_explanation"


def test_api_assistant_ask_routes_employee_name_match() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Why was e0 assigned to day 0 shift 0 as worker?",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "assignment"
    assert result_payload["intent"]["target"]["employee_id"] == 0


def test_api_assistant_ask_uses_target_hint_when_question_is_sparse() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain this assignment",
            "solve_request": _small_solve_request(),
            "target": {
                "employee_id": 0,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"]["kind"] == "assignment"
    assert result_payload["explanation"]["type"] == "assignment_explanation"


def test_api_assistant_ask_explicit_target_overrides_question_text() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": (
                "Why was employee 1 assigned to day 2 shift 3 as supervisor?"
            ),
            "solve_request": _small_solve_request(),
            "target": {
                "employee_id": 0,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["intent"] == {
        "kind": "assignment",
        "supported": True,
        "target": {
            "employee_id": 0,
            "day": 0,
            "shift": 0,
            "role": "worker",
        },
    }
    assert result_payload["explanation"]["type"] == "assignment_explanation"


def test_api_assistant_ask_returns_unsupported_when_target_is_missing() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Why was this assignment made?",
            "solve_request": _small_solve_request(),
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["status"] == "unsupported"
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["intent"] == {
        "kind": "unsupported",
        "supported": False,
        "target": {},
        "missing_fields": ["employee_id", "day", "shift", "role"],
    }
    assert result_payload["narration"] is None
    assert result_payload["explanation"] is None


def test_api_assistant_ask_returns_unsupported_for_unrelated_question() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Can this tool make coffee?",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["status"] == "unsupported"
    assert result_payload["intent"]["kind"] == "unsupported"
    assert result_payload["answer"] == result_payload["message"]
    assert result_payload["narration"] is None
    assert result_payload["explanation"] is None


@pytest.mark.parametrize(
    "json_payload",
    [
        {"solve_request": _small_solve_request()},
        {"question": "", "solve_request": _small_solve_request()},
        {"question": "Explain this roster"},
        {
            "question": "Explain employee 0",
            "solve_request": _small_solve_request(),
            "target": "not-an-object",
        },
    ],
)
def test_api_assistant_ask_rejects_invalid_request_shape(
    json_payload: Dict[str, object],
) -> None:
    response = _api_request("POST", "/assistant/ask", json_payload=json_payload)
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "AssistantIntentError"


def test_api_assistant_ask_preserves_schema_error() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain this roster",
            "solve_request": {"options": {"seed": 1}},
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "SchemaValidationError"
    assert response_payload["error"]["message"] == (
        "Solve request must contain a problem object"
    )


def test_api_assistant_ask_preserves_target_not_found_error() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Why was employee 99 assigned to day 0 shift 0 as worker?",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 404
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationTargetNotFoundError"


def test_api_assistant_recommendations_rejects_invalid_limits() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What should I change to reduce shortages?",
            "solve_request": _shortage_reduction_solve_request(),
            "limits": {
                "max_scenarios": 1,
                "max_recommendations": 6,
            },
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "RecommendationError"


def test_api_assistant_recommendations_preserves_schema_error() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What should I change to reduce shortages?",
            "solve_request": {"options": {"seed": 1}},
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "SchemaValidationError"


def test_api_assistant_recommendations_maps_scenario_validation_error_to_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_scenario_validation(_payload):
        raise ScenarioValidationError("invalid scenario")

    monkeypatch.setattr(
        api_module,
        "assistant_response_from_request",
        _raise_scenario_validation,
    )

    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What should I change to reduce shortages?",
            "solve_request": _shortage_reduction_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ScenarioValidationError"


def test_api_assistant_recommendations_maps_scenario_evaluation_error_to_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_scenario_failure(_payload):
        raise ScenarioEvaluationError("scenario solve failed")

    monkeypatch.setattr(
        api_module,
        "assistant_response_from_request",
        _raise_scenario_failure,
    )

    response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "What should I change to reduce shortages?",
            "solve_request": _shortage_reduction_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 500
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ScenarioEvaluationError"


def test_api_assistant_ask_is_deterministic_and_json_serializable() -> None:
    json_payload = {
        "question": "Explain employee 0",
        "solve_request": _small_solve_request(),
    }

    first = _api_request("POST", "/assistant/ask", json_payload=json_payload).json()
    second = _api_request("POST", "/assistant/ask", json_payload=json_payload).json()

    assert first == second
    json.dumps(first, sort_keys=True)


def test_api_assistant_recommendation_response_is_deterministic_and_json_serializable() -> None:
    json_payload = {
        "question": "What should I change to reduce shortages?",
        "solve_request": _temporary_employee_recommendation_solve_request(),
    }

    first = _api_request("POST", "/assistant/ask", json_payload=json_payload).json()
    second = _api_request("POST", "/assistant/ask", json_payload=json_payload).json()

    assert first == second
    assert first["result"]["grounding"]["supported_scenario_types"] == [
        "set_availability",
        "increase_employee_max_hours",
        "add_temporary_employee",
    ]
    json.dumps(first, sort_keys=True)


def test_api_assistant_ask_does_not_change_debug_solve_output() -> None:
    request_payload = _small_solve_request()

    before = _api_request("POST", "/solve", json_payload=request_payload).json()
    assistant_response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Explain employee 0",
            "solve_request": request_payload,
        },
    ).json()
    after = _api_request("POST", "/solve", json_payload=request_payload).json()

    assert before["ok"] is True
    assert assistant_response["ok"] is True
    assert after["ok"] is True
    assert _stable_solve_output(before["result"]) == _stable_solve_output(
        after["result"]
    )


def test_api_assistant_recommendations_do_not_change_debug_solve_output() -> None:
    request_payload = _shortage_reduction_solve_request()

    before = _api_request("POST", "/solve", json_payload=request_payload).json()
    assistant_response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Recommend a way to fix shortages",
            "solve_request": request_payload,
        },
    ).json()
    after = _api_request("POST", "/solve", json_payload=request_payload).json()

    assert before["ok"] is True
    assert assistant_response["ok"] is True
    assert after["ok"] is True
    assert _stable_solve_output(before["result"]) == _stable_solve_output(
        after["result"]
    )


def test_api_assistant_temporary_employee_recommendation_does_not_change_debug_solve_output() -> None:
    request_payload = _temporary_employee_recommendation_solve_request()

    before = _api_request("POST", "/solve", json_payload=request_payload).json()
    assistant_response = _api_request(
        "POST",
        "/assistant/ask",
        json_payload={
            "question": "Recommend a way to fix shortages",
            "solve_request": request_payload,
        },
    ).json()
    after = _api_request("POST", "/solve", json_payload=request_payload).json()

    assert before["ok"] is True
    assert assistant_response["ok"] is True
    assert after["ok"] is True
    assert _stable_solve_output(before["result"]) == _stable_solve_output(
        after["result"]
    )


def test_api_recommendations_returns_grounded_shortage_reduction() -> None:
    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _shortage_reduction_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["type"] == "scenario_recommendations"
    assert result_payload["recommendation_type"] == "what_if"
    assert result_payload["recommendation_contract_version"] == 1
    assert result_payload["goal"] == "reduce_shortages"
    assert result_payload["baseline"]["total_shortage"] == 1
    assert result_payload["summary"]["generated_scenario_count"] == 1
    assert result_payload["summary"]["recommendation_count"] == 1
    assert result_payload["summary"]["discarded_scenario_count"] == 0
    assert result_payload["summary"]["discarded_recommendation_count"] == 0
    assert result_payload["discarded_scenarios"] == []
    assert result_payload["discarded_recommendations"] == []
    assert result_payload["limits"] == {
        "max_scenarios": 5,
        "max_recommendations": 5,
        "scenario_limit_reached": False,
        "recommendation_limit_reached": False,
    }
    assert result_payload["recommendations"][0]["comparison"]["shortage_reduction"] == 1
    assert result_payload["recommendations"][0]["changes"] == [
        {
            "type": "set_availability",
            "employee_id": 1,
            "day": 0,
            "shift": 0,
            "role": "worker",
            "from": False,
            "to": True,
        }
    ]
    assert result_payload["recommendations"][0]["explanation"][
        "expected_improvement"
    ] == "Total shortage decreases from 1 to 0."
    assert result_payload["recommendations"][0]["grounding"] == {
        "source": "deterministic_scenario_solve",
        "scenario_id": "make_employee_1_available_day_0_shift_0_role_worker",
        "scenario_type": "set_availability",
        "baseline_total_shortage": 1,
        "scenario_total_shortage": 0,
        "shortage_reduction": 1,
        "uses_external_llm": False,
    }
    assert result_payload["metadata"]["uses_external_llm"] is False
    assert result_payload["metadata"]["recommendation_type"] == "what_if"
    assert result_payload["metadata"]["supported_scenario_types"] == [
        "set_availability",
        "increase_employee_max_hours",
        "add_temporary_employee",
    ]
    json.dumps(response_payload, sort_keys=True)


def test_api_recommendations_returns_grounded_max_hours_reduction() -> None:
    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _max_hours_recommendation_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["baseline"]["total_shortage"] == 1
    assert result_payload["summary"]["generated_scenario_count"] == 1
    assert result_payload["summary"]["recommendation_count"] == 1
    assert result_payload["recommendations"][0]["changes"] == [
        {
            "type": "increase_employee_max_hours",
            "employee_id": 0,
            "day": 0,
            "shift": 0,
            "role": "worker",
            "from": 8,
            "to": 16,
            "increase_by": 8,
        }
    ]
    assert result_payload["recommendations"][0]["comparison"][
        "shortage_reduction"
    ] == 1
    assert result_payload["recommendations"][0]["explanation"]["tradeoffs"] == [
        "May increase workload or overtime risk for the employee."
    ]
    assert result_payload["recommendations"][0]["grounding"] == {
        "source": "deterministic_scenario_solve",
        "scenario_id": (
            "increase_employee_0_max_hours_to_16_for_day_0_shift_0_role_worker"
        ),
        "scenario_type": "increase_employee_max_hours",
        "baseline_total_shortage": 1,
        "scenario_total_shortage": 0,
        "shortage_reduction": 1,
        "uses_external_llm": False,
    }


def test_api_recommendations_returns_grounded_temporary_employee_reduction() -> None:
    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _temporary_employee_recommendation_solve_request(),
        },
    )
    response_payload = response.json()
    result_payload = response_payload["result"]

    assert response.status_code == 200
    assert response_payload["ok"] is True
    assert result_payload["baseline"]["total_shortage"] == 1
    assert result_payload["summary"]["generated_scenario_count"] == 1
    assert result_payload["summary"]["recommendation_count"] == 1
    assert result_payload["recommendations"][0]["changes"] == [
        {
            "type": "add_temporary_employee",
            "employee_id": 1,
            "name": "Temporary worker day 0 shift 0",
            "role": "worker",
            "day": 0,
            "shift": 0,
            "hourly_cost": 20,
            "max_weekly_hours": 8,
        }
    ]
    assert result_payload["recommendations"][0]["comparison"][
        "shortage_reduction"
    ] == 1
    assert result_payload["recommendations"][0]["explanation"] == {
        "why_it_helps": (
            "The baseline had an uncovered worker requirement on day 0 shift 0. "
            "No existing-employee scenario was available for that slot, so "
            "this scenario adds one qualified temporary employee and re-solves."
        ),
        "what_changes": [
            "Adds temporary employee 1 with role worker.",
            "Makes the temporary employee available only for day 0 shift 0.",
        ],
        "expected_improvement": "Total shortage decreases from 1 to 0.",
        "tradeoffs": [
            "May increase staffing cost because an additional employee is introduced.",
            "Total objective value increases by 2049 under the solver scoring model.",
        ],
        "manager_next_checks": [
            "Confirm a temporary worker is actually available.",
            "Confirm the temporary staffing cost is acceptable.",
            "Confirm the change is operationally feasible before editing the roster.",
            "Confirm this change follows local staffing policy.",
        ],
    }
    assert result_payload["recommendations"][0]["grounding"] == {
        "source": "deterministic_scenario_solve",
        "scenario_id": "add_temporary_employee_1_day_0_shift_0_role_worker",
        "scenario_type": "add_temporary_employee",
        "baseline_total_shortage": 1,
        "scenario_total_shortage": 0,
        "shortage_reduction": 1,
        "uses_external_llm": False,
    }


def test_api_recommend_what_if_alias_matches_recommendations() -> None:
    request_payload = {
        "goal": "reduce_shortages",
        "solve_request": _shortage_reduction_solve_request(),
    }

    recommendations = _api_request(
        "POST",
        "/recommendations",
        json_payload=request_payload,
    ).json()
    what_if = _api_request(
        "POST",
        "/recommend/what-if",
        json_payload=request_payload,
    ).json()

    assert what_if == recommendations
    assert what_if["result"]["metadata"]["supported_scenario_types"] == [
        "set_availability",
        "increase_employee_max_hours",
        "add_temporary_employee",
    ]


def test_api_recommendations_rejects_invalid_request() -> None:
    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "balance_weekends",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "RecommendationError"


@pytest.mark.parametrize(
    "json_payload",
    [
        {"goal": "reduce_shortages"},
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": "not-an-object",
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_scenarios": True},
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_scenarios": 0},
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_scenarios": 6},
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_recommendations": True},
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_recommendations": 0},
        },
        {
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
            "limits": {"max_recommendations": 6},
        },
    ],
)
def test_api_recommendations_rejects_invalid_contract_inputs(
    json_payload: Dict[str, object],
) -> None:
    response = _api_request(
        "POST",
        "/recommend/what-if",
        json_payload=json_payload,
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "RecommendationError"


def test_api_recommendations_accepts_limits_object() -> None:
    response = _api_request(
        "POST",
        "/recommend/what-if",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _shortage_reduction_solve_request(),
            "limits": {
                "max_scenarios": 1,
                "max_recommendations": 1,
            },
        },
    )
    result_payload = response.json()["result"]

    assert response.status_code == 200
    assert result_payload["limits"] == {
        "max_scenarios": 1,
        "max_recommendations": 1,
        "scenario_limit_reached": False,
        "recommendation_limit_reached": False,
    }


def test_api_recommendations_preserves_schema_error_status() -> None:
    response = _api_request(
        "POST",
        "/recommend/what-if",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": {"options": {"seed": 1}},
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "SchemaValidationError"


def test_api_recommendations_maps_scenario_validation_error_to_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_scenario_validation(_payload):
        raise ScenarioValidationError("invalid scenario change")

    monkeypatch.setattr(
        api_module,
        "recommendation_response_from_request",
        _raise_scenario_validation,
    )

    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ScenarioValidationError"


def test_api_recommendations_maps_invalid_temporary_employee_change_to_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _invalid_temporary_employee_candidates(_request_payload, _baseline_result):
        return [
            {
                "scenario_id": "bad_temporary_employee",
                "goal": "reduce_shortages",
                "title": "Bad temporary employee",
                "description": "Invalid duplicate temporary employee id.",
                "changes": [
                    {
                        "type": "add_temporary_employee",
                        "employee_id": 0,
                        "name": "Temporary worker day 0 shift 0",
                        "role": "worker",
                        "day": 0,
                        "shift": 0,
                        "hourly_cost": 20,
                        "max_weekly_hours": 8,
                    }
                ],
            }
        ]

    monkeypatch.setattr(
        recommendations_module,
        "_shortage_reduction_scenario_candidates",
        _invalid_temporary_employee_candidates,
    )

    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ScenarioValidationError"
    assert response_payload["error"]["message"] == "Employee 0 already exists"


def test_api_recommendations_maps_scenario_evaluation_error_to_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_scenario_failure(_payload):
        raise ScenarioEvaluationError("scenario solve failed")

    monkeypatch.setattr(
        api_module,
        "recommendation_response_from_request",
        _raise_scenario_failure,
    )

    response = _api_request(
        "POST",
        "/recommendations",
        json_payload={
            "goal": "reduce_shortages",
            "solve_request": _small_solve_request(),
        },
    )
    response_payload = response.json()

    assert response.status_code == 500
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ScenarioEvaluationError"


def test_api_recommendations_are_deterministic_and_do_not_mutate_solve_output() -> None:
    request_payload = _shortage_reduction_solve_request()
    recommendation_request = {
        "goal": "reduce_shortages",
        "solve_request": request_payload,
    }

    before = _api_request("POST", "/solve", json_payload=request_payload).json()
    first = _api_request(
        "POST",
        "/recommendations",
        json_payload=recommendation_request,
    ).json()
    second = _api_request(
        "POST",
        "/recommendations",
        json_payload=recommendation_request,
    ).json()
    after = _api_request("POST", "/solve", json_payload=request_payload).json()

    assert first == second
    assert _stable_solve_output(before["result"]) == _stable_solve_output(
        after["result"]
    )


def test_api_explain_assignment_returns_404_for_missing_target() -> None:
    request_payload = _small_solve_request()

    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload=_explanation_request(
            request_payload,
            {
                "employee_id": 99,
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        ),
    )
    response_payload = response.json()

    assert response.status_code == 404
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationTargetNotFoundError"
    assert response_payload["error"]["request_id"] == response.headers["x-request-id"]


def test_api_explain_employee_returns_404_for_unknown_employee() -> None:
    response = _api_request(
        "POST",
        "/explain/employee",
        json_payload=_explanation_request(
            _small_solve_request(),
            {"employee_id": 99},
        ),
    )
    response_payload = response.json()

    assert response.status_code == 404
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationTargetNotFoundError"


def test_api_explain_shift_returns_404_for_valid_non_demanded_shift() -> None:
    response = _api_request(
        "POST",
        "/explain/shift",
        json_payload=_explanation_request(
            _non_demanded_shift_solve_request(),
            {"day": 1, "shift": 0},
        ),
    )
    response_payload = response.json()

    assert response.status_code == 404
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationTargetNotFoundError"


def test_api_explain_assignment_returns_query_error_for_bad_target() -> None:
    request_payload = _small_solve_request()

    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload=_explanation_request(
            request_payload,
            {
                "employee_id": "not-an-int",
                "day": 0,
                "shift": 0,
                "role": "worker",
            },
        ),
    )
    _assert_query_error(
        response,
        "Explanation target field employee_id must be an integer",
    )


def test_api_explain_assignment_returns_query_error_for_missing_role() -> None:
    request_payload = _small_solve_request()

    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload=_explanation_request(
            request_payload,
            {
                "employee_id": 0,
                "day": 0,
                "shift": 0,
            },
        ),
    )
    response_payload = response.json()

    assert response.status_code == 400
    assert response_payload["ok"] is False
    assert response_payload["error"]["type"] == "ExplanationQueryError"
    assert response_payload["error"]["message"] == (
        "Missing explanation target field(s): role"
    )


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("not-an-object", "Explanation target must be an object"),
        (
            {"employee_id": True, "day": 0, "shift": 0, "role": "worker"},
            "Explanation target field employee_id must be an integer",
        ),
        (
            {"employee_id": 0, "day": "not-an-int", "shift": 0, "role": "worker"},
            "Explanation target field day must be an integer",
        ),
        (
            {"employee_id": 0, "day": 0, "shift": "not-an-int", "role": "worker"},
            "Explanation target field shift must be an integer",
        ),
        (
            {"employee_id": 0, "day": 0, "shift": 0, "role": ""},
            "Explanation target field role must be a non-empty string",
        ),
    ],
)
def test_api_explain_assignment_validates_target_shape(
    target: object,
    message: str,
) -> None:
    response = _api_request(
        "POST",
        "/explain/assignment",
        json_payload={
            "solve_request": _small_solve_request(),
            "target": target,
        },
    )

    _assert_query_error(response, message)


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


def test_api_assistant_rejects_large_request_body() -> None:
    response = _api_request(
        "POST",
        "/assistant/ask",
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
