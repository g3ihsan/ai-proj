from __future__ import annotations

import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .assistant import SUPPORTED_ASSISTANT_KINDS, assistant_response_from_request
from .ai_explanations import (
    ExplanationNarrationError,
    narration_provider_from_name,
    narration_provider_metadata,
    narrate_explanation,
)
from .csv_adapter import (
    DEFAULT_MAX_CONSECUTIVE_DAYS,
    DEFAULT_MIN_REST_HOURS,
    DEFAULT_SHORTAGE_PENALTY,
    payload_from_csv_files,
    write_solve_response_csv,
)
from .csv_mapper import (
    CSV_MAPPING_CONTRACT_VERSION,
    CSV_TYPE_DEMAND,
    CSV_TYPE_EMPLOYEES,
    CSV_TYPE_SHIFTS,
    CsvMappingValidationError,
    MAX_PREVIEW_ROWS,
    csv_canonical_export_preview,
    csv_row_transformation_preview,
    csv_mapping_preview,
    csv_mapping_report,
    validate_export_preview_request,
    validate_row_preview_request,
)
from .explanations import (
    ExplanationQueryError,
    ExplanationTargetNotFoundError,
    explain_assignment,
    explain_employee,
    explain_shift,
    explain_shortages,
    explain_summary,
    solve_request_to_explanation_payload,
)
from .forecasting import (
    FORECAST_CONTRACT_VERSION,
    FORECAST_METHOD_HISTORICAL_AVERAGE,
    MAX_FORECAST_SLOTS,
    MAX_HISTORICAL_DEMAND_RECORDS,
    SUPPORTED_FORECAST_METHODS,
    forecast_response_from_request,
    forecast_to_demand_preview,
)
from .jobs import (
    InMemorySolveJobStore,
    JobCapacityError,
    JobNotFoundError,
    JobStoreFullError,
    MAX_ACTIVE_JOBS,
    MAX_RETAINED_JOBS,
    SOLVE_JOB_MAX_WORKERS,
    job_payload,
    submit_solve_job,
)
from .recommendations import (
    MAX_RECOMMENDATIONS,
    MAX_RECOMMENDATION_SCENARIOS,
    RECOMMENDATION_CONTRACT_VERSION,
    RECOMMENDATION_TYPE_WHAT_IF,
    SUPPORTED_RECOMMENDATION_GOALS,
    SUPPORTED_SCENARIO_TYPES,
    recommendation_response_from_request,
)
from .schemas import (
    MAX_TIME_LIMIT_SEC,
    RESPONSE_MODES,
    SCHEMA_VERSION,
    SchemaValidationError,
    SolveOptions,
    error_payload,
    parse_solve_request,
    solve_payload,
)


REQUEST_ID_HEADER = "X-Request-ID"
MAX_JSON_REQUEST_BYTES = 1_000_000
MAX_CSV_UPLOAD_BYTES = 1_000_000
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
EXAMPLES_CSV_DIR = Path(__file__).resolve().parent.parent / "examples" / "csv"
logger = logging.getLogger(__name__)
app = FastAPI(title="Workforce Scheduling Solver")
solve_job_store = InMemorySolveJobStore()


class RequestTooLargeError(Exception):
    pass


class CsvUploadTooLargeError(Exception):
    pass


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid4().hex
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        response = JSONResponse(
            content=_error_payload_for_request(exc, request),
            status_code=500,
        )

    duration_ms = (time.perf_counter() - started) * 1000
    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "api_request method=%s path=%s status_code=%s request_id=%s duration_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        request_id,
        duration_ms,
    )
    return response


@app.get("/health")
async def health() -> dict[str, bool | str]:
    return {"ok": True, "service": "workforce_scheduling_solver"}


@app.get("/viewer")
async def viewer_index_without_slash() -> RedirectResponse:
    return RedirectResponse(url="/viewer/", status_code=307)


@app.get("/viewer/")
async def viewer_index() -> Response:
    return _frontend_response("index.html", "text/html")


@app.get("/viewer/app.js")
async def viewer_app_js() -> Response:
    return _frontend_response("app.js", "application/javascript")


@app.get("/viewer/styles.css")
async def viewer_styles_css() -> Response:
    return _frontend_response("styles.css", "text/css")


@app.get("/viewer/examples/employees.csv")
async def viewer_example_employees_csv() -> Response:
    return _example_csv_response("employees.csv")


@app.get("/viewer/examples/shifts.csv")
async def viewer_example_shifts_csv() -> Response:
    return _example_csv_response("shifts.csv")


@app.get("/viewer/examples/demand.csv")
async def viewer_example_demand_csv() -> Response:
    return _example_csv_response("demand.csv")


@app.get("/metadata")
async def metadata() -> dict[str, Any]:
    default_options = SolveOptions()
    return {
        "ok": True,
        "service": "workforce_scheduling_solver",
        "schema_version": SCHEMA_VERSION,
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
            "forecast_demand": "POST /forecast/demand",
            "forecast_demand_preview": "POST /forecast/demand/preview",
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
            "csv_mapping_contract_version": CSV_MAPPING_CONTRACT_VERSION,
            "max_preview_rows": MAX_PREVIEW_ROWS,
            "uses_external_llm": False,
            "response_shape": {
                "ok": True,
                "result": (
                    "CSV mapping report, preview, row preview, or export preview"
                ),
            },
        },
        "forecasting": {
            "source": "Deterministic historical demand baseline",
            "forecast_contract_version": FORECAST_CONTRACT_VERSION,
            "default_method": FORECAST_METHOD_HISTORICAL_AVERAGE,
            "supported_methods": list(SUPPORTED_FORECAST_METHODS),
            "max_historical_demand_records": MAX_HISTORICAL_DEMAND_RECORDS,
            "max_forecast_slots": MAX_FORECAST_SLOTS,
            "uses_external_ml": False,
            "uses_external_llm": False,
            "will_solve": False,
            "will_mutate_solver_request": False,
            "forecast_to_demand_preview": {
                "endpoint": "POST /forecast/demand/preview",
                "will_solve": False,
                "will_mutate_solver_request": False,
                "will_write_files": False,
                "response_shape": {
                    "ok": True,
                    "result": "Forecast-to-demand preview payload",
                },
            },
            "response_shape": {
                "ok": True,
                "result": "Demand forecast payload",
            },
        },
        "solve_options": {
            "time_limit_sec": {
                "type": "number",
                "exclusive_minimum": 0,
                "maximum": MAX_TIME_LIMIT_SEC,
                "default": default_options.time_limit_sec,
            },
            "seed": {
                "type": "integer",
                "default": default_options.seed,
            },
            "use_warm_start": {
                "type": "boolean",
                "default": default_options.use_warm_start,
            },
            "response_mode": {
                "type": "string",
                "allowed": list(RESPONSE_MODES),
                "default": default_options.response_mode,
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
            "uses_external_llm_by_default": False,
            "response_shape": {"ok": True, "result": "Narration payload"},
            **narration_provider_metadata(),
        },
        "assistant_endpoint": {
            "source": "Deterministic explanation and narration helpers",
            "uses_external_llm_by_default": False,
            "supported_intents": list(SUPPORTED_ASSISTANT_KINDS),
            "response_shape": {"ok": True, "result": "Assistant response"},
        },
        "recommendation_engine": {
            "source": "Deterministic scenario solves",
            "recommendation_type": RECOMMENDATION_TYPE_WHAT_IF,
            "recommendation_contract_version": RECOMMENDATION_CONTRACT_VERSION,
            "uses_external_llm": False,
            "supported_goals": list(SUPPORTED_RECOMMENDATION_GOALS),
            "supported_scenario_types": list(SUPPORTED_SCENARIO_TYPES),
            "max_scenarios": MAX_RECOMMENDATION_SCENARIOS,
            "max_recommendations": MAX_RECOMMENDATIONS,
            "response_shape": {
                "ok": True,
                "result": "Scenario recommendation payload",
            },
        },
        "job_execution": {
            "backend": "in_memory_thread_pool",
            "max_workers": SOLVE_JOB_MAX_WORKERS,
            "max_active_jobs": MAX_ACTIVE_JOBS,
            "max_retained_jobs": MAX_RETAINED_JOBS,
        },
        "request_limits": {
            "max_json_request_bytes": MAX_JSON_REQUEST_BYTES,
            "max_csv_upload_bytes": MAX_CSV_UPLOAD_BYTES,
        },
    }


@app.post("/solve")
async def solve_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = solve_payload(request_payload)
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(request, "solve", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/explain/summary")
async def explain_summary_endpoint(request: Request) -> JSONResponse:
    return await _explanation_endpoint(request, "explain_summary", explain_summary)


@app.post("/explain/shortages")
async def explain_shortages_endpoint(request: Request) -> JSONResponse:
    return await _explanation_endpoint(
        request,
        "explain_shortages",
        explain_shortages,
    )


@app.post("/explain/assignment")
async def explain_assignment_endpoint(request: Request) -> JSONResponse:
    return await _explanation_endpoint(
        request,
        "explain_assignment",
        explain_assignment,
        required_target_keys=("employee_id", "day", "shift", "role"),
    )


@app.post("/explain/employee")
async def explain_employee_endpoint(request: Request) -> JSONResponse:
    return await _explanation_endpoint(
        request,
        "explain_employee",
        explain_employee,
        required_target_keys=("employee_id",),
    )


@app.post("/explain/shift")
async def explain_shift_endpoint(request: Request) -> JSONResponse:
    return await _explanation_endpoint(
        request,
        "explain_shift",
        explain_shift,
        required_target_keys=("day", "shift"),
    )


@app.post("/explain/narrate")
async def explain_narrate_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        explanation_payload, source_payload = _narration_source_from_payload(
            request_payload
        )
        provider = narration_provider_from_name(
            _narration_provider_name_from_payload(request_payload)
        )
        result_payload = narrate_explanation(explanation_payload, provider)
        if source_payload is not None:
            result_payload["source"] = source_payload
        response_payload = {
            "ok": True,
            "result": result_payload,
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    if _error_type(response_payload) == "ExplanationTargetNotFoundError":
        status_code = 404
    if _error_type(response_payload) == "NarrationProviderError":
        status_code = 502
    _log_solve_route(request, "explain_narrate", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/assistant/ask")
async def assistant_ask_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": assistant_response_from_request(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    if _error_type(response_payload) == "ExplanationTargetNotFoundError":
        status_code = 404
    if _error_type(response_payload) == "NarrationProviderError":
        status_code = 502
    if _error_type(response_payload) == "ScenarioEvaluationError":
        status_code = 500
    _log_solve_route(request, "assistant_ask", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/recommendations")
async def recommendations_endpoint(request: Request) -> JSONResponse:
    return await _recommendations_endpoint(request, "recommendations")


@app.post("/recommend/what-if")
async def recommend_what_if_endpoint(request: Request) -> JSONResponse:
    return await _recommendations_endpoint(request, "recommend_what_if")


@app.post("/csv/mapping/suggest")
async def csv_mapping_suggest_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": _csv_mapping_report_from_payload(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(request, "csv_mapping_suggest", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/csv/mapping/preview")
async def csv_mapping_preview_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": _csv_mapping_preview_from_payload(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(request, "csv_mapping_preview", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/csv/mapping/rows/preview")
async def csv_row_transformation_preview_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": csv_row_transformation_preview(
                **validate_row_preview_request(request_payload)
            ),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(request, "csv_row_transformation_preview", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/csv/mapping/export/preview")
async def csv_canonical_export_preview_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": csv_canonical_export_preview(
                **validate_export_preview_request(request_payload)
            ),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(
        request,
        "csv_canonical_export_preview",
        response_payload,
        status_code,
    )
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/forecast/demand")
async def forecast_demand_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": forecast_response_from_request(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(request, "forecast_demand", response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/forecast/demand/preview")
async def forecast_demand_preview_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": forecast_to_demand_preview(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    _log_solve_route(
        request,
        "forecast_demand_preview",
        response_payload,
        status_code,
    )
    return JSONResponse(content=response_payload, status_code=status_code)


async def _recommendations_endpoint(
    request: Request,
    route_name: str,
) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        response_payload = {
            "ok": True,
            "result": recommendation_response_from_request(request_payload),
        }
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    if _error_type(response_payload) == "ScenarioEvaluationError":
        status_code = 500
    _log_solve_route(request, route_name, response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/solve-csv", response_model=None)
async def solve_csv_endpoint(
    request: Request,
    employees_csv: UploadFile = File(...),
    shifts_csv: UploadFile = File(...),
    demand_csv: UploadFile = File(...),
    min_rest_hours: int = Form(DEFAULT_MIN_REST_HOURS),
    max_consecutive_days: int = Form(DEFAULT_MAX_CONSECUTIVE_DAYS),
    shortage_penalty: int = Form(DEFAULT_SHORTAGE_PENALTY),
    time_limit_sec: float = Form(10.0),
    seed: int = Form(1),
    use_warm_start: bool = Form(False),
):
    try:
        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            employees_path = temp_path / "employees.csv"
            shifts_path = temp_path / "shifts.csv"
            demand_path = temp_path / "demand.csv"
            output_path = temp_path / "roster.csv"

            await _write_upload_file(
                employees_csv,
                employees_path,
                "employees_csv",
            )
            await _write_upload_file(shifts_csv, shifts_path, "shifts_csv")
            await _write_upload_file(demand_csv, demand_path, "demand_csv")

            request_payload = payload_from_csv_files(
                employees_path,
                shifts_path,
                demand_path,
                min_rest_hours=min_rest_hours,
                max_consecutive_days=max_consecutive_days,
                shortage_penalty=shortage_penalty,
                time_limit_sec=time_limit_sec,
                seed=seed,
                use_warm_start=use_warm_start,
            )
            data = parse_solve_request(request_payload).problem
            response_payload = solve_payload(request_payload)
            if not response_payload["ok"]:
                response_payload = _with_request_id(response_payload, request)
                _log_solve_route(request, "solve_csv", response_payload, 400)
                return JSONResponse(content=response_payload, status_code=400)

            write_solve_response_csv(
                response_payload,
                output_path,
                employee_names={
                    employee.employee_id: employee.name
                    for employee in data.employees
                },
                shift_names={
                    shift: shift_name
                    for shift, shift_name in enumerate(data.shifts)
                },
            )
            response = Response(
                content=output_path.read_text(),
                media_type="text/csv",
                headers={
                    "Content-Disposition": 'attachment; filename="roster.csv"'
                },
            )
            _log_solve_route(request, "solve_csv", {"ok": True}, 200)
            return response
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)
        status_code = (
            413
            if _error_type(response_payload) == "CsvUploadTooLargeError"
            else 400
        )
        _log_solve_route(request, "solve_csv", response_payload, status_code)
        return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/solve-jobs")
async def create_solve_job(
    request: Request,
) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)
        status_code = (
            413
            if _error_type(response_payload) == "RequestTooLargeError"
            else 400
        )
        _log_solve_route(request, "solve_jobs", response_payload, status_code)
        return JSONResponse(content=response_payload, status_code=status_code)

    try:
        job = solve_job_store.create()
    except (JobCapacityError, JobStoreFullError) as exc:
        response_payload = _error_payload_for_request(exc, request)
        _log_solve_route(request, "solve_jobs", response_payload, 429)
        return JSONResponse(content=response_payload, status_code=429)

    submit_solve_job(solve_job_store, job.job_id, request_payload)
    response_payload = {
        "ok": True,
        "job": job_payload(job),
        "status_url": f"/solve-jobs/{job.job_id}",
    }
    _log_solve_route(request, "solve_jobs", response_payload, 202)
    return JSONResponse(content=response_payload, status_code=202)


@app.get("/solve-jobs/{job_id}")
async def get_solve_job(job_id: str, request: Request) -> JSONResponse:
    try:
        job = solve_job_store.get(job_id)
    except JobNotFoundError as exc:
        response_payload = _error_payload_for_request(exc, request)
        _log_solve_route(request, "solve_job_status", response_payload, 404)
        return JSONResponse(content=response_payload, status_code=404)

    response_payload = {"ok": True, "job": job_payload(job)}
    _log_solve_route(request, "solve_job_status", response_payload, 200)
    return JSONResponse(content=response_payload)


async def _write_upload_file(upload: UploadFile, path: Path, label: str) -> None:
    bytes_written = 0
    with open(path, "wb") as handle:
        while True:
            chunk = await upload.read(64 * 1024)
            if not chunk:
                return
            bytes_written += len(chunk)
            if bytes_written > MAX_CSV_UPLOAD_BYTES:
                raise CsvUploadTooLargeError(
                    f"{label} exceeds {MAX_CSV_UPLOAD_BYTES} bytes"
                )
            handle.write(chunk)


async def _json_request_payload(request: Request) -> Any:
    body = await request.body()
    if len(body) > MAX_JSON_REQUEST_BYTES:
        raise RequestTooLargeError(
            f"JSON request body exceeds {MAX_JSON_REQUEST_BYTES} bytes"
        )
    return json.loads(body)


def _csv_mapping_report_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CsvMappingValidationError("CSV mapping request must be an object")
    if "headers" in payload or "csv_type" in payload:
        return _single_csv_mapping_report_from_payload(payload)
    return csv_mapping_report(
        employee_headers=_optional_header_list(payload, "employee_headers"),
        demand_headers=_optional_header_list(payload, "demand_headers"),
        shift_headers=_optional_header_list(payload, "shift_headers"),
    )


def _single_csv_mapping_report_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    csv_type = payload.get("csv_type")
    if not isinstance(csv_type, str) or not csv_type.strip():
        raise CsvMappingValidationError("CSV mapping request csv_type must be a string")
    csv_type = csv_type.strip()
    if "headers" not in payload:
        raise CsvMappingValidationError("CSV mapping request must include headers")
    headers = payload["headers"]
    if csv_type == CSV_TYPE_EMPLOYEES:
        return csv_mapping_report(employee_headers=headers)
    if csv_type == CSV_TYPE_DEMAND:
        return csv_mapping_report(demand_headers=headers)
    if csv_type == CSV_TYPE_SHIFTS:
        return csv_mapping_report(shift_headers=headers)
    raise CsvMappingValidationError(f"Unsupported CSV mapping csv_type {csv_type}")


def _csv_mapping_preview_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CsvMappingValidationError("CSV mapping preview request must be an object")
    csv_type = payload.get("csv_type")
    if not isinstance(csv_type, str) or not csv_type.strip():
        raise CsvMappingValidationError(
            "CSV mapping preview request csv_type must be a string"
        )
    if "headers" not in payload:
        raise CsvMappingValidationError(
            "CSV mapping preview request must include headers"
        )
    csv_type = csv_type.strip()
    return csv_mapping_preview(
        csv_type=csv_type,
        headers=payload["headers"],
        mapping=payload.get("mapping"),
        mapping_report=payload.get("mapping_report"),
    )


def _optional_header_list(payload: dict[str, Any], key: str) -> Any:
    return payload[key] if key in payload else None


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _error_payload_for_request(exc: Exception, request: Request) -> dict[str, Any]:
    return _with_request_id(error_payload(exc), request)


def _with_request_id(
    response_payload: dict[str, Any],
    request: Request,
) -> dict[str, Any]:
    if not response_payload.get("ok", False):
        error = response_payload.setdefault("error", {})
        if isinstance(error, dict):
            error["request_id"] = _request_id(request)
    return response_payload


def _error_type(response_payload: dict[str, Any]) -> str | None:
    error = response_payload.get("error")
    if isinstance(error, dict):
        error_type = error.get("type")
        if isinstance(error_type, str):
            return error_type
    return None


def _log_solve_route(
    request: Request,
    route_name: str,
    response_payload: dict[str, Any],
    status_code: int,
) -> None:
    ok = bool(response_payload.get("ok", False))
    error_type = _error_type(response_payload)
    logger.info(
        "solve_route route=%s method=%s path=%s status_code=%s "
        "request_id=%s ok=%s error_type=%s",
        route_name,
        request.method,
        request.url.path,
        status_code,
        _request_id(request),
        ok,
        error_type,
    )


async def _explanation_endpoint(
    request: Request,
    route_name: str,
    explainer,
    required_target_keys: tuple[str, ...] = (),
) -> JSONResponse:
    try:
        request_payload = await _json_request_payload(request)
        solve_request_payload, target = _split_explanation_request(
            request_payload,
            required_target_keys=required_target_keys,
        )
        response_payload = solve_request_to_explanation_payload(
            solve_request_payload,
            explainer,
            target=target,
        )
    except Exception as exc:
        response_payload = _error_payload_for_request(exc, request)

    response_payload = _with_request_id(response_payload, request)
    status_code = 200 if response_payload["ok"] else 400
    if _error_type(response_payload) == "RequestTooLargeError":
        status_code = 413
    if _error_type(response_payload) == "ExplanationTargetNotFoundError":
        status_code = 404
    _log_solve_route(request, route_name, response_payload, status_code)
    return JSONResponse(content=response_payload, status_code=status_code)


def _split_explanation_request(
    payload: Any,
    *,
    required_target_keys: tuple[str, ...],
) -> tuple[Any, dict[str, Any]]:
    if not isinstance(payload, dict):
        return payload, {}
    if "solve_request" not in payload:
        return payload, _target_from_payload(payload, required_target_keys)

    solve_request_payload = payload["solve_request"]
    target = _target_from_payload(payload.get("target", {}), required_target_keys)
    return solve_request_payload, target


def _target_from_payload(
    payload: Any,
    required_target_keys: tuple[str, ...],
) -> dict[str, Any]:
    if not required_target_keys:
        return {}
    if not isinstance(payload, dict):
        raise ExplanationQueryError("Explanation target must be an object")
    missing = [
        key
        for key in required_target_keys
        if key not in payload
    ]
    if missing:
        raise ExplanationQueryError(
            f"Missing explanation target field(s): {', '.join(missing)}"
        )
    target = {
        key: payload[key]
        for key in payload
        if key in {*required_target_keys, "role"}
    }
    for key in ("employee_id", "day", "shift"):
        if key in target:
            if isinstance(target[key], bool):
                raise ExplanationQueryError(
                    f"Explanation target field {key} must be an integer"
                )
            try:
                target[key] = int(target[key])
            except (TypeError, ValueError) as exc:
                raise ExplanationQueryError(
                    f"Explanation target field {key} must be an integer"
                ) from exc
    if "role" in required_target_keys and target.get("role") is None:
        raise ExplanationQueryError(
            "Explanation target field role must be a non-empty string"
        )
    if "role" in target and target["role"] is not None:
        if not isinstance(target["role"], str) or not target["role"].strip():
            raise ExplanationQueryError(
                "Explanation target field role must be a non-empty string"
            )
        target["role"] = target["role"].strip()
    return target


def _narration_source_from_payload(
    payload: Any,
) -> tuple[Any, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        raise ExplanationNarrationError("Narration request must be an object")
    if "solve_request" in payload:
        return _narration_explanation_from_solve_request(payload)
    if "explanation" in payload:
        return payload["explanation"], None
    if payload.get("ok") is True and isinstance(payload.get("result"), dict):
        return payload["result"], None
    required_explanation_keys = {
        "type",
        "status",
        "title",
        "message",
        "evidence_contract_version",
        "reason_codes",
        "details",
        "recommended_next_checks",
    }
    if required_explanation_keys <= set(payload):
        return payload, None
    raise ExplanationNarrationError(
        "Narration request must contain an explanation object"
    )


def _narration_explanation_from_solve_request(
    payload: dict[str, Any],
) -> tuple[Any, dict[str, Any]]:
    kind = payload.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        raise ExplanationNarrationError(
            "Narration request with solve_request must include a non-empty kind"
        )
    kind = kind.strip()
    explanation_specs = {
        "summary": (explain_summary, ()),
        "shortages": (explain_shortages, ()),
        "assignment": (
            explain_assignment,
            ("employee_id", "day", "shift", "role"),
        ),
        "employee": (explain_employee, ("employee_id",)),
        "shift": (explain_shift, ("day", "shift")),
    }
    if kind not in explanation_specs:
        raise ExplanationNarrationError(
            "Narration kind must be one of assignment, employee, shift, shortages, summary"
        )

    explainer, required_target_keys = explanation_specs[kind]
    target = _target_from_payload(payload.get("target", {}), required_target_keys)
    explanation_response = solve_request_to_explanation_payload(
        payload["solve_request"],
        explainer,
        target=target,
    )
    if not explanation_response.get("ok", False):
        _raise_from_explanation_error_payload(explanation_response)
    return explanation_response["result"], {
        "mode": "solve_request",
        "kind": kind,
        "target": target,
    }


def _raise_from_explanation_error_payload(payload: dict[str, Any]) -> None:
    error = payload.get("error")
    if not isinstance(error, dict):
        raise ExplanationNarrationError("Could not build deterministic explanation")
    error_type = error.get("type")
    message = str(error.get("message", ""))
    if error_type == "SchemaValidationError":
        raise SchemaValidationError(message)
    if error_type == "ExplanationQueryError":
        raise ExplanationQueryError(message)
    if error_type == "ExplanationTargetNotFoundError":
        raise ExplanationTargetNotFoundError(message)
    if error_type == "ExplanationNarrationError":
        raise ExplanationNarrationError(message)
    raise ExplanationNarrationError(
        f"Could not build deterministic explanation: {message}"
    )


def _narration_provider_name_from_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return payload.get("provider")
    return None


def _frontend_response(filename: str, media_type: str) -> Response:
    path = FRONTEND_DIR / filename
    return Response(content=path.read_text(), media_type=media_type)


def _example_csv_response(filename: str) -> Response:
    path = EXAMPLES_CSV_DIR / filename
    return Response(content=path.read_text(), media_type="text/csv")
