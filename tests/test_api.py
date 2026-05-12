from __future__ import annotations

import asyncio

import httpx

from workforce_scheduling.api import app


def _api_request(method: str, path: str) -> httpx.Response:
    async def _request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.request(method, path)

    return asyncio.run(_request())


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
    }
