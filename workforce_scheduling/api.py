from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .schemas import error_payload, solve_payload


app = FastAPI(title="Workforce Scheduling Solver")


@app.get("/health")
async def health() -> dict[str, bool | str]:
    return {"ok": True, "service": "workforce_scheduling_solver"}


@app.post("/solve")
async def solve_endpoint(request: Request) -> JSONResponse:
    try:
        request_payload: Any = await request.json()
        response_payload = solve_payload(request_payload)
    except Exception as exc:
        response_payload = error_payload(exc)

    status_code = 200 if response_payload["ok"] else 400
    return JSONResponse(content=response_payload, status_code=status_code)
