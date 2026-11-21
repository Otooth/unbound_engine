"""Composition and one-retry policy for quality reports."""

from __future__ import annotations

from collections.abc import Iterable

from .models import QualityReport


def aggregate_reports(
    reports: Iterable[QualityReport],
    *,
    gate: str = "aggregate",
) -> QualityReport:
    """Flatten issues while preserving every source gate's metrics."""

    materialized = tuple(reports)
    if not all(isinstance(report, QualityReport) for report in materialized):
        raise ValueError("reports must contain only QualityReport values")
    return QualityReport(
        gate=gate,
        issues=tuple(
            issue
            for report in materialized
            for issue in report.issues
        ),
        metrics={
            "reports": [
                {
                    "gate": report.gate,
                    "passed": report.passed,
                    "metrics": report.metrics,
                }
                for report in materialized
            ]
        },
    )


RETRYABLE_GATES = frozenset({"semantic", "temporal"})


def should_retry(report: QualityReport, retries_used: int = 0) -> bool:
    """Retry once, and only when a new seed and prompt can fix every defect.

    Technical and boundary failures are deterministic for the same inputs,
    so regenerating them costs a full generation without changing the result.
    """

    if not isinstance(report, QualityReport):
        raise ValueError("report must be a QualityReport")
    if type(retries_used) is not int or retries_used < 0:
        raise ValueError("retries_used must be a non-negative integer")
    hard_issues = [issue for issue in report.issues if issue.hard]
    return (
        bool(hard_issues)
        and retries_used == 0
        and all(issue.gate in RETRYABLE_GATES for issue in hard_issues)
    )


aggregate_quality_reports = aggregate_reports
should_retry_hard_failure = should_retry
