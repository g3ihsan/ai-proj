from __future__ import annotations

import json
import re

import pytest

from workforce_scheduling.forecasting import (
    FORECAST_CONTRACT_VERSION,
    ForecastValidationError,
    forecast_response_from_request,
)


def _forecast_request() -> dict:
    return {
        "historical_demand": [
            {"period": 0, "day": 0, "shift": 0, "role": "worker", "required": 2},
            {"period": 1, "day": 0, "shift": 0, "role": "worker", "required": 4},
            {"period": 0, "day": 0, "shift": 1, "role": "worker", "required": 1},
            {"period": 1, "day": 0, "shift": 1, "role": "worker", "required": 3},
        ],
        "horizon": {
            "days": [0],
            "shifts": [0, 1, 2],
            "roles": ["worker"],
        },
    }


def test_forecast_response_uses_deterministic_historical_average() -> None:
    response = forecast_response_from_request(_forecast_request())

    assert response["type"] == "demand_forecast"
    assert response["forecast_contract_version"] == FORECAST_CONTRACT_VERSION
    assert response["method"] == "historical_average"
    assert response["source"] == "deterministic_historical_demand_baseline"
    assert response["uses_external_ml"] is False
    assert response["uses_external_llm"] is False
    assert response["will_solve"] is False
    assert response["will_mutate_solver_request"] is False
    assert response["will_write_files"] is False
    assert response["historical_record_count"] == 4
    assert response["historical_period_count"] == 2
    assert response["horizon"] == {
        "days": [0],
        "shifts": [0, 1, 2],
        "roles": ["worker"],
    }
    assert response["forecast"] == [
        {
            "day": 0,
            "shift": 0,
            "role": "worker",
            "required": 3,
            "mean_required": 3.0,
            "observation_count": 2,
            "historical_values": [2, 4],
        },
        {
            "day": 0,
            "shift": 1,
            "role": "worker",
            "required": 2,
            "mean_required": 2.0,
            "observation_count": 2,
            "historical_values": [1, 3],
        },
        {
            "day": 0,
            "shift": 2,
            "role": "worker",
            "required": 0,
            "mean_required": 0.0,
            "observation_count": 0,
            "historical_values": [],
        },
    ]
    assert response["diagnostics"]["baseline_window_periods"] == [0, 1]
    assert response["diagnostics"]["missing_history_slot_count"] == 1
    assert response["diagnostics"]["missing_history_slots"] == [
        {
            "day": 0,
            "shift": 2,
            "role": "worker",
            "message": (
                "No historical demand records for this horizon slot; "
                "forecast defaults to 0."
            ),
        }
    ]
    assert response["metrics"] == {
        "forecast_slot_count": 3,
        "total_forecast_required": 5,
        "mean_forecast_required": 1.6667,
        "min_forecast_required": 0,
        "max_forecast_required": 3,
        "total_historical_required": 10,
    }
    json.dumps(response)


def test_forecast_response_derives_horizon_from_history_when_omitted() -> None:
    payload = {"historical_demand": _forecast_request()["historical_demand"]}

    response = forecast_response_from_request(payload)

    assert response["horizon"] == {
        "days": [0],
        "shifts": [0, 1],
        "roles": ["worker"],
    }
    assert [row["required"] for row in response["forecast"]] == [3, 2]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "historical_demand must be a list"),
        ({"historical_demand": []}, "historical_demand must not be empty"),
        (
            {
                "historical_demand": [
                    {
                        "period": True,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    }
                ]
            },
            "historical_demand[0].period must be an integer",
        ),
        (
            {
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "",
                        "required": 1,
                    }
                ]
            },
            "historical_demand[0].role must be a non-empty string",
        ),
        (
            {
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": -1,
                    }
                ]
            },
            "historical_demand[0].required must be non-negative",
        ),
        (
            {
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    },
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 2,
                    },
                ]
            },
            "Duplicate historical demand record",
        ),
        (
            {
                "method": "external_model",
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    }
                ],
            },
            "Unsupported forecast method external_model",
        ),
    ],
)
def test_forecast_request_validation_errors(payload: dict, message: str) -> None:
    with pytest.raises(ForecastValidationError, match=re.escape(message)):
        forecast_response_from_request(payload)
