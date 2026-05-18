from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping


FORECAST_CONTRACT_VERSION = 1
FORECAST_TYPE_DEMAND = "demand_forecast"
FORECAST_METHOD_HISTORICAL_AVERAGE = "historical_average"
SUPPORTED_FORECAST_METHODS = (FORECAST_METHOD_HISTORICAL_AVERAGE,)


class ForecastingError(ValueError):
    pass


class ForecastValidationError(ForecastingError):
    pass


def forecast_response_from_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    request = _require_mapping(payload, "Forecast request")
    method = _forecast_method(request)
    historical_records = _historical_demand_records(request)
    horizon = _forecast_horizon(request, historical_records)
    return baseline_demand_forecast(
        historical_records=historical_records,
        horizon=horizon,
        method=method,
    )


def baseline_demand_forecast(
    *,
    historical_records: list[dict[str, Any]],
    horizon: dict[str, list[int] | list[str]],
    method: str = FORECAST_METHOD_HISTORICAL_AVERAGE,
) -> dict[str, Any]:
    if method not in SUPPORTED_FORECAST_METHODS:
        raise ForecastValidationError(f"Unsupported forecast method {method}")

    observed_periods = sorted({int(record["period"]) for record in historical_records})
    grouped_required: dict[tuple[int, int, str], list[int]] = defaultdict(list)
    for record in historical_records:
        grouped_required[
            (int(record["day"]), int(record["shift"]), str(record["role"]))
        ].append(int(record["required"]))

    forecast_rows: list[dict[str, Any]] = []
    missing_history_slots: list[dict[str, Any]] = []
    for day in horizon["days"]:
        for shift in horizon["shifts"]:
            for role in horizon["roles"]:
                key = (int(day), int(shift), str(role))
                values = grouped_required.get(key, [])
                mean_required = (
                    sum(values) / len(values)
                    if values
                    else 0.0
                )
                forecast_required = _round_half_up(mean_required)
                if not values:
                    missing_history_slots.append(
                        {
                            "day": key[0],
                            "shift": key[1],
                            "role": key[2],
                            "message": (
                                "No historical demand records for this horizon slot; "
                                "forecast defaults to 0."
                            ),
                        }
                    )
                forecast_rows.append(
                    {
                        "day": key[0],
                        "shift": key[1],
                        "role": key[2],
                        "required": forecast_required,
                        "mean_required": round(mean_required, 4),
                        "observation_count": len(values),
                        "historical_values": list(values),
                    }
                )

    total_forecast_required = sum(row["required"] for row in forecast_rows)
    forecast_slot_count = len(forecast_rows)
    return {
        "type": FORECAST_TYPE_DEMAND,
        "forecast_contract_version": FORECAST_CONTRACT_VERSION,
        "method": method,
        "source": "deterministic_historical_demand_baseline",
        "uses_external_ml": False,
        "uses_external_llm": False,
        "will_solve": False,
        "will_mutate_solver_request": False,
        "will_write_files": False,
        "historical_record_count": len(historical_records),
        "historical_period_count": len(observed_periods),
        "horizon": {
            "days": list(horizon["days"]),
            "shifts": list(horizon["shifts"]),
            "roles": list(horizon["roles"]),
        },
        "forecast": forecast_rows,
        "diagnostics": {
            "baseline_window_periods": observed_periods,
            "missing_history_slots": missing_history_slots,
            "missing_history_slot_count": len(missing_history_slots),
            "notes": [
                (
                    "Deterministic historical average baseline; this forecast "
                    "does not change solver demand or assignments."
                )
            ],
        },
        "metrics": {
            "forecast_slot_count": forecast_slot_count,
            "total_forecast_required": total_forecast_required,
            "mean_forecast_required": (
                round(total_forecast_required / forecast_slot_count, 4)
                if forecast_slot_count
                else 0.0
            ),
            "min_forecast_required": (
                min((row["required"] for row in forecast_rows), default=0)
            ),
            "max_forecast_required": (
                max((row["required"] for row in forecast_rows), default=0)
            ),
            "total_historical_required": sum(
                int(record["required"]) for record in historical_records
            ),
        },
    }


def _forecast_method(payload: Mapping[str, Any]) -> str:
    method = payload.get("method", FORECAST_METHOD_HISTORICAL_AVERAGE)
    if not isinstance(method, str) or not method.strip():
        raise ForecastValidationError("Forecast method must be a non-empty string")
    method = method.strip()
    if method not in SUPPORTED_FORECAST_METHODS:
        raise ForecastValidationError(f"Unsupported forecast method {method}")
    return method


def _historical_demand_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_records = payload.get("historical_demand")
    if not isinstance(raw_records, list):
        raise ForecastValidationError(
            "Forecast request historical_demand must be a list"
        )
    if not raw_records:
        raise ForecastValidationError(
            "Forecast request historical_demand must not be empty"
        )

    records: list[dict[str, Any]] = []
    seen_keys: set[tuple[int, int, int, str]] = set()
    for index, raw_record in enumerate(raw_records):
        record = _require_mapping(raw_record, f"historical_demand[{index}]")
        parsed = {
            "period": _required_non_negative_int(record, "period", index),
            "day": _required_non_negative_int(record, "day", index),
            "shift": _required_non_negative_int(record, "shift", index),
            "role": _required_non_empty_string(record, "role", index),
            "required": _required_non_negative_int(record, "required", index),
        }
        key = (
            parsed["period"],
            parsed["day"],
            parsed["shift"],
            parsed["role"],
        )
        if key in seen_keys:
            raise ForecastValidationError(
                "Duplicate historical demand record "
                f"(period={key[0]}, day={key[1]}, shift={key[2]}, role={key[3]})"
            )
        seen_keys.add(key)
        records.append(parsed)

    return records


def _forecast_horizon(
    payload: Mapping[str, Any],
    historical_records: list[dict[str, Any]],
) -> dict[str, list[int] | list[str]]:
    raw_horizon = payload.get("horizon")
    if raw_horizon is None:
        return {
            "days": sorted({int(record["day"]) for record in historical_records}),
            "shifts": sorted({int(record["shift"]) for record in historical_records}),
            "roles": sorted({str(record["role"]) for record in historical_records}),
        }

    horizon = _require_mapping(raw_horizon, "Forecast horizon")
    return {
        "days": _horizon_int_values(horizon, "days"),
        "shifts": _horizon_int_values(horizon, "shifts"),
        "roles": _horizon_role_values(horizon),
    }


def _horizon_int_values(payload: Mapping[str, Any], key: str) -> list[int]:
    values = payload.get(key)
    if not isinstance(values, list) or not values:
        raise ForecastValidationError(f"Forecast horizon {key} must be a non-empty list")
    parsed = [_non_negative_int(value, f"horizon.{key}") for value in values]
    return sorted(set(parsed))


def _horizon_role_values(payload: Mapping[str, Any]) -> list[str]:
    values = payload.get("roles")
    if not isinstance(values, list) or not values:
        raise ForecastValidationError("Forecast horizon roles must be a non-empty list")
    parsed: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ForecastValidationError(
                "Forecast horizon roles must contain non-empty strings"
            )
        parsed.append(value.strip())
    return sorted(set(parsed))


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ForecastValidationError(f"{label} must be an object")
    return value


def _required_non_negative_int(
    record: Mapping[str, Any],
    key: str,
    index: int,
) -> int:
    if key not in record:
        raise ForecastValidationError(
            f"Missing historical_demand[{index}].{key}"
        )
    return _non_negative_int(record[key], f"historical_demand[{index}].{key}")


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ForecastValidationError(f"{label} must be an integer")
    if value < 0:
        raise ForecastValidationError(f"{label} must be non-negative")
    return value


def _required_non_empty_string(
    record: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    if key not in record:
        raise ForecastValidationError(
            f"Missing historical_demand[{index}].{key}"
        )
    value = record[key]
    if not isinstance(value, str) or not value.strip():
        raise ForecastValidationError(
            f"historical_demand[{index}].{key} must be a non-empty string"
        )
    return value.strip()


def _round_half_up(value: float) -> int:
    return int(value + 0.5)
