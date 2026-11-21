"""FFmpeg black-frame and frozen-video quality gates."""

from __future__ import annotations

import math
import os
import re
from typing import Any

from .commands import CommandRunner, execute_command
from .models import QualityIssue, QualityReport


DEFAULT_BLACK_DURATION = 0.5
DEFAULT_FREEZE_DURATION = 2.0

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_BLACK_PATTERN = re.compile(
    rf"black_start:(?P<start>{_NUMBER})\s+"
    rf"black_end:(?P<end>{_NUMBER})\s+"
    rf"black_duration:(?P<duration>{_NUMBER})"
)
_FREEZE_START_PATTERN = re.compile(
    rf"(?:lavfi\.freezedetect\.)?freeze_start:\s*(?P<value>{_NUMBER})"
)
_FREEZE_END_PATTERN = re.compile(
    rf"(?:lavfi\.freezedetect\.)?freeze_end:\s*(?P<value>{_NUMBER})"
)
_FREEZE_DURATION_PATTERN = re.compile(
    rf"(?:lavfi\.freezedetect\.)?freeze_duration:\s*(?P<value>{_NUMBER})"
)


def build_temporal_analysis_command(
    video_path: str | os.PathLike[str],
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    path = os.fspath(video_path)
    if not path:
        raise ValueError("video_path must not be empty")
    if not ffmpeg_bin:
        raise ValueError("ffmpeg_bin must not be empty")
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-i",
        path,
        "-map",
        "0:v:0",
        "-vf",
        (
            "blackdetect=d=0.1:pix_th=0.10,"
            "freezedetect=n=-50dB:d=0.5"
        ),
        "-an",
        "-f",
        "null",
        "-",
    ]


def _finite_nonnegative(value: float, name: str) -> float:
    if (
        type(value) not in {int, float}
        or not math.isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def parse_temporal_findings(
    output: str | bytes,
    *,
    video_duration: float | None = None,
) -> dict[str, list[dict[str, float]]]:
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str):
        raise ValueError("ffmpeg temporal output must be text")
    duration = (
        _finite_nonnegative(video_duration, "video_duration")
        if video_duration is not None
        else None
    )
    black = [
        {
            "start": float(match.group("start")),
            "end": float(match.group("end")),
            "duration": float(match.group("duration")),
        }
        for match in _BLACK_PATTERN.finditer(output)
    ]
    starts = [
        float(match.group("value"))
        for match in _FREEZE_START_PATTERN.finditer(output)
    ]
    ends = [
        float(match.group("value"))
        for match in _FREEZE_END_PATTERN.finditer(output)
    ]
    durations = [
        float(match.group("value"))
        for match in _FREEZE_DURATION_PATTERN.finditer(output)
    ]
    frozen: list[dict[str, float]] = []
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else None
        frozen_duration = (
            durations[index] if index < len(durations) else None
        )
        if frozen_duration is None and end is not None:
            frozen_duration = max(0.0, end - start)
        if frozen_duration is None and duration is not None:
            frozen_duration = max(0.0, duration - start)
            end = duration
        if frozen_duration is None:
            continue
        frozen.append(
            {
                "start": start,
                "end": (
                    end if end is not None else start + frozen_duration
                ),
                "duration": frozen_duration,
            }
        )
    return {"black_intervals": black, "freeze_intervals": frozen}


def evaluate_temporal_findings(
    findings: dict[str, list[dict[str, float]]],
    *,
    min_black_duration: float = DEFAULT_BLACK_DURATION,
    min_freeze_duration: float = DEFAULT_FREEZE_DURATION,
) -> QualityReport:
    min_black_duration = _finite_nonnegative(
        min_black_duration, "min_black_duration"
    )
    min_freeze_duration = _finite_nonnegative(
        min_freeze_duration, "min_freeze_duration"
    )
    black = findings.get("black_intervals", [])
    frozen = findings.get("freeze_intervals", [])
    issues: list[QualityIssue] = []
    excessive_black = [
        interval
        for interval in black
        if interval.get("duration", 0.0) >= min_black_duration
    ]
    excessive_freeze = [
        interval
        for interval in frozen
        if interval.get("duration", 0.0) >= min_freeze_duration
    ]
    if excessive_black:
        issues.append(
            QualityIssue(
                code="temporal.black_frames",
                message="The video contains an excessive black interval",
                gate="temporal",
                hard=True,
                details={"intervals": excessive_black},
            )
        )
    if excessive_freeze:
        issues.append(
            QualityIssue(
                code="temporal.frozen_video",
                message="The video contains an excessive frozen interval",
                gate="temporal",
                hard=True,
                details={"intervals": excessive_freeze},
            )
        )
    return QualityReport(
        gate="temporal",
        issues=tuple(issues),
        metrics={
            **findings,
            "min_black_duration": min_black_duration,
            "min_freeze_duration": min_freeze_duration,
        },
    )


def analyze_temporal_video(
    video_path: str | os.PathLike[str],
    *,
    runner: CommandRunner | None = None,
    ffmpeg_bin: str = "ffmpeg",
    video_duration: float | None = None,
    min_black_duration: float = DEFAULT_BLACK_DURATION,
    min_freeze_duration: float = DEFAULT_FREEZE_DURATION,
) -> QualityReport:
    result = execute_command(
        build_temporal_analysis_command(video_path, ffmpeg_bin=ffmpeg_bin),
        runner=runner,
    )
    findings = parse_temporal_findings(
        f"{result.stdout}\n{result.stderr}",
        video_duration=video_duration,
    )
    return evaluate_temporal_findings(
        findings,
        min_black_duration=min_black_duration,
        min_freeze_duration=min_freeze_duration,
    )
