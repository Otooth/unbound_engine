"""Strict Qwen Vision audit normalization and semantic gate policy."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from .models import QualityIssue, QualityReport


SEMANTIC_CONFIDENCE_THRESHOLD = 0.75

VISION_AUDIT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "anatomy_valid",
        "front_back_fusion_absent",
        "orientation_consistent",
        "identity_consistent",
        "instruction_followed",
        "confidence",
        "evidence",
        "corrective_prompt",
    ],
    "properties": {
        "anatomy_valid": {"type": "boolean"},
        "front_back_fusion_absent": {"type": "boolean"},
        "orientation_consistent": {"type": "boolean"},
        "identity_consistent": {
            "oneOf": [
                {"type": "boolean"},
                {"type": "string", "const": "not_applicable"},
            ]
        },
        "instruction_followed": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
        },
        "corrective_prompt": {"type": "string"},
    },
}

_AUDIT_FIELDS = frozenset(VISION_AUDIT_JSON_SCHEMA["required"])
_BOOLEAN_FIELDS = (
    "anatomy_valid",
    "front_back_fusion_absent",
    "orientation_consistent",
    "instruction_followed",
)
_VERDICT_MESSAGES = {
    "anatomy_valid": "The anatomy audit found an invalid body configuration",
    "front_back_fusion_absent": (
        "The audit found front/back body-surface fusion"
    ),
    "orientation_consistent": (
        "The audit found inconsistent body or camera-relative orientation"
    ),
    "identity_consistent": (
        "The audit found that subject identity was not preserved"
    ),
    "instruction_followed": (
        "The audit found that the requested instruction was not followed"
    ),
}


class VisionAuditSchemaError(ValueError):
    """Raised when a model response does not exactly match the audit schema."""


def _decode_payload(
    payload: str | bytes | bytearray | Mapping[str, Any],
) -> Mapping[str, Any]:
    if isinstance(payload, (str, bytes, bytearray)):
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise VisionAuditSchemaError(
                "vision audit response is not valid JSON"
            ) from error
    else:
        decoded = payload
    if not isinstance(decoded, Mapping):
        raise VisionAuditSchemaError("vision audit response must be an object")
    return decoded


def normalize_vision_audit(
    payload: str | bytes | bytearray | Mapping[str, Any],
) -> dict[str, Any]:
    """Validate exact keys and types without truthy/string coercion."""

    decoded = _decode_payload(payload)
    keys = set(decoded.keys())
    if not all(isinstance(key, str) for key in keys):
        raise VisionAuditSchemaError("vision audit keys must be strings")
    missing = _AUDIT_FIELDS - keys
    extra = keys - _AUDIT_FIELDS
    if missing:
        raise VisionAuditSchemaError(
            f"vision audit is missing fields: {', '.join(sorted(missing))}"
        )
    if extra:
        raise VisionAuditSchemaError(
            f"vision audit has unknown fields: {', '.join(sorted(extra))}"
        )

    normalized: dict[str, Any] = {}
    for field_name in _BOOLEAN_FIELDS:
        value = decoded[field_name]
        if type(value) is not bool:
            raise VisionAuditSchemaError(
                f"{field_name} must be a JSON boolean"
            )
        normalized[field_name] = value

    identity = decoded["identity_consistent"]
    if type(identity) is bool:
        normalized["identity_consistent"] = identity
    elif identity == "not_applicable":
        normalized["identity_consistent"] = "not_applicable"
    else:
        raise VisionAuditSchemaError(
            "identity_consistent must be a boolean or 'not_applicable'"
        )

    confidence = decoded["confidence"]
    if type(confidence) not in {int, float}:
        raise VisionAuditSchemaError("confidence must be a JSON number")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        raise VisionAuditSchemaError("confidence must be between 0 and 1")
    normalized["confidence"] = confidence

    evidence = decoded["evidence"]
    if not isinstance(evidence, list):
        raise VisionAuditSchemaError("evidence must be an array of strings")
    normalized_evidence: list[str] = []
    for index, item in enumerate(evidence):
        if not isinstance(item, str):
            raise VisionAuditSchemaError(
                f"evidence[{index}] must be a string"
            )
        normalized_evidence.append(item.strip())
    normalized["evidence"] = normalized_evidence

    corrective_prompt = decoded["corrective_prompt"]
    if not isinstance(corrective_prompt, str):
        raise VisionAuditSchemaError("corrective_prompt must be a string")
    normalized["corrective_prompt"] = corrective_prompt.strip()
    return normalized


def evaluate_vision_audit(
    payload: str | bytes | bytearray | Mapping[str, Any],
    *,
    confidence_threshold: float = SEMANTIC_CONFIDENCE_THRESHOLD,
) -> QualityReport:
    """Convert a normalized semantic audit into hard/soft quality issues."""

    if (
        type(confidence_threshold) not in {int, float}
        or not math.isfinite(float(confidence_threshold))
        or not 0.0 <= float(confidence_threshold) <= 1.0
    ):
        raise ValueError("confidence_threshold must be between 0 and 1")
    confidence_threshold = float(confidence_threshold)
    audit = normalize_vision_audit(payload)
    confidence = audit["confidence"]
    high_confidence = confidence >= confidence_threshold
    details = {
        "confidence": confidence,
        "evidence": audit["evidence"],
        "corrective_prompt": audit["corrective_prompt"],
    }

    issues: list[QualityIssue] = []
    verdict_fields = (*_BOOLEAN_FIELDS[:-1], "identity_consistent", _BOOLEAN_FIELDS[-1])
    for field_name in verdict_fields:
        if audit[field_name] is False:
            issues.append(
                QualityIssue(
                    code=f"semantic.{field_name}",
                    message=_VERDICT_MESSAGES[field_name],
                    gate="semantic",
                    hard=high_confidence,
                    details=details,
                )
            )
    if not high_confidence:
        issues.append(
            QualityIssue(
                code="semantic.low_confidence",
                message=(
                    "The semantic audit confidence is below the hard-gate "
                    "threshold"
                ),
                gate="semantic",
                hard=False,
                details={
                    "confidence": confidence,
                    "threshold": confidence_threshold,
                },
            )
        )

    return QualityReport(
        gate="semantic",
        issues=tuple(issues),
        metrics={
            "audit": audit,
            "confidence_threshold": confidence_threshold,
        },
    )


normalize_qwen_vision_result = normalize_vision_audit
