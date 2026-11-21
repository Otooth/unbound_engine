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


def should_retry(report: QualityReport, retries_used: int = 0) -> bool:
    """Retry a hard failure only when the single retry is still unused."""

    if not isinstance(report, QualityReport):
        raise ValueError("report must be a QualityReport")
    if type(retries_used) is not int or retries_used < 0:
        raise ValueError("retries_used must be a non-negative integer")
    return report.hard_failed and retries_used == 0


aggregate_quality_reports = aggregate_reports
should_retry_hard_failure = should_retry
