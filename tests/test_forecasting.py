from __future__ import annotations

import json
import re

import pytest

from workforce_scheduling.forecasting import (
    FORECAST_CONTRACT_VERSION,
    MAX_FORECAST_SLOTS,
    MAX_HISTORICAL_DEMAND_RECORDS,
    ForecastValidationError,
    demand_rows_from_forecast,
    forecast_response_from_request,
    forecast_to_demand_preview,
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
    assert response["limits"] == {
        "max_historical_demand_records": MAX_HISTORICAL_DEMAND_RECORDS,
        "max_forecast_slots": MAX_FORECAST_SLOTS,
        "historical_record_limit_reached": False,
        "forecast_slot_limit_reached": False,
    }
    assert response["fallback_policy"] == {
        "missing_exact_history": "default_required_to_0",
        "uses_shift_role_fallback": False,
        "uses_role_fallback": False,
        "uses_global_fallback": False,
    }
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
            "confidence": "medium",
            "basis": {
                "method": "historical_average",
                "match_level": "exact_day_shift_role",
                "observation_count": 2,
                "mean_required": 3.0,
                "fallback_used": False,
            },
        },
        {
            "day": 0,
            "shift": 1,
            "role": "worker",
            "required": 2,
            "mean_required": 2.0,
            "observation_count": 2,
            "historical_values": [1, 3],
            "confidence": "medium",
            "basis": {
                "method": "historical_average",
                "match_level": "exact_day_shift_role",
                "observation_count": 2,
                "mean_required": 2.0,
                "fallback_used": False,
            },
        },
        {
            "day": 0,
            "shift": 2,
            "role": "worker",
            "required": 0,
            "mean_required": 0.0,
            "observation_count": 0,
            "historical_values": [],
            "confidence": "low",
            "basis": {
                "method": "historical_average",
                "match_level": "none",
                "observation_count": 0,
                "mean_required": 0.0,
                "fallback_used": True,
                "fallback_reason": "no_exact_history",
            },
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


def test_forecast_response_is_deterministic_and_json_serializable() -> None:
    response = forecast_response_from_request(_forecast_request())
    second_response = forecast_response_from_request(_forecast_request())

    assert response == second_response
    json.dumps(response)


def test_forecast_confidence_levels_are_deterministic() -> None:
    payload = {
        "historical_demand": [
            {
                "period": period,
                "day": 0,
                "shift": 0,
                "role": "worker",
                "required": 4,
            }
            for period in range(4)
        ]
        + [
            {
                "period": period,
                "day": 0,
                "shift": 1,
                "role": "worker",
                "required": 2,
            }
            for period in range(2)
        ]
        + [
            {
                "period": 0,
                "day": 0,
                "shift": 2,
                "role": "worker",
                "required": 1,
            }
        ],
        "horizon": {
            "days": [0],
            "shifts": [0, 1, 2, 3],
            "roles": ["worker"],
        },
    }

    response = forecast_response_from_request(payload)

    rows_by_shift = {
        row["shift"]: row
        for row in response["forecast"]
    }
    assert rows_by_shift[0]["confidence"] == "high"
    assert rows_by_shift[0]["basis"] == {
        "method": "historical_average",
        "match_level": "exact_day_shift_role",
        "observation_count": 4,
        "mean_required": 4.0,
        "fallback_used": False,
    }
    assert rows_by_shift[1]["confidence"] == "medium"
    assert rows_by_shift[1]["basis"]["observation_count"] == 2
    assert rows_by_shift[1]["basis"]["fallback_used"] is False
    assert rows_by_shift[2]["confidence"] == "low"
    assert rows_by_shift[2]["basis"]["observation_count"] == 1
    assert rows_by_shift[2]["basis"]["fallback_used"] is False
    assert rows_by_shift[3]["confidence"] == "low"
    assert rows_by_shift[3]["basis"] == {
        "method": "historical_average",
        "match_level": "none",
        "observation_count": 0,
        "mean_required": 0.0,
        "fallback_used": True,
        "fallback_reason": "no_exact_history",
    }


def test_forecast_to_demand_preview_accepts_full_forecast_response() -> None:
    forecast_response = forecast_response_from_request(_forecast_request())

    preview = forecast_to_demand_preview({"forecast": forecast_response})

    assert preview == {
        "type": "forecast_to_demand_preview",
        "forecast_contract_version": FORECAST_CONTRACT_VERSION,
        "source": "deterministic_forecast_to_demand_preview",
        "input_shape": "forecast_response",
        "uses_external_ml": False,
        "uses_external_llm": False,
        "will_solve": False,
        "will_mutate_solver_request": False,
        "will_write_files": False,
        "row_count": 3,
        "total_required": 5,
        "summary": {
            "demand_row_count": 3,
            "total_required": 5,
            "low_confidence_row_count": 1,
            "fallback_row_count": 1,
            "zero_required_row_count": 1,
            "warning_count": 3,
        },
        "warnings": [
            {
                "source_forecast_index": 2,
                "code": "low_confidence_forecast",
                "message": (
                    "Forecast row has low confidence and should be reviewed "
                    "before converting to solver demand."
                ),
            },
            {
                "source_forecast_index": 2,
                "code": "fallback_used",
                "message": (
                    "Forecast row used fallback demand because no exact "
                    "historical day/shift/role match was available."
                ),
            },
            {
                "source_forecast_index": 2,
                "code": "zero_required_demand",
                "message": (
                    "Forecast row converts to required=0 and may need "
                    "manager review before replacing demand."
                ),
            },
        ],
        "demand_rows": [
            {"day": 0, "shift": 0, "role": "worker", "required": 3},
            {"day": 0, "shift": 1, "role": "worker", "required": 2},
            {"day": 0, "shift": 2, "role": "worker", "required": 0},
        ],
        "row_evidence": [
            {
                "source_forecast_index": 0,
                "day": 0,
                "shift": 0,
                "role": "worker",
                "required": 3,
                "confidence": "medium",
                "basis": {
                    "method": "historical_average",
                    "match_level": "exact_day_shift_role",
                    "observation_count": 2,
                    "mean_required": 3.0,
                    "fallback_used": False,
                },
            },
            {
                "source_forecast_index": 1,
                "day": 0,
                "shift": 1,
                "role": "worker",
                "required": 2,
                "confidence": "medium",
                "basis": {
                    "method": "historical_average",
                    "match_level": "exact_day_shift_role",
                    "observation_count": 2,
                    "mean_required": 2.0,
                    "fallback_used": False,
                },
            },
            {
                "source_forecast_index": 2,
                "day": 0,
                "shift": 2,
                "role": "worker",
                "required": 0,
                "confidence": "low",
                "basis": {
                    "method": "historical_average",
                    "match_level": "none",
                    "observation_count": 0,
                    "mean_required": 0.0,
                    "fallback_used": True,
                    "fallback_reason": "no_exact_history",
                },
            },
        ],
        "traceability": {
            "source_forecast_row_count": 3,
            "source_fields_used": [
                "day",
                "shift",
                "role",
                "required",
                "confidence",
                "basis",
            ],
            "preserves_solver_contract": True,
            "row_semantics_validated": False,
        },
    }
    json.dumps(preview)


def test_forecast_to_demand_preview_accepts_direct_forecast_rows() -> None:
    forecast_rows = forecast_response_from_request(_forecast_request())["forecast"]

    preview = forecast_to_demand_preview({"forecast_rows": forecast_rows})

    assert preview["input_shape"] == "forecast_rows"
    assert preview["summary"] == {
        "demand_row_count": 3,
        "total_required": 5,
        "low_confidence_row_count": 1,
        "fallback_row_count": 1,
        "zero_required_row_count": 1,
        "warning_count": 3,
    }
    assert preview["demand_rows"] == [
        {"day": 0, "shift": 0, "role": "worker", "required": 3},
        {"day": 0, "shift": 1, "role": "worker", "required": 2},
        {"day": 0, "shift": 2, "role": "worker", "required": 0},
    ]
    assert preview["row_evidence"][0] == {
        "source_forecast_index": 0,
        "day": 0,
        "shift": 0,
        "role": "worker",
        "required": 3,
        "confidence": "medium",
        "basis": {
            "method": "historical_average",
            "match_level": "exact_day_shift_role",
            "observation_count": 2,
            "mean_required": 3.0,
            "fallback_used": False,
        },
    }
    assert [warning["code"] for warning in preview["warnings"]] == [
        "low_confidence_forecast",
        "fallback_used",
        "zero_required_demand",
    ]


def test_forecast_to_demand_preview_accepts_forecast_row_list_alias() -> None:
    forecast_rows = forecast_response_from_request(_forecast_request())["forecast"]

    preview = forecast_to_demand_preview({"forecast": forecast_rows})

    assert preview["input_shape"] == "forecast_rows"
    assert preview["row_count"] == 3


def test_forecast_to_demand_preview_is_deterministic_and_does_not_mutate_input() -> None:
    forecast_response = forecast_response_from_request(_forecast_request())
    request_payload = {"forecast": forecast_response}
    before = json.loads(json.dumps(request_payload))

    preview = forecast_to_demand_preview(request_payload)
    second_preview = forecast_to_demand_preview(request_payload)

    assert preview == second_preview
    assert request_payload == before
    json.dumps(preview)


def test_demand_rows_from_forecast_returns_canonical_solver_demand_rows() -> None:
    forecast_rows = [
        {
            "day": 1,
            "shift": 2,
            "role": " supervisor ",
            "required": 3,
            "confidence": "medium",
            "basis": {
                "method": "historical_average",
                "match_level": "exact_day_shift_role",
                "observation_count": 2,
                "mean_required": 3.0,
                "fallback_used": False,
            },
        }
    ]

    assert demand_rows_from_forecast(forecast_rows) == [
        {"day": 1, "shift": 2, "role": "supervisor", "required": 3}
    ]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "Forecast request must be an object"),
        ({}, "historical_demand must be a list"),
        ({"historical_demand": []}, "historical_demand must not be empty"),
        (
            {
                "method": "",
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
            "Forecast method must be a non-empty string",
        ),
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
                        "day": True,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    }
                ]
            },
            "historical_demand[0].day must be an integer",
        ),
        (
            {
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": True,
                        "role": "worker",
                        "required": 1,
                    }
                ]
            },
            "historical_demand[0].shift must be an integer",
        ),
        (
            {
                "historical_demand": [
                    {
                        "period": 0,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": True,
                    }
                ]
            },
            "historical_demand[0].required must be an integer",
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
                "historical_demand": [
                    {
                        "period": index,
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    }
                    for index in range(MAX_HISTORICAL_DEMAND_RECORDS + 1)
                ]
            },
            "historical_demand contains 1001 record(s); maximum is 1000",
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
                    }
                ],
                "horizon": {
                    "days": list(range(10)),
                    "shifts": list(range(10)),
                    "roles": ["worker", "supervisor"],
                },
            },
            "Forecast horizon produces 200 slot(s); maximum is 100",
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
                    }
                ],
                "horizon": {
                    "days": [True],
                    "shifts": [0],
                    "roles": ["worker"],
                },
            },
            "horizon.days must be an integer",
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
                    }
                ],
                "horizon": {
                    "days": [0],
                    "shifts": [False],
                    "roles": ["worker"],
                },
            },
            "horizon.shifts must be an integer",
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
                    }
                ],
                "horizon": {
                    "days": [0],
                    "shifts": [0],
                    "roles": [""],
                },
            },
            "Forecast horizon roles must contain non-empty strings",
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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "Forecast-to-demand preview request must be an object"),
        ({}, "must include forecast or forecast_rows"),
        (
            {"forecast": {"type": "other", "forecast": []}},
            "forecast.type must be demand_forecast",
        ),
        (
            {"forecast": {"type": "demand_forecast"}},
            "forecast rows must be a list",
        ),
        ({"forecast_rows": []}, "forecast rows must not be empty"),
        (
            {
                "forecast_rows": [
                    {
                        "day": index,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                    }
                    for index in range(MAX_FORECAST_SLOTS + 1)
                ]
            },
            "contains 101 row(s); maximum is 100",
        ),
        (
            {"forecast_rows": [{"shift": 0, "role": "worker", "required": 1}]},
            "Missing forecast[0].day",
        ),
        (
            {
                "forecast_rows": [
                    {"day": True, "shift": 0, "role": "worker", "required": 1}
                ]
            },
            "forecast[0].day must be an integer",
        ),
        (
            {
                "forecast_rows": [
                    {"day": 0, "shift": False, "role": "worker", "required": 1}
                ]
            },
            "forecast[0].shift must be an integer",
        ),
        (
            {
                "forecast_rows": [
                    {"day": 0, "shift": 0, "role": "", "required": 1}
                ]
            },
            "forecast[0].role must be a non-empty string",
        ),
        (
            {
                "forecast_rows": [
                    {"day": 0, "shift": 0, "role": "worker", "required": True}
                ]
            },
            "forecast[0].required must be an integer",
        ),
        (
            {
                "forecast_rows": [
                    {"day": 0, "shift": 0, "role": "worker", "required": -1}
                ]
            },
            "forecast[0].required must be non-negative",
        ),
        (
            {
                "forecast_rows": [
                    {"day": 0, "shift": 0, "role": "worker", "required": 1}
                ]
            },
            "Missing forecast[0].confidence",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                        "confidence": "medium",
                    }
                ]
            },
            "Missing forecast[0].basis",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                        "confidence": "certain",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "exact_day_shift_role",
                            "observation_count": 2,
                            "mean_required": 1.0,
                            "fallback_used": False,
                        },
                    }
                ]
            },
            "forecast[0].confidence must be one of",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                        "confidence": "high",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "exact_day_shift_role",
                            "observation_count": 2,
                            "mean_required": 1.0,
                            "fallback_used": False,
                        },
                    }
                ]
            },
            "confidence must match basis.observation_count (medium)",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 0,
                        "confidence": "low",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "none",
                            "observation_count": 0,
                            "mean_required": 0.0,
                            "fallback_used": True,
                        },
                    }
                ]
            },
            "Missing forecast[0].basis.fallback_reason",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 0,
                        "confidence": "low",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "exact_day_shift_role",
                            "observation_count": 0,
                            "mean_required": 0.0,
                            "fallback_used": True,
                            "fallback_reason": "no_exact_history",
                        },
                    }
                ]
            },
            "basis.match_level must be none when fallback_used is true",
        ),
        (
            {
                "forecast_rows": [
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 1,
                        "confidence": "medium",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "exact_day_shift_role",
                            "observation_count": 2,
                            "mean_required": 1.0,
                            "fallback_used": False,
                        },
                    },
                    {
                        "day": 0,
                        "shift": 0,
                        "role": "worker",
                        "required": 2,
                        "confidence": "medium",
                        "basis": {
                            "method": "historical_average",
                            "match_level": "exact_day_shift_role",
                            "observation_count": 2,
                            "mean_required": 2.0,
                            "fallback_used": False,
                        },
                    },
                ]
            },
            "Duplicate forecast demand slot",
        ),
    ],
)
def test_forecast_to_demand_preview_validation_errors(
    payload: dict,
    message: str,
) -> None:
    with pytest.raises(ForecastValidationError, match=re.escape(message)):
        forecast_to_demand_preview(payload)
