from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping


FORECAST_CONTRACT_VERSION = 1
FORECAST_TYPE_DEMAND = "demand_forecast"
FORECAST_TYPE_DEMAND_PREVIEW = "forecast_to_demand_preview"
FORECAST_METHOD_HISTORICAL_AVERAGE = "historical_average"
SUPPORTED_FORECAST_METHODS = (FORECAST_METHOD_HISTORICAL_AVERAGE,)
MAX_HISTORICAL_DEMAND_RECORDS = 1000
MAX_FORECAST_SLOTS = 100
FORECAST_MATCH_EXACT = "exact_day_shift_role"
FORECAST_MATCH_NONE = "none"
FORECAST_FALLBACK_REASON_NO_EXACT_HISTORY = "no_exact_history"
FORECAST_CONFIDENCE_HIGH = "high"
FORECAST_CONFIDENCE_MEDIUM = "medium"
FORECAST_CONFIDENCE_LOW = "low"
SUPPORTED_FORECAST_CONFIDENCE_LEVELS = (
    FORECAST_CONFIDENCE_HIGH,
    FORECAST_CONFIDENCE_MEDIUM,
    FORECAST_CONFIDENCE_LOW,
)
SUPPORTED_FORECAST_MATCH_LEVELS = (
    FORECAST_MATCH_EXACT,
    FORECAST_MATCH_NONE,
)


class ForecastingError(ValueError):
    pass


class ForecastValidationError(ForecastingError):
    pass


def forecast_to_demand_preview(payload: Mapping[str, Any]) -> dict[str, Any]:
    preview_request = validate_forecast_to_demand_request(payload)
    forecast_rows = preview_request["forecast_rows"]
    row_evidence = row_evidence_from_forecast(forecast_rows)
    demand_rows = _demand_rows_from_evidence(row_evidence)
    warnings = forecast_to_demand_warnings(row_evidence)
    total_required = sum(row["required"] for row in demand_rows)

    return {
        "type": FORECAST_TYPE_DEMAND_PREVIEW,
        "forecast_contract_version": FORECAST_CONTRACT_VERSION,
        "source": "deterministic_forecast_to_demand_preview",
        "input_shape": preview_request["input_shape"],
        "uses_external_ml": False,
        "uses_external_llm": False,
        "will_solve": False,
        "will_mutate_solver_request": False,
        "will_write_files": False,
        "row_count": len(demand_rows),
        "total_required": total_required,
        "summary": {
            "demand_row_count": len(demand_rows),
            "total_required": total_required,
            "low_confidence_row_count": sum(
                1
                for evidence in row_evidence
                if evidence["confidence"] == FORECAST_CONFIDENCE_LOW
            ),
            "fallback_row_count": sum(
                1
                for evidence in row_evidence
                if bool(evidence["basis"]["fallback_used"])
            ),
            "zero_required_row_count": sum(
                1
                for row in demand_rows
                if row["required"] == 0
            ),
            "warning_count": len(warnings),
        },
        "warnings": warnings,
        "demand_rows": demand_rows,
        "row_evidence": row_evidence,
        "traceability": {
            "source_forecast_row_count": len(forecast_rows),
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


def demand_rows_from_forecast(
    forecast_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return _demand_rows_from_evidence(row_evidence_from_forecast(forecast_rows))


def _demand_rows_from_evidence(
    row_evidence: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    demand_rows: list[dict[str, Any]] = []
    seen_slots: set[tuple[int, int, str]] = set()
    for evidence in row_evidence:
        demand_row = {
            "day": evidence["day"],
            "shift": evidence["shift"],
            "role": evidence["role"],
            "required": evidence["required"],
        }
        slot_key = (
            demand_row["day"],
            demand_row["shift"],
            demand_row["role"],
        )
        if slot_key in seen_slots:
            raise ForecastValidationError(
                "Duplicate forecast demand slot "
                f"(day={slot_key[0]}, shift={slot_key[1]}, role={slot_key[2]})"
            )
        seen_slots.add(slot_key)
        demand_rows.append(demand_row)
    return demand_rows


def row_evidence_from_forecast(
    forecast_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence_rows: list[dict[str, Any]] = []
    for index, forecast_row in enumerate(forecast_rows):
        row = _require_mapping(forecast_row, f"forecast[{index}]")
        day = _required_forecast_row_non_negative_int(row, "day", index)
        shift = _required_forecast_row_non_negative_int(row, "shift", index)
        role = _required_forecast_row_non_empty_string(row, "role", index)
        required = _required_forecast_row_non_negative_int(row, "required", index)
        confidence = _required_forecast_row_confidence(row, index)
        basis = _required_forecast_row_basis(row, index)
        _validate_forecast_row_confidence_matches_basis(
            confidence=confidence,
            basis=basis,
            index=index,
        )
        evidence_rows.append(
            {
                "source_forecast_index": index,
                "day": day,
                "shift": shift,
                "role": role,
                "required": required,
                "confidence": confidence,
                "basis": basis,
            }
        )
    return evidence_rows


def forecast_to_demand_warnings(row_evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for evidence in row_evidence:
        source_index = evidence["source_forecast_index"]
        if evidence["confidence"] == FORECAST_CONFIDENCE_LOW:
            warnings.append(
                {
                    "source_forecast_index": source_index,
                    "code": "low_confidence_forecast",
                    "message": (
                        "Forecast row has low confidence and should be reviewed "
                        "before converting to solver demand."
                    ),
                }
            )
        if bool(evidence["basis"]["fallback_used"]):
            warnings.append(
                {
                    "source_forecast_index": source_index,
                    "code": "fallback_used",
                    "message": (
                        "Forecast row used fallback demand because no exact "
                        "historical day/shift/role match was available."
                    ),
                }
            )
        if evidence["required"] == 0:
            warnings.append(
                {
                    "source_forecast_index": source_index,
                    "code": "zero_required_demand",
                    "message": (
                        "Forecast row converts to required=0 and may need "
                        "manager review before replacing demand."
                    ),
                }
            )
    return warnings


def validate_forecast_to_demand_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    request = _require_mapping(payload, "Forecast-to-demand preview request")
    forecast_payload = request.get("forecast")
    if isinstance(forecast_payload, Mapping):
        forecast_type = forecast_payload.get("type")
        if forecast_type != FORECAST_TYPE_DEMAND:
            raise ForecastValidationError(
                "Forecast-to-demand preview forecast.type must be demand_forecast"
            )
        forecast_rows = forecast_payload.get("forecast")
        input_shape = "forecast_response"
    elif isinstance(forecast_payload, list):
        forecast_rows = forecast_payload
        input_shape = "forecast_rows"
    elif "forecast_rows" in request:
        forecast_rows = request.get("forecast_rows")
        input_shape = "forecast_rows"
    else:
        raise ForecastValidationError(
            "Forecast-to-demand preview request must include forecast or forecast_rows"
        )

    if not isinstance(forecast_rows, list):
        raise ForecastValidationError(
            "Forecast-to-demand preview forecast rows must be a list"
        )
    if not forecast_rows:
        raise ForecastValidationError(
            "Forecast-to-demand preview forecast rows must not be empty"
        )
    if len(forecast_rows) > MAX_FORECAST_SLOTS:
        raise ForecastValidationError(
            "Forecast-to-demand preview contains "
            f"{len(forecast_rows)} row(s); maximum is {MAX_FORECAST_SLOTS}"
        )

    return {
        "forecast_rows": list(forecast_rows),
        "input_shape": input_shape,
    }


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
    forecast_slot_count = _forecast_slot_count(horizon)
    if forecast_slot_count > MAX_FORECAST_SLOTS:
        raise ForecastValidationError(
            "Forecast horizon produces "
            f"{forecast_slot_count} slot(s); maximum is {MAX_FORECAST_SLOTS}"
        )
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
                observation_count = len(values)
                confidence = _forecast_confidence(observation_count)
                rounded_mean_required = round(mean_required, 4)
                fallback_used = observation_count == 0
                match_level = (
                    FORECAST_MATCH_NONE
                    if fallback_used
                    else FORECAST_MATCH_EXACT
                )
                basis: dict[str, Any] = {
                    "method": method,
                    "match_level": match_level,
                    "observation_count": observation_count,
                    "mean_required": rounded_mean_required,
                    "fallback_used": fallback_used,
                }
                if not values:
                    basis["fallback_reason"] = (
                        FORECAST_FALLBACK_REASON_NO_EXACT_HISTORY
                    )
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
                        "mean_required": rounded_mean_required,
                        "observation_count": observation_count,
                        "historical_values": list(values),
                        "confidence": confidence,
                        "basis": basis,
                    }
                )

    total_forecast_required = sum(row["required"] for row in forecast_rows)
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
        "limits": {
            "max_historical_demand_records": MAX_HISTORICAL_DEMAND_RECORDS,
            "max_forecast_slots": MAX_FORECAST_SLOTS,
            "historical_record_limit_reached": (
                len(historical_records) >= MAX_HISTORICAL_DEMAND_RECORDS
            ),
            "forecast_slot_limit_reached": forecast_slot_count >= MAX_FORECAST_SLOTS,
        },
        "fallback_policy": {
            "missing_exact_history": "default_required_to_0",
            "uses_shift_role_fallback": False,
            "uses_role_fallback": False,
            "uses_global_fallback": False,
        },
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
    if len(raw_records) > MAX_HISTORICAL_DEMAND_RECORDS:
        raise ForecastValidationError(
            "Forecast request historical_demand contains "
            f"{len(raw_records)} record(s); maximum is "
            f"{MAX_HISTORICAL_DEMAND_RECORDS}"
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


def _forecast_slot_count(horizon: Mapping[str, list[int] | list[str]]) -> int:
    return (
        len(horizon["days"])
        * len(horizon["shifts"])
        * len(horizon["roles"])
    )


def _forecast_confidence(observation_count: int) -> str:
    if observation_count >= 4:
        return FORECAST_CONFIDENCE_HIGH
    if observation_count >= 2:
        return FORECAST_CONFIDENCE_MEDIUM
    return FORECAST_CONFIDENCE_LOW


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


def _required_forecast_row_non_negative_int(
    row: Mapping[str, Any],
    key: str,
    index: int,
) -> int:
    if key not in row:
        raise ForecastValidationError(f"Missing forecast[{index}].{key}")
    return _non_negative_int(row[key], f"forecast[{index}].{key}")


def _required_forecast_row_non_empty_string(
    row: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    if key not in row:
        raise ForecastValidationError(f"Missing forecast[{index}].{key}")
    value = row[key]
    if not isinstance(value, str) or not value.strip():
        raise ForecastValidationError(
            f"forecast[{index}].{key} must be a non-empty string"
        )
    return value.strip()


def _required_forecast_row_confidence(
    row: Mapping[str, Any],
    index: int,
) -> str:
    if "confidence" not in row:
        raise ForecastValidationError(f"Missing forecast[{index}].confidence")
    value = row["confidence"]
    if not isinstance(value, str) or not value.strip():
        raise ForecastValidationError(
            f"forecast[{index}].confidence must be a non-empty string"
        )
    confidence = value.strip()
    if confidence not in SUPPORTED_FORECAST_CONFIDENCE_LEVELS:
        raise ForecastValidationError(
            f"forecast[{index}].confidence must be one of "
            f"{list(SUPPORTED_FORECAST_CONFIDENCE_LEVELS)}"
        )
    return confidence


def _required_forecast_row_basis(
    row: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    if "basis" not in row:
        raise ForecastValidationError(f"Missing forecast[{index}].basis")
    basis = _require_mapping(row["basis"], f"forecast[{index}].basis")
    method = _required_basis_string(basis, "method", index)
    if method != FORECAST_METHOD_HISTORICAL_AVERAGE:
        raise ForecastValidationError(
            f"forecast[{index}].basis.method must be {FORECAST_METHOD_HISTORICAL_AVERAGE}"
        )
    match_level = _required_basis_string(basis, "match_level", index)
    if match_level not in SUPPORTED_FORECAST_MATCH_LEVELS:
        raise ForecastValidationError(
            f"forecast[{index}].basis.match_level must be one of "
            f"{list(SUPPORTED_FORECAST_MATCH_LEVELS)}"
        )
    observation_count = _required_basis_non_negative_int(
        basis,
        "observation_count",
        index,
    )
    mean_required = _required_basis_non_negative_number(
        basis,
        "mean_required",
        index,
    )
    fallback_used = _required_basis_bool(basis, "fallback_used", index)
    parsed: dict[str, Any] = {
        "method": method,
        "match_level": match_level,
        "observation_count": observation_count,
        "mean_required": mean_required,
        "fallback_used": fallback_used,
    }

    if fallback_used:
        fallback_reason = _required_basis_string(basis, "fallback_reason", index)
        if fallback_reason != FORECAST_FALLBACK_REASON_NO_EXACT_HISTORY:
            raise ForecastValidationError(
                f"forecast[{index}].basis.fallback_reason must be "
                f"{FORECAST_FALLBACK_REASON_NO_EXACT_HISTORY}"
            )
        if match_level != FORECAST_MATCH_NONE:
            raise ForecastValidationError(
                f"forecast[{index}].basis.match_level must be none when "
                "fallback_used is true"
            )
        if observation_count != 0:
            raise ForecastValidationError(
                f"forecast[{index}].basis.observation_count must be 0 when "
                "fallback_used is true"
            )
        parsed["fallback_reason"] = fallback_reason
    elif match_level != FORECAST_MATCH_EXACT:
        raise ForecastValidationError(
            f"forecast[{index}].basis.match_level must be exact_day_shift_role "
            "when fallback_used is false"
        )

    return parsed


def _required_basis_string(
    basis: Mapping[str, Any],
    key: str,
    index: int,
) -> str:
    if key not in basis:
        raise ForecastValidationError(f"Missing forecast[{index}].basis.{key}")
    value = basis[key]
    if not isinstance(value, str) or not value.strip():
        raise ForecastValidationError(
            f"forecast[{index}].basis.{key} must be a non-empty string"
        )
    return value.strip()


def _required_basis_non_negative_int(
    basis: Mapping[str, Any],
    key: str,
    index: int,
) -> int:
    if key not in basis:
        raise ForecastValidationError(f"Missing forecast[{index}].basis.{key}")
    return _non_negative_int(basis[key], f"forecast[{index}].basis.{key}")


def _required_basis_non_negative_number(
    basis: Mapping[str, Any],
    key: str,
    index: int,
) -> float:
    if key not in basis:
        raise ForecastValidationError(f"Missing forecast[{index}].basis.{key}")
    value = basis[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastValidationError(
            f"forecast[{index}].basis.{key} must be a number"
        )
    if value < 0:
        raise ForecastValidationError(
            f"forecast[{index}].basis.{key} must be non-negative"
        )
    return round(float(value), 4)


def _required_basis_bool(
    basis: Mapping[str, Any],
    key: str,
    index: int,
) -> bool:
    if key not in basis:
        raise ForecastValidationError(f"Missing forecast[{index}].basis.{key}")
    value = basis[key]
    if not isinstance(value, bool):
        raise ForecastValidationError(
            f"forecast[{index}].basis.{key} must be a boolean"
        )
    return value


def _validate_forecast_row_confidence_matches_basis(
    *,
    confidence: str,
    basis: Mapping[str, Any],
    index: int,
) -> None:
    expected_confidence = _forecast_confidence(int(basis["observation_count"]))
    if confidence != expected_confidence:
        raise ForecastValidationError(
            f"forecast[{index}].confidence must match basis.observation_count "
            f"({expected_confidence})"
        )


def _round_half_up(value: float) -> int:
    return int(value + 0.5)
