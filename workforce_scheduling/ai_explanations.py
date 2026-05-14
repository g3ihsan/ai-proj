from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


REQUIRED_EXPLANATION_FIELDS = {
    "type",
    "status",
    "title",
    "message",
    "evidence_contract_version",
    "reason_codes",
    "details",
    "recommended_next_checks",
}


class ExplanationNarrationError(ValueError):
    pass


class NarrationProviderError(RuntimeError):
    pass


class NarrationProvider(Protocol):
    name: str
    uses_external_llm: bool

    def narrate(self, prompt: str, explanation_payload: Mapping[str, Any]) -> str:
        ...


@dataclass(frozen=True)
class FakeNarrationProvider:
    name: str = "fake"
    uses_external_llm: bool = False

    def narrate(self, prompt: str, explanation_payload: Mapping[str, Any]) -> str:
        _ = prompt
        explanation_type = str(explanation_payload["type"])
        status = str(explanation_payload["status"])
        message = str(explanation_payload["message"])
        reason_codes = [str(code) for code in explanation_payload["reason_codes"]]
        checks = [
            str(check)
            for check in explanation_payload.get("recommended_next_checks", [])
        ]

        lines = [
            f"{message} Solver status: {status}.",
            f"Explanation type: {explanation_type}.",
        ]
        if reason_codes:
            lines.append("Main reason codes: " + ", ".join(reason_codes) + ".")
        if checks:
            lines.append("Next checks: " + " ".join(checks))
        lines.append("This narration is based only on deterministic solver evidence.")
        return " ".join(lines)


def build_explanation_prompt(explanation_payload: Mapping[str, Any]) -> str:
    explanation = _validated_explanation_payload(explanation_payload)
    evidence_json = json.dumps(explanation, sort_keys=True, separators=(",", ":"))
    return "\n".join(
        [
            "You are rewriting a deterministic workforce scheduling solver explanation.",
            "Use only the provided evidence.",
            "Do not invent facts.",
            "Do not change assignments, shortages, constraints, reason codes, or objective values.",
            "Do not claim the schedule is optimal unless the payload status is OPTIMAL.",
            "Do not provide legal, HR, disciplinary, or compliance advice.",
            "Explain uncertainty plainly when the evidence is local or limited.",
            "Keep the response concise and manager-friendly.",
            "Evidence JSON:",
            evidence_json,
        ]
    )


def narrate_explanation(
    explanation_payload: Mapping[str, Any],
    provider: NarrationProvider | None = None,
) -> dict[str, Any]:
    explanation = _validated_explanation_payload(explanation_payload)
    narration_provider = provider or FakeNarrationProvider()
    prompt = build_explanation_prompt(explanation)
    try:
        narration = narration_provider.narrate(prompt, explanation)
    except Exception as exc:
        raise NarrationProviderError(str(exc)) from exc
    if not isinstance(narration, str) or not narration.strip():
        raise NarrationProviderError("Narration provider returned empty narration")

    return {
        "type": "explanation_narration",
        "source_explanation_type": explanation["type"],
        "status": explanation["status"],
        "title": "Manager-facing explanation narration",
        "message": narration.strip(),
        "evidence_contract_version": explanation["evidence_contract_version"],
        "reason_codes": list(explanation["reason_codes"]),
        "provider": {
            "name": narration_provider.name,
            "uses_external_llm": narration_provider.uses_external_llm,
        },
        "grounding": {
            "source_title": explanation["title"],
            "source_message": explanation["message"],
            "recommended_next_checks": list(
                explanation["recommended_next_checks"]
            ),
        },
    }


def _validated_explanation_payload(
    explanation_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(explanation_payload, Mapping):
        raise ExplanationNarrationError("explanation must be an object")

    missing = sorted(REQUIRED_EXPLANATION_FIELDS - set(explanation_payload))
    if missing:
        raise ExplanationNarrationError(
            "explanation missing required field(s): " + ", ".join(missing)
        )

    if not isinstance(explanation_payload["type"], str) or not explanation_payload[
        "type"
    ].strip():
        raise ExplanationNarrationError("explanation.type must be a non-empty string")
    if not isinstance(explanation_payload["status"], str):
        raise ExplanationNarrationError("explanation.status must be a string")
    if not isinstance(explanation_payload["title"], str):
        raise ExplanationNarrationError("explanation.title must be a string")
    if not isinstance(explanation_payload["message"], str):
        raise ExplanationNarrationError("explanation.message must be a string")
    if not isinstance(explanation_payload["evidence_contract_version"], int):
        raise ExplanationNarrationError(
            "explanation.evidence_contract_version must be an integer"
        )
    if not isinstance(explanation_payload["reason_codes"], list) or not all(
        isinstance(reason_code, str)
        for reason_code in explanation_payload["reason_codes"]
    ):
        raise ExplanationNarrationError(
            "explanation.reason_codes must be a list of strings"
        )
    if not isinstance(explanation_payload["details"], Mapping):
        raise ExplanationNarrationError("explanation.details must be an object")
    if not isinstance(explanation_payload["recommended_next_checks"], list) or not all(
        isinstance(check, str)
        for check in explanation_payload["recommended_next_checks"]
    ):
        raise ExplanationNarrationError(
            "explanation.recommended_next_checks must be a list of strings"
        )

    return {
        "type": explanation_payload["type"].strip(),
        "status": explanation_payload["status"],
        "title": explanation_payload["title"],
        "message": explanation_payload["message"],
        "evidence_contract_version": explanation_payload[
            "evidence_contract_version"
        ],
        "reason_codes": sorted(set(explanation_payload["reason_codes"])),
        "details": dict(explanation_payload["details"]),
        "recommended_next_checks": list(
            explanation_payload["recommended_next_checks"]
        ),
    }
