from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .schemas import (
    MAX_TIME_LIMIT_SEC,
    SCHEMA_VERSION,
    SolveOptions,
    error_payload,
    solve_payload,
)


app = FastAPI(title="Workforce Scheduling Solver")


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
        },
        "response_envelope": {
            "success": {"ok": True, "result": "SolveResult payload"},
            "error": {"ok": False, "error": {"type": "string", "message": "string"}},
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
