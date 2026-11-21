"""ffprobe command construction and technical video validation."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from fractions import Fraction
from typing import Any

from .commands import CommandRunner, execute_command
from .models import QualityIssue, QualityReport


def build_ffprobe_command(
    video_path: str | os.PathLike[str],
    *,
    ffprobe_bin: str = "ffprobe",
) -> list[str]:
    """Build the machine-readable probe command without executing it."""

    path = os.fspath(video_path)
    if not path:
        raise ValueError("video_path must not be empty")
    if not ffprobe_bin:
        raise ValueError("ffprobe_bin must not be empty")
    return [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]


def _number(value: Any) -> float | None:
    if type(value) in {int, float}:
        parsed = float(value)
    elif isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _positive_number(value: Any) -> float | None:
    parsed = _number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _frame_rate(value: Any) -> float | None:
    if type(value) in {int, float}:
        return _positive_number(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = float(Fraction(value.strip()))
    except (ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(parsed) or parsed <= 0:
        return None
    return parsed


def _positive_integer(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _issue(
    code: str,
    message: str,
    **details: Any,
) -> QualityIssue:
    return QualityIssue(
        code=f"technical.{code}",
        message=message,
        gate="technical",
        hard=True,
        details=details,
    )


def _validate_expectations(
    expected_duration: float | None,
    duration_tolerance: float,
    expected_fps: float | None,
    fps_tolerance: float,
    expected_resolution: tuple[int, int] | None,
) -> None:
    if expected_duration is not None and _positive_number(
        expected_duration
    ) is None:
        raise ValueError("expected_duration must be positive and finite")
    if _number(duration_tolerance) is None or duration_tolerance < 0:
        raise ValueError("duration_tolerance must be finite and non-negative")
    if expected_fps is not None and _positive_number(expected_fps) is None:
        raise ValueError("expected_fps must be positive and finite")
    if _number(fps_tolerance) is None or fps_tolerance < 0:
        raise ValueError("fps_tolerance must be finite and non-negative")
    if expected_resolution is not None:
        if (
            not isinstance(expected_resolution, tuple)
            or len(expected_resolution) != 2
            or any(_positive_integer(item) is None for item in expected_resolution)
        ):
            raise ValueError(
                "expected_resolution must be a (width, height) tuple of "
                "positive integers"
            )


def validate_probe_result(
    probe: Mapping[str, Any] | object,
    *,
    expected_duration: float | None = None,
    duration_tolerance: float = 0.25,
    expected_fps: float | None = None,
    fps_tolerance: float = 0.01,
    expected_resolution: tuple[int, int] | None = None,
) -> QualityReport:
    """Validate an ffprobe JSON object as a usable MP4 video."""

    _validate_expectations(
        expected_duration,
        duration_tolerance,
        expected_fps,
        fps_tolerance,
        expected_resolution,
    )
    if not isinstance(probe, Mapping):
        return QualityReport(
            gate="technical",
            issues=(
                _issue(
                    "probe_output_invalid",
                    "ffprobe output must be a JSON object",
                ),
            ),
        )

    issues: list[QualityIssue] = []
    metrics: dict[str, Any] = {}
    raw_format = probe.get("format")
    format_info = raw_format if isinstance(raw_format, Mapping) else {}
    format_name = format_info.get("format_name")
    metrics["format_name"] = format_name if isinstance(format_name, str) else None
    format_tokens = (
        {token.strip().casefold() for token in format_name.split(",")}
        if isinstance(format_name, str)
        else set()
    )
    if "mp4" not in format_tokens:
        issues.append(
            _issue(
                "container_not_mp4",
                "The probed container is not MP4",
                format_name=metrics["format_name"],
            )
        )

    raw_streams = probe.get("streams")
    streams = raw_streams if isinstance(raw_streams, list) else []
    video_stream = next(
        (
            stream
            for stream in streams
            if isinstance(stream, Mapping)
            and stream.get("codec_type") == "video"
        ),
        None,
    )
    if video_stream is None:
        issues.append(
            _issue(
                "video_stream_missing",
                "The MP4 does not contain a video stream",
            )
        )
        return QualityReport(
            gate="technical",
            issues=tuple(issues),
            metrics=metrics,
        )

    duration = _positive_number(format_info.get("duration"))
    if duration is None:
        duration = _positive_number(video_stream.get("duration"))
    metrics["duration"] = duration
    if duration is None:
        issues.append(
            _issue(
                "duration_invalid",
                "The video duration is missing, non-finite, or not positive",
            )
        )
    elif (
        expected_duration is not None
        and abs(duration - expected_duration) > duration_tolerance
    ):
        issues.append(
            _issue(
                "duration_mismatch",
                "The video duration is outside the allowed tolerance",
                actual=duration,
                expected=expected_duration,
                tolerance=duration_tolerance,
            )
        )

    fps = _frame_rate(video_stream.get("avg_frame_rate"))
    if fps is None:
        fps = _frame_rate(video_stream.get("r_frame_rate"))
    metrics["fps"] = fps
    if fps is None:
        issues.append(
            _issue(
                "fps_invalid",
                "The video frame rate is missing, non-finite, or not positive",
            )
        )
    elif expected_fps is not None and abs(fps - expected_fps) > fps_tolerance:
        issues.append(
            _issue(
                "fps_mismatch",
                "The video frame rate is outside the allowed tolerance",
                actual=fps,
                expected=expected_fps,
                tolerance=fps_tolerance,
            )
        )

    width = _positive_integer(video_stream.get("width"))
    height = _positive_integer(video_stream.get("height"))
    metrics["width"] = width
    metrics["height"] = height
    if width is None or height is None:
        issues.append(
            _issue(
                "resolution_invalid",
                "The video resolution is missing or not positive",
                width=width,
                height=height,
            )
        )
    elif expected_resolution is not None and (
        width,
        height,
    ) != expected_resolution:
        issues.append(
            _issue(
                "resolution_mismatch",
                "The video resolution does not match the expected dimensions",
                actual=[width, height],
                expected=list(expected_resolution),
            )
        )

    return QualityReport(
        gate="technical",
        issues=tuple(issues),
        metrics=metrics,
    )


def probe_video(
    video_path: str | os.PathLike[str],
    *,
    runner: CommandRunner | None = None,
    ffprobe_bin: str = "ffprobe",
    expected_duration: float | None = None,
    duration_tolerance: float = 0.25,
    expected_fps: float | None = None,
    fps_tolerance: float = 0.01,
    expected_resolution: tuple[int, int] | None = None,
) -> QualityReport:
    """Run ffprobe through ``runner`` and validate its JSON response."""

    command = build_ffprobe_command(video_path, ffprobe_bin=ffprobe_bin)
    result = execute_command(command, runner=runner)
    try:
        decoded = json.loads(result.stdout)
    except json.JSONDecodeError:
        decoded = None
    return validate_probe_result(
        decoded,
        expected_duration=expected_duration,
        duration_tolerance=duration_tolerance,
        expected_fps=expected_fps,
        fps_tolerance=fps_tolerance,
        expected_resolution=expected_resolution,
    )


validate_technical_probe = validate_probe_result
