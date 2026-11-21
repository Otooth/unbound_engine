"""Pure quality gates plus injectable ffmpeg/ffprobe adapters."""

from .boundary import (
    DEFAULT_PSNR_THRESHOLD,
    DEFAULT_SSIM_THRESHOLD,
    BoundaryMetrics,
    build_boundary_metrics_command,
    build_ffmpeg_boundary_command,
    evaluate_boundary_metrics,
    measure_boundary,
    parse_boundary_metrics,
)
from .commands import (
    CommandResult,
    CommandRunner,
    ExternalCommandError,
    default_command_runner,
    execute_command,
)
from .gates import (
    aggregate_quality_reports,
    aggregate_reports,
    should_retry,
    should_retry_hard_failure,
)
from .models import QualityIssue, QualityReport
from .probe import (
    build_ffprobe_command,
    probe_video,
    validate_probe_result,
    validate_technical_probe,
)
from .semantic import (
    SEMANTIC_CONFIDENCE_THRESHOLD,
    VISION_AUDIT_JSON_SCHEMA,
    VisionAuditSchemaError,
    evaluate_vision_audit,
    normalize_qwen_vision_result,
    normalize_vision_audit,
)
from .temporal import (
    DEFAULT_BLACK_DURATION,
    DEFAULT_FREEZE_DURATION,
    analyze_temporal_video,
    build_temporal_analysis_command,
    evaluate_temporal_findings,
    parse_temporal_findings,
)

__all__ = [
    "DEFAULT_BLACK_DURATION",
    "DEFAULT_FREEZE_DURATION",
    "DEFAULT_PSNR_THRESHOLD",
    "DEFAULT_SSIM_THRESHOLD",
    "SEMANTIC_CONFIDENCE_THRESHOLD",
    "VISION_AUDIT_JSON_SCHEMA",
    "BoundaryMetrics",
    "CommandResult",
    "CommandRunner",
    "ExternalCommandError",
    "QualityIssue",
    "QualityReport",
    "VisionAuditSchemaError",
    "aggregate_quality_reports",
    "aggregate_reports",
    "analyze_temporal_video",
    "build_boundary_metrics_command",
    "build_ffmpeg_boundary_command",
    "build_ffprobe_command",
    "build_temporal_analysis_command",
    "default_command_runner",
    "evaluate_boundary_metrics",
    "evaluate_temporal_findings",
    "evaluate_vision_audit",
    "execute_command",
    "measure_boundary",
    "normalize_qwen_vision_result",
    "normalize_vision_audit",
    "parse_boundary_metrics",
    "parse_temporal_findings",
    "probe_video",
    "should_retry",
    "should_retry_hard_failure",
    "validate_probe_result",
    "validate_technical_probe",
]
