from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .jobs import (
    InMemorySolveJobStore,
    JobNotFoundError,
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
    solve_payload,
)


app = FastAPI(title="Workforce Scheduling Solver")
solve_job_store = InMemorySolveJobStore()


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
            "solve_jobs": "POST /solve-jobs",
            "solve_job_status": "GET /solve-jobs/{job_id}",
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
            "error": {"ok": False, "error": {"type": "string", "message": "string"}},
        },
        "job_execution": {
            "backend": "in_memory_thread_pool",
            "max_workers": SOLVE_JOB_MAX_WORKERS,
        },
    }


@app.post("/solve")
async def solve_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload: Any = await request.json()
        response_payload = solve_payload(request_payload)
    except Exception as exc:
        response_payload = error_payload(exc)

    status_code = 200 if response_payload["ok"] else 400
    return JSONResponse(content=response_payload, status_code=status_code)


@app.post("/solve-jobs")
async def create_solve_job(
    request: Request,
) -> JSONResponse:
    try:
        request_payload: Any = await request.json()
    except Exception as exc:
        response_payload = error_payload(exc)
        return JSONResponse(content=response_payload, status_code=400)

    job = solve_job_store.create()
    submit_solve_job(solve_job_store, job.job_id, request_payload)
    return JSONResponse(
        content={
            "ok": True,
            "job": job_payload(job),
            "status_url": f"/solve-jobs/{job.job_id}",
        },
        status_code=202,
    )


@app.get("/solve-jobs/{job_id}")
async def get_solve_job(job_id: str) -> JSONResponse:
    try:
        job = solve_job_store.get(job_id)
    except JobNotFoundError as exc:
        return JSONResponse(content=error_payload(exc), status_code=404)

    return JSONResponse(content={"ok": True, "job": job_payload(job)})
