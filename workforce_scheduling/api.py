from __future__ import annotations

import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from .csv_adapter import (
    DEFAULT_MAX_CONSECUTIVE_DAYS,
    DEFAULT_MIN_REST_HOURS,
    DEFAULT_SHORTAGE_PENALTY,
    payload_from_csv_files,
    write_solve_response_csv,
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
from .schemas import (
    MAX_TIME_LIMIT_SEC,
    RESPONSE_MODES,
    SCHEMA_VERSION,
    SolveOptions,
    error_payload,
    parse_solve_request,
    solve_payload,
)


REQUEST_ID_HEADER = "X-Request-ID"
MAX_JSON_REQUEST_BYTES = 1_000_000
MAX_CSV_UPLOAD_BYTES = 1_000_000
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
            "solve_csv": "POST /solve-csv",
            "solve_jobs": "POST /solve-jobs",
            "solve_job_status": "GET /solve-jobs/{job_id}",
        },
        "csv_upload": {
            "file_fields": ["employees_csv", "shifts_csv", "demand_csv"],
            "response_media_type": "text/csv",
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
