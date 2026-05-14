from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from .data import shift_duration_hours
from .schemas import (
    RESPONSE_MODE_DEBUG,
    SchemaValidationError,
    parse_solve_request,
    solve_payload,
)


RECOMMENDATION_GOAL_REDUCE_SHORTAGES = "reduce_shortages"
SUPPORTED_RECOMMENDATION_GOALS = (RECOMMENDATION_GOAL_REDUCE_SHORTAGES,)
RECOMMENDATION_CONTRACT_VERSION = 1
RECOMMENDATION_TYPE_WHAT_IF = "what_if"
SCENARIO_TYPE_SET_AVAILABILITY = "set_availability"
SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS = "increase_employee_max_hours"
SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE = "add_temporary_employee"
SUPPORTED_SCENARIO_TYPES = (
    SCENARIO_TYPE_SET_AVAILABILITY,
    SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS,
    SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE,
)
DISCARDED_MAX_SCENARIO_LIMIT = "MAX_SCENARIO_LIMIT"
DISCARDED_MAX_RECOMMENDATION_LIMIT = "MAX_RECOMMENDATION_LIMIT"
MAX_RECOMMENDATION_SCENARIOS = 5
MAX_RECOMMENDATIONS = 5


@dataclass(frozen=True)
class RecommendationLimits:
    max_scenarios: int = MAX_RECOMMENDATION_SCENARIOS
    max_recommendations: int = MAX_RECOMMENDATIONS


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
    return _shortage_reduction_scenario_candidates(
        solve_request_payload,
        baseline_result_payload,
    )[:max_scenarios]


def _shortage_reduction_scenario_candidates(
    solve_request_payload: Mapping[str, Any],
    baseline_result_payload: Mapping[str, Any],
) -> list[dict[str, Any]]:
    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    employee_index = {
        int(employee["employee_id"]): employee
        for employee in employees
        if isinstance(employee, Mapping) and "employee_id" in employee
    }
    assigned_hours_by_employee = _assigned_hours_by_employee(baseline_result_payload)
    next_temporary_employee_id = _next_employee_id(employees)
    scenarios: list[dict[str, Any]] = []
    seen_availability_changes: set[tuple[int, int, int, str]] = set()
    seen_max_hours_changes: set[tuple[int, int, int, str]] = set()
    seen_temporary_changes: set[tuple[int, int, str]] = set()

    diagnostics = _list_field(baseline_result_payload, "demanded_slot_diagnostics")
    for diagnostic in sorted(
        diagnostics,
        key=lambda item: (item["day"], item["shift"], item["role"]),
    ):
        if int(diagnostic.get("shortage_count", 0)) <= 0:
            continue
        blocked = _mapping_field(diagnostic, "blocked_employee_ids_by_reason")
        slot_existing_scenario_count = 0
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
            if change_key in seen_availability_changes:
                continue
            seen_availability_changes.add(change_key)
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
                            "type": SCENARIO_TYPE_SET_AVAILABILITY,
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
            slot_existing_scenario_count += 1
        for employee_id in sorted(
            int(value) for value in blocked.get("exceeds_weekly_hours", [])
        ):
            employee = employee_index.get(employee_id)
            if employee is None:
                continue
            role = str(diagnostic["role"])
            if role not in employee.get("roles", []):
                continue
            day = int(diagnostic["day"])
            shift = int(diagnostic["shift"])
            if _has_non_hours_blocker(blocked, employee_id):
                continue
            assigned_hours = assigned_hours_by_employee.get(employee_id, 0)
            duration = shift_duration_hours(
                _list_field(problem, "shift_start_hours"),
                _list_field(problem, "shift_end_hours"),
                shift,
            )
            current_max_hours = int(employee["max_weekly_hours"])
            new_max_hours = assigned_hours + duration
            if new_max_hours <= current_max_hours:
                continue
            change_key = (employee_id, day, shift, role)
            if change_key in seen_max_hours_changes:
                continue
            seen_max_hours_changes.add(change_key)
            scenario_id = (
                f"increase_employee_{employee_id}_max_hours_to_{new_max_hours}_"
                f"for_day_{day}_shift_{shift}_role_{role}"
            )
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "goal": RECOMMENDATION_GOAL_REDUCE_SHORTAGES,
                    "title": (
                        f"Increase employee {employee_id} max weekly hours "
                        f"from {current_max_hours} to {new_max_hours}"
                    ),
                    "description": (
                        "Scenario changes only this employee max weekly hours "
                        "and re-solves with the existing CP-SAT model."
                    ),
                    "changes": [
                        {
                            "type": SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS,
                            "employee_id": employee_id,
                            "day": day,
                            "shift": shift,
                            "role": role,
                            "from": current_max_hours,
                            "to": new_max_hours,
                            "increase_by": new_max_hours - current_max_hours,
                        }
                    ],
                }
            )
            slot_existing_scenario_count += 1
        if slot_existing_scenario_count == 0:
            day = int(diagnostic["day"])
            shift = int(diagnostic["shift"])
            role = str(diagnostic["role"])
            change_key = (day, shift, role)
            if change_key in seen_temporary_changes:
                continue
            seen_temporary_changes.add(change_key)
            duration = shift_duration_hours(
                _list_field(problem, "shift_start_hours"),
                _list_field(problem, "shift_end_hours"),
                shift,
            )
            hourly_cost = _temporary_hourly_cost(employees, role)
            temp_employee_id = next_temporary_employee_id
            scenario_id = (
                f"add_temporary_employee_{temp_employee_id}_day_{day}_"
                f"shift_{shift}_role_{role}"
            )
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "goal": RECOMMENDATION_GOAL_REDUCE_SHORTAGES,
                    "title": (
                        f"Add temporary employee {temp_employee_id} for day {day} "
                        f"shift {shift} as {role}"
                    ),
                    "description": (
                        "Scenario adds one synthetic temporary employee for this "
                        "shortage slot and re-solves with the existing CP-SAT model."
                    ),
                    "changes": [
                        {
                            "type": SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE,
                            "employee_id": temp_employee_id,
                            "name": f"Temporary {role} day {day} shift {shift}",
                            "role": role,
                            "day": day,
                            "shift": shift,
                            "hourly_cost": hourly_cost,
                            "max_weekly_hours": duration,
                        }
                    ],
                }
            )
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
    max_recommendations: int = MAX_RECOMMENDATIONS,
) -> dict[str, Any]:
    if goal not in SUPPORTED_RECOMMENDATION_GOALS:
        raise RecommendationError(
            f"Unsupported recommendation goal {goal}; expected reduce_shortages"
        )
    _validate_max_scenarios(max_scenarios)
    _validate_max_recommendations(max_recommendations)
    parse_solve_request(solve_request_payload)
    baseline_request = copy.deepcopy(dict(solve_request_payload))
    _force_debug_response_mode(baseline_request)
    baseline_response = solve_payload(baseline_request)
    if not baseline_response.get("ok", False):
        _raise_from_solve_error_payload(baseline_response)

    baseline_result = baseline_response["result"]
    baseline_snapshot = build_baseline_snapshot(baseline_result)
    scenario_candidates = _shortage_reduction_scenario_candidates(
        baseline_request,
        baseline_result,
    )
    scenarios = scenario_candidates[:max_scenarios]
    discarded_scenarios = [
        _discarded_scenario(candidate, DISCARDED_MAX_SCENARIO_LIMIT)
        for candidate in scenario_candidates[max_scenarios:]
    ]
    evaluated_scenarios: list[dict[str, Any]] = []
    recommendation_candidates: list[dict[str, Any]] = []

    for scenario in scenarios:
        evaluation = evaluate_scenario(baseline_request, scenario)
        comparison = compare_scenario_to_baseline(baseline_snapshot, evaluation)
        evaluation["comparison"] = comparison
        evaluated_scenarios.append(evaluation)
        if comparison["shortage_reduction"] > 0:
            recommendation_candidates.append(
                {
                    "scenario_id": evaluation["scenario_id"],
                    "title": evaluation["title"],
                    "message": (
                        f"This scenario reduces total shortage by "
                        f"{comparison['shortage_reduction']}."
                    ),
                    "changes": evaluation["changes"],
                    "comparison": comparison,
                    "explanation": build_recommendation_explanation(
                        evaluation,
                        comparison,
                    ),
                    "grounding": build_recommendation_grounding(
                        evaluation,
                        comparison,
                    ),
                }
            )

    recommendation_candidates.sort(
        key=lambda item: (
            -item["comparison"]["shortage_reduction"],
            item["scenario_id"],
        )
    )
    recommendations = recommendation_candidates[:max_recommendations]
    discarded_recommendations = [
        _discarded_recommendation(candidate, DISCARDED_MAX_RECOMMENDATION_LIMIT)
        for candidate in recommendation_candidates[max_recommendations:]
    ]
    return {
        "type": "scenario_recommendations",
        "recommendation_type": RECOMMENDATION_TYPE_WHAT_IF,
        "recommendation_contract_version": RECOMMENDATION_CONTRACT_VERSION,
        "goal": goal,
        "status": baseline_snapshot["status"],
        "baseline": baseline_snapshot,
        "recommendations": recommendations,
        "evaluated_scenarios": sorted(
            evaluated_scenarios,
            key=lambda item: item["scenario_id"],
        ),
        "discarded_scenarios": discarded_scenarios,
        "discarded_recommendations": discarded_recommendations,
        "summary": {
            "baseline_total_shortage": baseline_snapshot["total_shortage"],
            "generated_scenario_count": len(scenario_candidates),
            "scenario_count": len(evaluated_scenarios),
            "discarded_scenario_count": len(discarded_scenarios),
            "generated_recommendation_count": len(recommendation_candidates),
            "recommendation_count": len(recommendations),
            "discarded_recommendation_count": len(discarded_recommendations),
            "best_shortage_reduction": (
                recommendations[0]["comparison"]["shortage_reduction"]
                if recommendations
                else 0
            ),
        },
        "limits": {
            "max_scenarios": max_scenarios,
            "max_recommendations": max_recommendations,
            "scenario_limit_reached": bool(discarded_scenarios),
            "recommendation_limit_reached": bool(discarded_recommendations),
        },
        "metadata": {
            "engine": "deterministic_scenario_recommendations",
            "recommendation_type": RECOMMENDATION_TYPE_WHAT_IF,
            "recommendation_contract_version": RECOMMENDATION_CONTRACT_VERSION,
            "supported_goals": list(SUPPORTED_RECOMMENDATION_GOALS),
            "supported_scenario_types": list(SUPPORTED_SCENARIO_TYPES),
            "max_scenarios": max_scenarios,
            "max_recommendations": max_recommendations,
            "uses_external_llm": False,
            "changes_solver_behavior": False,
        },
    }


def _discarded_scenario(
    scenario: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "scenario_id": scenario["scenario_id"],
        "goal": scenario["goal"],
        "status": "discarded",
        "reason": reason,
        "title": scenario["title"],
        "changes": [dict(change) for change in scenario["changes"]],
    }


def _discarded_recommendation(
    recommendation: Mapping[str, Any],
    reason: str,
) -> dict[str, Any]:
    return {
        "scenario_id": recommendation["scenario_id"],
        "status": "discarded",
        "reason": reason,
        "title": recommendation["title"],
        "changes": [dict(change) for change in recommendation["changes"]],
        "comparison": dict(recommendation["comparison"]),
        "explanation": dict(recommendation["explanation"]),
        "grounding": dict(recommendation["grounding"]),
    }


def build_recommendation_grounding(
    scenario_evaluation: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    changes = _list_field(scenario_evaluation, "changes")
    return {
        "source": "deterministic_scenario_solve",
        "scenario_id": str(scenario_evaluation["scenario_id"]),
        "scenario_type": _scenario_type_from_changes(changes),
        "baseline_total_shortage": int(comparison["baseline_total_shortage"]),
        "scenario_total_shortage": int(comparison["scenario_total_shortage"]),
        "shortage_reduction": int(comparison["shortage_reduction"]),
        "uses_external_llm": False,
    }


def build_recommendation_explanation(
    scenario_evaluation: Mapping[str, Any],
    comparison: Mapping[str, Any],
) -> dict[str, Any]:
    changes = _list_field(scenario_evaluation, "changes")
    return {
        "why_it_helps": _recommendation_why_it_helps(changes),
        "what_changes": _recommendation_what_changes(changes),
        "expected_improvement": _recommendation_expected_improvement(comparison),
        "tradeoffs": _recommendation_tradeoffs(changes, comparison),
        "manager_next_checks": _recommendation_next_checks(changes),
    }


def _recommendation_why_it_helps(changes: list[Any]) -> str:
    if not changes:
        return (
            "The scenario was re-solved by the deterministic recommendation "
            "engine and reduced shortage in the CP-SAT comparison."
        )
    change = _mapping_change(changes[0])
    change_type = str(change.get("type", ""))
    day = change.get("day")
    shift = change.get("shift")
    role = change.get("role")
    slot = f"day {day} shift {shift}"
    if change_type == SCENARIO_TYPE_SET_AVAILABILITY:
        return (
            f"The baseline had an uncovered {role} requirement on {slot}. "
            f"This scenario makes qualified employee {change.get('employee_id')} "
            "available for that shortage slot."
        )
    if change_type == SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS:
        return (
            f"The baseline had an uncovered {role} requirement on {slot}, and "
            f"employee {change.get('employee_id')} was blocked by max weekly "
            "hours. This scenario raises that limit and re-solves the same "
            "CP-SAT model."
        )
    if change_type == SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE:
        return (
            f"The baseline had an uncovered {role} requirement on {slot}. "
            "No existing-employee scenario was available for that slot, so "
            "this scenario adds one qualified temporary employee and re-solves."
        )
    return (
        "The scenario was re-solved by the deterministic recommendation engine "
        "and reduced shortage in the CP-SAT comparison."
    )


def _recommendation_what_changes(changes: list[Any]) -> list[str]:
    descriptions: list[str] = []
    for raw_change in changes:
        change = _mapping_change(raw_change)
        change_type = str(change.get("type", ""))
        if change_type == SCENARIO_TYPE_SET_AVAILABILITY:
            descriptions.append(
                f"Sets employee {change.get('employee_id')} availability to "
                f"{str(change.get('to')).lower()} for day {change.get('day')} "
                f"shift {change.get('shift')} as {change.get('role')}."
            )
        elif change_type == SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS:
            descriptions.append(
                f"Increases employee {change.get('employee_id')} max weekly "
                f"hours from {change.get('from')} to {change.get('to')}."
            )
        elif change_type == SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE:
            descriptions.extend(
                [
                    (
                        f"Adds temporary employee {change.get('employee_id')} "
                        f"with role {change.get('role')}."
                    ),
                    (
                        "Makes the temporary employee available only for day "
                        f"{change.get('day')} shift {change.get('shift')}."
                    ),
                ]
            )
        else:
            descriptions.append(f"Applies scenario change {change_type}.")
    return descriptions


def _recommendation_expected_improvement(comparison: Mapping[str, Any]) -> str:
    return (
        "Total shortage decreases from "
        f"{int(comparison['baseline_total_shortage'])} to "
        f"{int(comparison['scenario_total_shortage'])}."
    )


def _recommendation_tradeoffs(
    changes: list[Any],
    comparison: Mapping[str, Any],
) -> list[str]:
    tradeoffs: list[str] = []
    change_types = {
        str(_mapping_change(change).get("type", ""))
        for change in changes
    }
    if SCENARIO_TYPE_SET_AVAILABILITY in change_types:
        tradeoffs.append(
            "Requires confirming the employee can actually work that slot."
        )
    if SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS in change_types:
        tradeoffs.append(
            "May increase workload or overtime risk for the employee."
        )
    if SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE in change_types:
        tradeoffs.append(
            "May increase staffing cost because an additional employee is introduced."
        )
    objective_delta = comparison.get("total_objective_delta")
    if objective_delta is not None and int(objective_delta) > 0:
        tradeoffs.append(
            f"Total objective value increases by {int(objective_delta)} "
            "under the solver scoring model."
        )
    if not tradeoffs:
        tradeoffs.append(
            "No additional tradeoff was detected in the deterministic comparison."
        )
    return tradeoffs


def _recommendation_next_checks(changes: list[Any]) -> list[str]:
    checks = [
        "Confirm the change is operationally feasible before editing the roster.",
        "Confirm this change follows local staffing policy.",
    ]
    change_types = {
        str(_mapping_change(change).get("type", ""))
        for change in changes
    }
    if SCENARIO_TYPE_SET_AVAILABILITY in change_types:
        checks.insert(0, "Confirm the employee is actually available for the slot.")
    if SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS in change_types:
        checks.insert(0, "Confirm the higher weekly-hours limit is allowed.")
    if SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE in change_types:
        checks.insert(0, "Confirm a temporary worker is actually available.")
        checks.insert(1, "Confirm the temporary staffing cost is acceptable.")
    return checks


def _scenario_type_from_changes(changes: list[Any]) -> str:
    if not changes:
        return "unknown"
    return str(_mapping_change(changes[0]).get("type", "unknown"))


def _mapping_change(change: Any) -> Mapping[str, Any]:
    if isinstance(change, Mapping):
        return change
    raise ScenarioValidationError("scenario change must be an object")


def recommendation_response_from_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise RecommendationError("recommendation request must be an object")
    solve_request = payload.get("solve_request")
    if not isinstance(solve_request, Mapping):
        raise RecommendationError("recommendation request must include solve_request")
    goal = payload.get("goal", RECOMMENDATION_GOAL_REDUCE_SHORTAGES)
    if not isinstance(goal, str) or not goal.strip():
        raise RecommendationError("recommendation goal must be a non-empty string")
    limits = _limits_from_payload(payload)
    return recommend_scenarios(
        solve_request,
        goal=goal,
        max_scenarios=limits.max_scenarios,
        max_recommendations=limits.max_recommendations,
    )


def _limits_from_payload(payload: Mapping[str, Any]) -> RecommendationLimits:
    limits_payload = payload.get("limits", {})
    if limits_payload is None:
        limits_payload = {}
    if not isinstance(limits_payload, Mapping):
        raise RecommendationError("limits must be an object")
    max_scenarios = _limit_int(
        limits_payload.get(
            "max_scenarios",
            payload.get("max_scenarios", MAX_RECOMMENDATION_SCENARIOS),
        ),
        "max_scenarios",
    )
    max_recommendations = _limit_int(
        limits_payload.get("max_recommendations", MAX_RECOMMENDATIONS),
        "max_recommendations",
    )
    _validate_max_scenarios(max_scenarios)
    _validate_max_recommendations(max_recommendations)
    return RecommendationLimits(
        max_scenarios=max_scenarios,
        max_recommendations=max_recommendations,
    )


def _limit_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RecommendationError(f"{field} must be an integer")
    return value


def _validate_max_scenarios(value: int) -> None:
    if not 1 <= value <= MAX_RECOMMENDATION_SCENARIOS:
        raise RecommendationError(
            f"max_scenarios must be between 1 and {MAX_RECOMMENDATION_SCENARIOS}"
        )


def _validate_max_recommendations(value: int) -> None:
    if not 1 <= value <= MAX_RECOMMENDATIONS:
        raise RecommendationError(
            f"max_recommendations must be between 1 and {MAX_RECOMMENDATIONS}"
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
    if not isinstance(change, Mapping):
        raise ScenarioValidationError("scenario change must be an object")
    change_type = _required_str_change_field(change, "type")
    if change_type == SCENARIO_TYPE_SET_AVAILABILITY:
        _apply_set_availability_change(solve_request_payload, change)
        return
    if change_type == SCENARIO_TYPE_INCREASE_EMPLOYEE_MAX_HOURS:
        _apply_increase_employee_max_hours_change(solve_request_payload, change)
        return
    if change_type == SCENARIO_TYPE_ADD_TEMPORARY_EMPLOYEE:
        _apply_add_temporary_employee_change(solve_request_payload, change)
        return
    raise ScenarioValidationError(f"Unsupported scenario change {change_type}")


def _apply_set_availability_change(
    solve_request_payload: dict[str, Any],
    change: Mapping[str, Any],
) -> None:
    employee_id = _required_int_change_field(change, "employee_id")
    day = _required_int_change_field(change, "day")
    shift = _required_int_change_field(change, "shift")
    _optional_str_change_field(change, "role")
    to_value = _required_bool_change_field(change, "to")
    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    for employee in employees:
        if _required_int_change_field(employee, "employee_id") != employee_id:
            continue
        availability = _list_field(employee, "availability")
        if not _availability_in_bounds(availability, day, shift):
            raise ScenarioValidationError(
                f"Availability index outside matrix for employee {employee_id}"
            )
        availability[day][shift] = to_value
        return
    raise ScenarioValidationError(f"Unknown employee {employee_id}")


def _apply_increase_employee_max_hours_change(
    solve_request_payload: dict[str, Any],
    change: Mapping[str, Any],
) -> None:
    employee_id = _required_int_change_field(change, "employee_id")
    new_max_hours = _required_int_change_field(change, "to")
    current_max_hours = _required_int_change_field(change, "from")
    if new_max_hours <= current_max_hours:
        raise ScenarioValidationError(
            "increase_employee_max_hours change must increase max_weekly_hours"
        )
    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    for employee in employees:
        if _required_int_change_field(employee, "employee_id") != employee_id:
            continue
        existing_max_hours = _required_int_change_field(
            employee,
            "max_weekly_hours",
        )
        if existing_max_hours != current_max_hours:
            raise ScenarioValidationError(
                f"max_weekly_hours baseline mismatch for employee {employee_id}"
            )
        employee["max_weekly_hours"] = new_max_hours
        return
    raise ScenarioValidationError(f"Unknown employee {employee_id}")


def _apply_add_temporary_employee_change(
    solve_request_payload: dict[str, Any],
    change: Mapping[str, Any],
) -> None:
    employee_id = _required_int_change_field(change, "employee_id")
    name = _required_str_change_field(change, "name")
    role = _required_str_change_field(change, "role")
    day = _required_int_change_field(change, "day")
    shift = _required_int_change_field(change, "shift")
    hourly_cost = _required_int_change_field(change, "hourly_cost")
    max_weekly_hours = _required_int_change_field(change, "max_weekly_hours")
    if hourly_cost < 0:
        raise ScenarioValidationError(
            "temporary employee hourly_cost must be non-negative"
        )
    if max_weekly_hours <= 0:
        raise ScenarioValidationError(
            "temporary employee max_weekly_hours must be positive"
        )

    problem = _mapping_field(solve_request_payload, "problem")
    employees = _list_field(problem, "employees")
    if any(
        _required_int_change_field(employee, "employee_id") == employee_id
        for employee in employees
    ):
        raise ScenarioValidationError(f"Employee {employee_id} already exists")
    roles = _list_field(problem, "roles")
    if role not in {str(value) for value in roles}:
        raise ScenarioValidationError(f"Unknown temporary employee role {role}")
    days = _list_field(problem, "days")
    shifts = _list_field(problem, "shifts")
    if day not in {int(value) for value in days}:
        raise ScenarioValidationError(f"Unknown temporary employee day {day}")
    if not 0 <= shift < len(shifts):
        raise ScenarioValidationError(f"Unknown temporary employee shift {shift}")

    availability = [
        [False for _shift in range(len(shifts))] for _day in range(len(days))
    ]
    if not _availability_in_bounds(availability, day, shift):
        raise ScenarioValidationError(
            f"Temporary employee availability index outside matrix for employee {employee_id}"
        )
    availability[day][shift] = True
    employees.append(
        {
            "employee_id": employee_id,
            "name": name,
            "roles": [role],
            "hourly_cost": hourly_cost,
            "max_weekly_hours": max_weekly_hours,
            "availability": availability,
        }
    )


def _required_int_change_field(
    payload: Mapping[str, Any],
    field: str,
) -> int:
    if field not in payload:
        raise ScenarioValidationError(f"scenario change missing required field {field}")
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ScenarioValidationError(
            f"scenario change field {field} must be an integer"
        )
    return value


def _required_bool_change_field(
    payload: Mapping[str, Any],
    field: str,
) -> bool:
    if field not in payload:
        raise ScenarioValidationError(f"scenario change missing required field {field}")
    value = payload[field]
    if not isinstance(value, bool):
        raise ScenarioValidationError(
            f"scenario change field {field} must be a boolean"
        )
    return value


def _required_str_change_field(
    payload: Mapping[str, Any],
    field: str,
) -> str:
    if field not in payload:
        raise ScenarioValidationError(f"scenario change missing required field {field}")
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise ScenarioValidationError(
            f"scenario change field {field} must be a non-empty string"
        )
    return value.strip()


def _optional_str_change_field(
    payload: Mapping[str, Any],
    field: str,
) -> str | None:
    if field not in payload:
        return None
    return _required_str_change_field(payload, field)


def _assigned_hours_by_employee(result_payload: Mapping[str, Any]) -> dict[int, int]:
    fairness_metrics = _mapping_field(result_payload, "fairness_metrics")
    records = _list_field(fairness_metrics, "assigned_hours_per_employee")
    return {
        int(record["employee_id"]): int(record["assigned_hours"])
        for record in records
        if isinstance(record, Mapping)
    }


def _has_non_hours_blocker(
    blocked_employee_ids_by_reason: Mapping[str, Any],
    employee_id: int,
) -> bool:
    for reason, employee_ids in blocked_employee_ids_by_reason.items():
        if reason == "exceeds_weekly_hours":
            continue
        if employee_id in {int(value) for value in employee_ids}:
            return True
    return False


def _next_employee_id(employees: list[Any]) -> int:
    employee_ids = [
        int(employee["employee_id"])
        for employee in employees
        if isinstance(employee, Mapping) and "employee_id" in employee
    ]
    return (max(employee_ids) + 1) if employee_ids else 0


def _temporary_hourly_cost(employees: list[Any], role: str) -> int:
    role_costs = [
        hourly_cost
        for employee in employees
        if isinstance(employee, Mapping) and role in employee.get("roles", [])
        for hourly_cost in [_optional_non_bool_int(employee.get("hourly_cost"))]
        if hourly_cost is not None
    ]
    all_costs = [
        hourly_cost
        for employee in employees
        if isinstance(employee, Mapping)
        for hourly_cost in [_optional_non_bool_int(employee.get("hourly_cost"))]
        if hourly_cost is not None
    ]
    if role_costs:
        return max(role_costs)
    if all_costs:
        return max(all_costs)
    return 0


def _optional_non_bool_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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
