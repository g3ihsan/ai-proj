from __future__ import annotations

import copy
from typing import Any, Mapping

from .schemas import (
    RESPONSE_MODE_DEBUG,
    SchemaValidationError,
    parse_solve_request,
    solve_payload,
)


RECOMMENDATION_GOAL_REDUCE_SHORTAGES = "reduce_shortages"
SUPPORTED_RECOMMENDATION_GOALS = (RECOMMENDATION_GOAL_REDUCE_SHORTAGES,)
MAX_RECOMMENDATION_SCENARIOS = 5


class RecommendationError(ValueError):
    pass


class ScenarioValidationError(RecommendationError):
    pass


class ScenarioEvaluationError(RecommendationError):
    pass


def build_baseline_snapshot(result_payload: Mapping[str, Any]) -> dict[str, Any]:
    objective_breakdown = _mapping_field(result_payload, "objective_breakdown")
    metrics = _mapping_field(result_payload, "metrics")
    shortages = _list_field(result_payload, "shortages")
    assignments = _list_field(result_payload, "assignments")
    violations = _list_field(result_payload, "violations")
    return {
        "status": metrics.get("status"),
        "assignment_count": len(assignments),
        "total_shortage": int(objective_breakdown.get("total_shortage", 0)),
        "objective_breakdown": dict(objective_breakdown),
        "metrics": {
            "status": metrics.get("status"),
            "objective_value": metrics.get("objective_value"),
            "best_bound": metrics.get("best_bound"),
            "num_conflicts": metrics.get("num_conflicts"),
            "num_branches": metrics.get("num_branches"),
            "num_variables": metrics.get("num_variables"),
            "num_constraints": metrics.get("num_constraints"),
        },
        "shortages": sorted(
            (dict(shortage) for shortage in shortages),
            key=lambda item: (item["day"], item["shift"], item["role"]),
        ),
        "violations": list(violations),
    }


def generate_shortage_reduction_scenarios(
    solve_request_payload: Mapping[str, Any],
    baseline_result_payload: Mapping[str, Any],
    *,
    max_scenarios: int = MAX_RECOMMENDATION_SCENARIOS,
) -> list[dict[str, Any]]:
    _validate_max_scenarios(max_scenarios)
    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    employee_index = {
        int(employee["employee_id"]): employee
        for employee in employees
        if isinstance(employee, Mapping) and "employee_id" in employee
    }
    scenarios: list[dict[str, Any]] = []
    seen_changes: set[tuple[int, int, int, str]] = set()

    diagnostics = _list_field(baseline_result_payload, "demanded_slot_diagnostics")
    for diagnostic in sorted(
        diagnostics,
        key=lambda item: (item["day"], item["shift"], item["role"]),
    ):
        if int(diagnostic.get("shortage_count", 0)) <= 0:
            continue
        blocked = _mapping_field(diagnostic, "blocked_employee_ids_by_reason")
        for employee_id in sorted(
            int(value) for value in blocked.get("unavailable", [])
        ):
            employee = employee_index.get(employee_id)
            if employee is None:
                continue
            role = str(diagnostic["role"])
            if role not in employee.get("roles", []):
                continue
            day = int(diagnostic["day"])
            shift = int(diagnostic["shift"])
            availability = _list_field(employee, "availability")
            if not _availability_is_false(availability, day, shift):
                continue
            change_key = (employee_id, day, shift, role)
            if change_key in seen_changes:
                continue
            seen_changes.add(change_key)
            scenario_id = (
                f"make_employee_{employee_id}_available_day_{day}_"
                f"shift_{shift}_role_{role}"
            )
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "goal": RECOMMENDATION_GOAL_REDUCE_SHORTAGES,
                    "title": (
                        f"Ask employee {employee_id} to work day {day} "
                        f"shift {shift} as {role}"
                    ),
                    "description": (
                        "Scenario changes only this employee availability flag "
                        "and re-solves with the existing CP-SAT model."
                    ),
                    "changes": [
                        {
                            "type": "set_availability",
                            "employee_id": employee_id,
                            "day": day,
                            "shift": shift,
                            "role": role,
                            "from": False,
                            "to": True,
                        }
                    ],
                }
            )
            if len(scenarios) >= max_scenarios:
                return scenarios
    return scenarios


def evaluate_scenario(
    solve_request_payload: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_request = copy.deepcopy(dict(solve_request_payload))
    for change in _list_field(scenario, "changes"):
        _apply_change(scenario_request, change)
    _force_debug_response_mode(scenario_request)
    response_payload = solve_payload(scenario_request)
    if not response_payload.get("ok", False):
        error = response_payload.get("error", {})
        message = (
            error.get("message", "scenario solve failed")
            if isinstance(error, Mapping)
            else "scenario solve failed"
        )
        raise ScenarioEvaluationError(str(message))
    result_payload = response_payload["result"]
    return {
        "scenario_id": scenario["scenario_id"],
        "goal": scenario["goal"],
        "title": scenario["title"],
        "description": scenario["description"],
        "changes": [dict(change) for change in scenario["changes"]],
        "snapshot": build_baseline_snapshot(result_payload),
    }


def compare_scenario_to_baseline(
    baseline_snapshot: Mapping[str, Any],
    scenario_evaluation: Mapping[str, Any],
) -> dict[str, Any]:
    scenario_snapshot = _mapping_field(scenario_evaluation, "snapshot")
    baseline_shortage = int(baseline_snapshot["total_shortage"])
    scenario_shortage = int(scenario_snapshot["total_shortage"])
    baseline_objective = _objective_value(baseline_snapshot)
    scenario_objective = _objective_value(scenario_snapshot)
    return {
        "total_shortage_delta": scenario_shortage - baseline_shortage,
        "shortage_reduction": baseline_shortage - scenario_shortage,
        "baseline_total_shortage": baseline_shortage,
        "scenario_total_shortage": scenario_shortage,
        "total_objective_delta": (
            scenario_objective - baseline_objective
            if baseline_objective is not None and scenario_objective is not None
            else None
        ),
    }


def recommend_scenarios(
    solve_request_payload: Mapping[str, Any],
    *,
    goal: str = RECOMMENDATION_GOAL_REDUCE_SHORTAGES,
    max_scenarios: int = MAX_RECOMMENDATION_SCENARIOS,
) -> dict[str, Any]:
    if goal not in SUPPORTED_RECOMMENDATION_GOALS:
        raise RecommendationError(
            f"Unsupported recommendation goal {goal}; expected reduce_shortages"
        )
    _validate_max_scenarios(max_scenarios)
    parse_solve_request(solve_request_payload)
    baseline_request = copy.deepcopy(dict(solve_request_payload))
    _force_debug_response_mode(baseline_request)
    baseline_response = solve_payload(baseline_request)
    if not baseline_response.get("ok", False):
        _raise_from_solve_error_payload(baseline_response)

    baseline_result = baseline_response["result"]
    baseline_snapshot = build_baseline_snapshot(baseline_result)
    scenarios = generate_shortage_reduction_scenarios(
        baseline_request,
        baseline_result,
        max_scenarios=max_scenarios,
    )
    evaluated_scenarios: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    for scenario in scenarios:
        evaluation = evaluate_scenario(baseline_request, scenario)
        comparison = compare_scenario_to_baseline(baseline_snapshot, evaluation)
        evaluation["comparison"] = comparison
        evaluated_scenarios.append(evaluation)
        if comparison["shortage_reduction"] > 0:
            recommendations.append(
                {
                    "scenario_id": evaluation["scenario_id"],
                    "title": evaluation["title"],
                    "message": (
                        f"Reduced total shortage by "
                        f"{comparison['shortage_reduction']}."
                    ),
                    "changes": evaluation["changes"],
                    "comparison": comparison,
                }
            )

    recommendations.sort(
        key=lambda item: (
            -item["comparison"]["shortage_reduction"],
            item["scenario_id"],
        )
    )
    return {
        "type": "scenario_recommendations",
        "goal": goal,
        "status": baseline_snapshot["status"],
        "baseline": baseline_snapshot,
        "recommendations": recommendations,
        "evaluated_scenarios": sorted(
            evaluated_scenarios,
            key=lambda item: item["scenario_id"],
        ),
        "summary": {
            "baseline_total_shortage": baseline_snapshot["total_shortage"],
            "scenario_count": len(evaluated_scenarios),
            "recommendation_count": len(recommendations),
            "best_shortage_reduction": (
                recommendations[0]["comparison"]["shortage_reduction"]
                if recommendations
                else 0
            ),
        },
        "metadata": {
            "engine": "deterministic_scenario_recommendations",
            "supported_goals": list(SUPPORTED_RECOMMENDATION_GOALS),
            "max_scenarios": max_scenarios,
            "uses_external_llm": False,
            "changes_solver_behavior": False,
        },
    }


def recommendation_response_from_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise RecommendationError("recommendation request must be an object")
    solve_request = payload.get("solve_request")
    if not isinstance(solve_request, Mapping):
        raise RecommendationError("recommendation request must include solve_request")
    goal = payload.get("goal", RECOMMENDATION_GOAL_REDUCE_SHORTAGES)
    if not isinstance(goal, str) or not goal.strip():
        raise RecommendationError("recommendation goal must be a non-empty string")
    max_scenarios = _max_scenarios_from_payload(payload)
    return recommend_scenarios(
        solve_request,
        goal=goal,
        max_scenarios=max_scenarios,
    )


def _max_scenarios_from_payload(payload: Mapping[str, Any]) -> int:
    value = payload.get("max_scenarios", MAX_RECOMMENDATION_SCENARIOS)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecommendationError("max_scenarios must be an integer")
    _validate_max_scenarios(value)
    return value


def _validate_max_scenarios(value: int) -> None:
    if not 1 <= value <= MAX_RECOMMENDATION_SCENARIOS:
        raise RecommendationError(
            f"max_scenarios must be between 1 and {MAX_RECOMMENDATION_SCENARIOS}"
        )


def _force_debug_response_mode(solve_request_payload: dict[str, Any]) -> None:
    options = solve_request_payload.setdefault("options", {})
    if not isinstance(options, dict):
        raise SchemaValidationError("Solve request options must be an object")
    options["response_mode"] = RESPONSE_MODE_DEBUG


def _apply_change(
    solve_request_payload: dict[str, Any],
    change: Mapping[str, Any],
) -> None:
    change_type = change.get("type")
    if change_type != "set_availability":
        raise ScenarioValidationError(f"Unsupported scenario change {change_type}")
    employee_id = int(change["employee_id"])
    day = int(change["day"])
    shift = int(change["shift"])
    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    for employee in employees:
        if int(employee["employee_id"]) != employee_id:
            continue
        availability = _list_field(employee, "availability")
        if not _availability_in_bounds(availability, day, shift):
            raise ScenarioValidationError(
                f"Availability index outside matrix for employee {employee_id}"
            )
        availability[day][shift] = bool(change["to"])
        return
    raise ScenarioValidationError(f"Unknown employee {employee_id}")


def _availability_is_false(availability: list[Any], day: int, shift: int) -> bool:
    return _availability_in_bounds(availability, day, shift) and (
        availability[day][shift] is False
    )


def _availability_in_bounds(availability: list[Any], day: int, shift: int) -> bool:
    return (
        0 <= day < len(availability)
        and isinstance(availability[day], list)
        and 0 <= shift < len(availability[day])
    )


def _objective_value(snapshot: Mapping[str, Any]) -> int | None:
    objective_breakdown = _mapping_field(snapshot, "objective_breakdown")
    value = objective_breakdown.get("total_objective_value")
    return int(value) if value is not None else None


def _mapping_field(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ScenarioValidationError(f"{key} must be an object")
    return value


def _list_field(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ScenarioValidationError(f"{key} must be a list")
    return value


def _raise_from_solve_error_payload(payload: Mapping[str, Any]) -> None:
    error = payload.get("error")
    if not isinstance(error, Mapping):
        raise ScenarioEvaluationError("baseline solve failed")
    error_type = error.get("type")
    message = str(error.get("message", "baseline solve failed"))
    if error_type == "SchemaValidationError":
        raise SchemaValidationError(message)
    raise ScenarioEvaluationError(message)
