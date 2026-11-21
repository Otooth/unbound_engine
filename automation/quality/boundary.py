"""SSIM/PSNR boundary comparison logic and ffmpeg command adapter."""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass

from .commands import CommandRunner, execute_command
from .models import QualityIssue, QualityReport


DEFAULT_SSIM_THRESHOLD = 0.95
DEFAULT_PSNR_THRESHOLD = 30.0

_NUMBER_PATTERN = r"(?:[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[-+]?\d+)?|inf)"
_SSIM_PATTERN = re.compile(rf"\bAll:\s*({_NUMBER_PATTERN})", re.IGNORECASE)
_PSNR_PATTERN = re.compile(
    rf"\baverage:\s*({_NUMBER_PATTERN})", re.IGNORECASE
)


@dataclass(frozen=True, slots=True)
class BoundaryMetrics:
    ssim: float
    psnr: float

    def __post_init__(self) -> None:
        if (
            type(self.ssim) not in {int, float}
            or not math.isfinite(float(self.ssim))
            or not 0.0 <= float(self.ssim) <= 1.0
        ):
            raise ValueError("ssim must be finite and between 0 and 1")
        if type(self.psnr) not in {int, float}:
            raise ValueError("psnr must be numeric")
        normalized_psnr = float(self.psnr)
        if math.isnan(normalized_psnr) or normalized_psnr == -math.inf:
            raise ValueError("psnr must not be NaN or negative infinity")
        object.__setattr__(self, "ssim", float(self.ssim))
        object.__setattr__(self, "psnr", normalized_psnr)


def build_boundary_metrics_command(
    previous_frame: str | os.PathLike[str],
    next_frame: str | os.PathLike[str],
    *,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build one ffmpeg invocation that reports both SSIM and PSNR."""

    previous = os.fspath(previous_frame)
    following = os.fspath(next_frame)
    if not previous or not following:
        raise ValueError("boundary frame paths must not be empty")
    if not ffmpeg_bin:
        raise ValueError("ffmpeg_bin must not be empty")
    filter_graph = (
        "[0:v:0]setpts=PTS-STARTPTS,format=yuv420p,"
        "split=2[previous_ssim][previous_psnr];"
        "[1:v:0]setpts=PTS-STARTPTS,format=yuv420p,"
        "split=2[next_ssim][next_psnr];"
        "[previous_ssim][next_ssim]ssim[ssim_out];"
        "[previous_psnr][next_psnr]psnr[psnr_out]"
    )
    return [
        ffmpeg_bin,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info",
        "-i",
        previous,
        "-i",
        following,
        "-filter_complex",
        filter_graph,
        "-map",
        "[ssim_out]",
        "-map",
        "[psnr_out]",
        "-frames:v",
        "1",
        "-f",
        "null",
        "-",
    ]


def parse_boundary_metrics(output: str | bytes) -> BoundaryMetrics:
    """Parse the summary emitted by ffmpeg's ssim and psnr filters."""

    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    if not isinstance(output, str):
        raise ValueError("ffmpeg metrics output must be text")
    ssim_matches = _SSIM_PATTERN.findall(output)
    psnr_matches = _PSNR_PATTERN.findall(output)
    if not ssim_matches:
        raise ValueError("ffmpeg output does not contain an SSIM All value")
    if not psnr_matches:
        raise ValueError("ffmpeg output does not contain a PSNR average value")
    return BoundaryMetrics(
        ssim=float(ssim_matches[-1]),
        psnr=float(psnr_matches[-1]),
    )


def _threshold(value: float, name: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{name} must be finite")
    return float(value)


def evaluate_boundary_metrics(
    ssim: float,
    psnr: float,
    *,
    boundary_kind: str = "segment",
    min_ssim: float = DEFAULT_SSIM_THRESHOLD,
    min_psnr: float = DEFAULT_PSNR_THRESHOLD,
) -> QualityReport:
    """Apply independently configurable hard thresholds to a boundary."""

    if boundary_kind not in {"segment", "continuation"}:
        raise ValueError("boundary_kind must be 'segment' or 'continuation'")
    metrics = BoundaryMetrics(ssim=ssim, psnr=psnr)
    min_ssim = _threshold(min_ssim, "min_ssim")
    min_psnr = _threshold(min_psnr, "min_psnr")
    if not 0.0 <= min_ssim <= 1.0:
        raise ValueError("min_ssim must be between 0 and 1")

    gate = f"boundary.{boundary_kind}"
    issues: list[QualityIssue] = []
    if metrics.ssim < min_ssim:
        issues.append(
            QualityIssue(
                code=f"{gate}.ssim_below_threshold",
                message="Boundary SSIM is below the required threshold",
                gate=gate,
                hard=True,
                details={"actual": metrics.ssim, "minimum": min_ssim},
            )
        )
    if metrics.psnr < min_psnr:
        issues.append(
            QualityIssue(
                code=f"{gate}.psnr_below_threshold",
                message="Boundary PSNR is below the required threshold",
                gate=gate,
                hard=True,
                details={"actual": metrics.psnr, "minimum": min_psnr},
            )
        )

    psnr_value: float | str = (
        "inf" if metrics.psnr == math.inf else metrics.psnr
    )
    return QualityReport(
        gate=gate,
        issues=tuple(issues),
        metrics={
            "ssim": metrics.ssim,
            "psnr": psnr_value,
            "min_ssim": min_ssim,
            "min_psnr": min_psnr,
        },
    )


def measure_boundary(
    previous_frame: str | os.PathLike[str],
    next_frame: str | os.PathLike[str],
    *,
    runner: CommandRunner | None = None,
    ffmpeg_bin: str = "ffmpeg",
    boundary_kind: str = "segment",
    min_ssim: float = DEFAULT_SSIM_THRESHOLD,
    min_psnr: float = DEFAULT_PSNR_THRESHOLD,
) -> QualityReport:
    """Run the injectable ffmpeg adapter, parse, then evaluate a boundary."""

    command = build_boundary_metrics_command(
        previous_frame,
        next_frame,
        ffmpeg_bin=ffmpeg_bin,
    )
    result = execute_command(command, runner=runner)
    parsed = parse_boundary_metrics(f"{result.stdout}\n{result.stderr}")
    return evaluate_boundary_metrics(
        parsed.ssim,
        parsed.psnr,
        boundary_kind=boundary_kind,
        min_ssim=min_ssim,
        min_psnr=min_psnr,
    )


build_ffmpeg_boundary_command = build_boundary_metrics_command
