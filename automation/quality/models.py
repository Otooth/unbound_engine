"""Shared, JSON-serializable quality gate result models."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


def _normalize_json(value: Any, path: str = "$") -> Any:
    """Return a detached JSON value and reject non-standard JSON values."""

    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string object key")
            normalized[key] = _normalize_json(item, f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ValueError(f"{path} contains a non-JSON value: {type(value).__name__}")


def _require_nonempty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class QualityIssue:
    """One actionable finding produced by a quality gate."""

    code: str
    message: str
    gate: str
    hard: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "code", _require_nonempty_string(self.code, "code")
        )
        object.__setattr__(
            self, "message", _require_nonempty_string(self.message, "message")
        )
        object.__setattr__(
            self, "gate", _require_nonempty_string(self.gate, "gate")
        )
        if type(self.hard) is not bool:
            raise ValueError("hard must be a boolean")
        details = _normalize_json(self.details, "$.details")
        if not isinstance(details, dict):
            raise ValueError("details must be a JSON object")
        object.__setattr__(self, "details", details)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "gate": self.gate,
            "hard": self.hard,
            "details": _normalize_json(self.details),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> QualityIssue:
        if not isinstance(value, Mapping):
            raise ValueError("quality issue must be a JSON object")
        required = {"code", "message", "gate", "hard"}
        allowed = required | {"details"}
        missing = required - value.keys()
        extra = value.keys() - allowed
        if missing:
            raise ValueError(
                f"quality issue is missing fields: {', '.join(sorted(missing))}"
            )
        if extra:
            raise ValueError(
                f"quality issue has unknown fields: {', '.join(sorted(extra))}"
            )
        return cls(
            code=value["code"],
            message=value["message"],
            gate=value["gate"],
            hard=value["hard"],
            details=value.get("details", {}),
        )


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Result of one gate or an aggregate of several gates.

    Soft issues are retained for observability, but only hard issues make
    ``passed`` false.
    """

    gate: str
    issues: tuple[QualityIssue, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "gate", _require_nonempty_string(self.gate, "gate")
        )
        try:
            issues = tuple(self.issues)
        except TypeError as error:
            raise ValueError("issues must be an iterable of QualityIssue") from error
        if not all(isinstance(issue, QualityIssue) for issue in issues):
            raise ValueError("issues must contain only QualityIssue values")
        metrics = _normalize_json(self.metrics, "$.metrics")
        if not isinstance(metrics, dict):
            raise ValueError("metrics must be a JSON object")
        object.__setattr__(self, "issues", issues)
        object.__setattr__(self, "metrics", metrics)

    @property
    def hard_failed(self) -> bool:
        return any(issue.hard for issue in self.issues)

    @property
    def passed(self) -> bool:
        return not self.hard_failed

    @property
    def warnings(self) -> tuple[QualityIssue, ...]:
        return tuple(issue for issue in self.issues if not issue.hard)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": _normalize_json(self.metrics),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> QualityReport:
        if not isinstance(value, Mapping):
            raise ValueError("quality report must be a JSON object")
        required = {"gate", "issues"}
        allowed = required | {"metrics"}
        missing = required - value.keys()
        extra = value.keys() - allowed
        if missing:
            raise ValueError(
                f"quality report is missing fields: {', '.join(sorted(missing))}"
            )
        if extra:
            raise ValueError(
                f"quality report has unknown fields: {', '.join(sorted(extra))}"
            )
        raw_issues = value["issues"]
        if not isinstance(raw_issues, list):
            raise ValueError("quality report issues must be a JSON array")
        return cls(
            gate=value["gate"],
            issues=tuple(QualityIssue.from_dict(item) for item in raw_issues),
            metrics=value.get("metrics", {}),
        )

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=indent,
        )

    @classmethod
    def from_json(cls, value: str | bytes | bytearray) -> QualityReport:
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("quality report is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError("quality report JSON must contain an object")
        return cls.from_dict(decoded)
