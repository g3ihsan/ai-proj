from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .ai_explanations import (
    NarrationProvider,
    narration_provider_from_name,
    narrate_explanation,
)
from .explanations import (
    ExplanationQueryError,
    ExplanationTargetNotFoundError,
    explain_assignment,
    explain_employee,
    explain_shift,
    explain_shortages,
    explain_summary,
    solve_request_to_explanation_payload,
)
from .schemas import SchemaValidationError


SUPPORTED_ASSISTANT_KINDS = ("summary", "shortages", "assignment", "employee", "shift")


class AssistantIntentError(ValueError):
    pass


class AssistantUnsupportedIntentError(AssistantIntentError):
    pass


@dataclass(frozen=True)
class AssistantIntent:
    kind: str
    target: dict[str, Any]
    missing_fields: tuple[str, ...] = ()
    supported: bool = True


def parse_assistant_intent(
    question: str,
    solve_request: Mapping[str, Any] | None = None,
    target_hint: Mapping[str, Any] | None = None,
) -> AssistantIntent:
    if not isinstance(question, str) or not question.strip():
        raise AssistantIntentError("assistant question must be a non-empty string")

    normalized_question = _normalize_question(question)
    extracted = _extract_numeric_targets(normalized_question)
    name_employee_id = _employee_id_from_exact_name(question, solve_request)
    if name_employee_id is not None and "employee_id" not in extracted:
        extracted["employee_id"] = name_employee_id
    role = _extract_role(normalized_question)
    if role is not None and "role" not in extracted:
        extracted["role"] = role
    extracted.update(_target_from_hint(target_hint))

    kind = _classify_kind(normalized_question, extracted)
    if kind == "unsupported":
        return AssistantIntent(
            kind="unsupported",
            target=extracted,
            missing_fields=(),
            supported=False,
        )

    missing_fields = _missing_fields(kind, extracted)
    if missing_fields:
        return AssistantIntent(
            kind="unsupported",
            target=extracted,
            missing_fields=tuple(missing_fields),
            supported=False,
        )

    return AssistantIntent(
        kind=kind,
        target=_target_for_kind(kind, extracted),
        missing_fields=(),
        supported=True,
    )


def assistant_response_from_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise AssistantIntentError("assistant request must be an object")
    question = payload.get("question")
    if "solve_request" not in payload:
        raise AssistantIntentError("assistant request must include solve_request")
    solve_request = payload["solve_request"]
    if not isinstance(solve_request, Mapping):
        raise AssistantIntentError("assistant solve_request must be an object")

    intent = parse_assistant_intent(
        question,
        solve_request=solve_request,
        target_hint=payload.get("target"),
    )
    provider = narration_provider_from_name(payload.get("provider"))
    return build_assistant_response(
        question=str(question),
        solve_request=solve_request,
        intent=intent,
        target=intent.target,
        provider=provider,
    )


def build_assistant_response(
    question: str,
    solve_request: Mapping[str, Any],
    intent: AssistantIntent,
    target: Mapping[str, Any],
    provider: NarrationProvider | None = None,
) -> dict[str, Any]:
    if not intent.supported:
        return _unsupported_response(question, intent)

    explainer, required_target_keys = _explainer_for_kind(intent.kind)
    explanation_response = solve_request_to_explanation_payload(
        solve_request,
        explainer,
        target={key: target[key] for key in required_target_keys},
    )
    if not explanation_response.get("ok", False):
        _raise_from_explanation_error_payload(explanation_response)

    explanation = explanation_response["result"]
    narration = narrate_explanation(explanation, provider)
    answer = narration["message"]
    return {
        "type": "assistant_response",
        "status": narration["status"],
        "question": question,
        "message": answer,
        "answer": answer,
        "intent": {
            "kind": intent.kind,
            "supported": True,
            "target": dict(target),
        },
        "narration": narration,
        "explanation": explanation,
        "provider": narration["provider"],
        "grounding": narration["grounding"],
        "reason_codes": list(narration["reason_codes"]),
    }


def _normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question.strip().lower())


def _target_from_hint(target_hint: Mapping[str, Any] | None) -> dict[str, Any]:
    if target_hint is None:
        return {}
    if not isinstance(target_hint, Mapping):
        raise AssistantIntentError("assistant target must be an object")
    target: dict[str, Any] = {}
    for key in ("employee_id", "day", "shift"):
        if key in target_hint:
            target[key] = _required_int(target_hint[key], key)
    if "role" in target_hint:
        role = target_hint["role"]
        if not isinstance(role, str) or not role.strip():
            raise AssistantIntentError("assistant target.role must be a non-empty string")
        target["role"] = role.strip()
    return target


def _required_int(value: Any, field: str) -> int:
    if isinstance(value, bool):
        raise AssistantIntentError(f"assistant target.{field} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise AssistantIntentError(
            f"assistant target.{field} must be an integer"
        ) from exc


def _extract_numeric_targets(question: str) -> dict[str, int]:
    patterns = {
        "employee_id": r"\bemployee(?:_id| id)?\s+(\d+)\b",
        "day": r"\bday\s+(\d+)\b",
        "shift": r"\bshift\s+(\d+)\b",
    }
    values: dict[str, int] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, question)
        if match:
            values[field] = int(match.group(1))
    return values


def _extract_role(question: str) -> str | None:
    match = re.search(r"\b(?:role|as|for)\s+([a-z][a-z0-9_-]*)\b", question)
    if not match:
        return None
    role = match.group(1)
    if role in {"employee", "day", "shift", "assignment", "summary"}:
        return None
    return role


def _employee_id_from_exact_name(
    question: str,
    solve_request: Mapping[str, Any] | None,
) -> int | None:
    if not isinstance(solve_request, Mapping):
        return None
    problem = solve_request.get("problem")
    if not isinstance(problem, Mapping):
        return None
    employees = problem.get("employees", [])
    if not isinstance(employees, list):
        return None

    matches: list[int] = []
    for employee in employees:
        if not isinstance(employee, Mapping):
            continue
        name = employee.get("name")
        employee_id = employee.get("employee_id")
        if not isinstance(name, str) or not name.strip():
            continue
        if _contains_exact_phrase(question, name):
            matches.append(_required_int(employee_id, "employee_id"))
    unique_matches = sorted(set(matches))
    if len(unique_matches) > 1:
        raise AssistantIntentError("employee name match is ambiguous")
    return unique_matches[0] if unique_matches else None


def _classify_kind(question: str, target: Mapping[str, Any]) -> str:
    if any(term in question for term in ("shortage", "understaffed", "missing staff", "coverage gap")):
        return "shortages"
    if _looks_like_assignment_question(question, target):
        return "assignment"
    if _looks_like_employee_question(question, target):
        return "employee"
    if _looks_like_shift_question(question, target):
        return "shift"
    if any(
        term in question
        for term in (
            "explain this roster",
            "summarize this schedule",
            "summary",
            "what happened overall",
        )
    ):
        return "summary"
    return "unsupported"


def _looks_like_assignment_question(question: str, target: Mapping[str, Any]) -> bool:
    if "assignment" in question or " assigned" in question or "not assigned" in question:
        return True
    return {"employee_id", "day", "shift", "role"} <= set(target)


def _looks_like_employee_question(question: str, target: Mapping[str, Any]) -> bool:
    if "employee" not in question and "employee_id" not in target:
        return False
    return "day" not in target and "shift" not in target


def _looks_like_shift_question(question: str, target: Mapping[str, Any]) -> bool:
    return "day" in target and "shift" in target and "employee_id" not in target


def _missing_fields(kind: str, target: Mapping[str, Any]) -> list[str]:
    return [
        field
        for field in _required_fields_for_kind(kind)
        if field not in target
    ]


def _required_fields_for_kind(kind: str) -> tuple[str, ...]:
    return {
        "summary": (),
        "shortages": (),
        "assignment": ("employee_id", "day", "shift", "role"),
        "employee": ("employee_id",),
        "shift": ("day", "shift"),
    }[kind]


def _target_for_kind(kind: str, target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: target[field]
        for field in _required_fields_for_kind(kind)
    }


def _explainer_for_kind(kind: str) -> tuple[Callable[..., dict[str, Any]], tuple[str, ...]]:
    if kind == "summary":
        return explain_summary, ()
    if kind == "shortages":
        return explain_shortages, ()
    if kind == "assignment":
        return explain_assignment, ("employee_id", "day", "shift", "role")
    if kind == "employee":
        return explain_employee, ("employee_id",)
    if kind == "shift":
        return explain_shift, ("day", "shift")
    raise AssistantUnsupportedIntentError(f"Unsupported assistant intent {kind}")


def _unsupported_response(question: str, intent: AssistantIntent) -> dict[str, Any]:
    missing = list(intent.missing_fields)
    if missing:
        message = (
            "I need these target fields before I can answer with solver evidence: "
            + ", ".join(missing)
            + "."
        )
    else:
        message = (
            "I can answer summary, shortage, assignment, employee, and shift "
            "explanation questions when enough target information is provided."
        )
    return {
        "type": "assistant_response",
        "status": "unsupported",
        "question": question,
        "message": message,
        "answer": message,
        "intent": {
            "kind": "unsupported",
            "supported": False,
            "target": dict(intent.target),
            "missing_fields": missing,
        },
        "narration": None,
        "explanation": None,
        "provider": None,
        "grounding": None,
        "reason_codes": [],
    }


def _contains_exact_phrase(text: str, phrase: str) -> bool:
    normalized_text = _normalize_question(text)
    normalized_phrase = _normalize_question(phrase)
    if not normalized_phrase:
        return False
    pattern = rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)"
    return re.search(pattern, normalized_text, flags=re.IGNORECASE) is not None


def _raise_from_explanation_error_payload(payload: dict[str, Any]) -> None:
    error = payload.get("error")
    if not isinstance(error, dict):
        raise AssistantIntentError("Could not build deterministic explanation")
    error_type = error.get("type")
    message = str(error.get("message", ""))
    if error_type == "SchemaValidationError":
        raise SchemaValidationError(message)
    if error_type == "ExplanationQueryError":
        raise ExplanationQueryError(message)
    if error_type == "ExplanationTargetNotFoundError":
        raise ExplanationTargetNotFoundError(message)
    raise AssistantIntentError(f"Could not build deterministic explanation: {message}")
