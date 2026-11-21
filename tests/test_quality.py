from __future__ import annotations

import json
import math
import unittest

from automation.quality import (
    CommandResult,
    QualityIssue,
    QualityReport,
    VisionAuditSchemaError,
    aggregate_reports,
    analyze_temporal_video,
    evaluate_boundary_metrics,
    evaluate_vision_audit,
    measure_boundary,
    normalize_vision_audit,
    parse_boundary_metrics,
    parse_temporal_findings,
    probe_video,
    should_retry,
    validate_probe_result,
)


def valid_probe() -> dict[str, object]:
    return {
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": "5.000000",
        },
        "streams": [
            {
                "codec_type": "audio",
                "duration": "5.000000",
            },
            {
                "codec_type": "video",
                "duration": "5.000000",
                "avg_frame_rate": "24000/1000",
                "r_frame_rate": "24/1",
                "width": 1280,
                "height": 704,
            },
        ],
    }


def valid_audit(**overrides: object) -> dict[str, object]:
    audit: dict[str, object] = {
        "anatomy_valid": True,
        "front_back_fusion_absent": True,
        "orientation_consistent": True,
        "identity_consistent": "not_applicable",
        "instruction_followed": True,
        "confidence": 0.9,
        "evidence": ["Body orientation remains coherent."],
        "corrective_prompt": "",
    }
    audit.update(overrides)
    return audit


class QualityModelTests(unittest.TestCase):
    def test_report_json_round_trip(self) -> None:
        original = QualityReport(
            gate="semantic",
            issues=(
                QualityIssue(
                    code="semantic.orientation_consistent",
                    message="Orientation changed",
                    gate="semantic",
                    hard=False,
                    details={"confidence": 0.6, "frames": [1, 2]},
                ),
            ),
            metrics={"confidence": 0.6},
        )

        restored = QualityReport.from_json(original.to_json())

        self.assertEqual(restored, original)
        self.assertTrue(restored.passed)
        self.assertEqual(len(restored.warnings), 1)


class ProbeGateTests(unittest.TestCase):
    def test_valid_mp4_probe_passes_expected_properties(self) -> None:
        report = validate_probe_result(
            valid_probe(),
            expected_duration=5.0,
            expected_fps=24.0,
            expected_resolution=(1280, 704),
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.issues, ())
        self.assertEqual(report.metrics["fps"], 24.0)

    def test_invalid_probe_reports_each_technical_failure(self) -> None:
        report = validate_probe_result(
            {
                "format": {
                    "format_name": "matroska,webm",
                    "duration": "N/A",
                },
                "streams": [
                    {
                        "codec_type": "video",
                        "avg_frame_rate": "0/0",
                        "width": 0,
                        "height": 704,
                    }
                ],
            }
        )

        self.assertFalse(report.passed)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {
                "technical.container_not_mp4",
                "technical.duration_invalid",
                "technical.fps_invalid",
                "technical.resolution_invalid",
            },
        )

    def test_probe_runner_is_injected_without_a_video_file(self) -> None:
        commands: list[tuple[str, ...]] = []

        def runner(command: object) -> CommandResult:
            commands.append(tuple(command))  # type: ignore[arg-type]
            return CommandResult(0, json.dumps(valid_probe()), "")

        report = probe_video(
            "/does/not/exist.mp4",
            runner=runner,
            ffprobe_bin="/fake/ffprobe",
        )

        self.assertTrue(report.passed)
        self.assertEqual(commands[0][0], "/fake/ffprobe")
        self.assertEqual(commands[0][-1], "/does/not/exist.mp4")
        self.assertIn("-show_streams", commands[0])


class SemanticGateTests(unittest.TestCase):
    def test_strict_normalization_preserves_not_applicable_identity(self) -> None:
        normalized = normalize_vision_audit(
            json.dumps(valid_audit(), ensure_ascii=False)
        )

        self.assertEqual(
            normalized["identity_consistent"], "not_applicable"
        )
        self.assertEqual(normalized["confidence"], 0.9)

    def test_strict_schema_rejects_coercion_and_extra_keys(self) -> None:
        with self.assertRaises(VisionAuditSchemaError):
            normalize_vision_audit(valid_audit(anatomy_valid="true"))
        with self.assertRaises(VisionAuditSchemaError):
            normalize_vision_audit(valid_audit(unexpected=True))

    def test_only_high_confidence_semantic_failure_is_hard(self) -> None:
        high = evaluate_vision_audit(
            valid_audit(
                orientation_consistent=False,
                confidence=0.75,
                corrective_prompt="Keep the torso facing one direction.",
            )
        )
        low = evaluate_vision_audit(
            valid_audit(
                orientation_consistent=False,
                confidence=0.74,
                corrective_prompt="Keep the torso facing one direction.",
            )
        )

        self.assertTrue(high.hard_failed)
        self.assertFalse(low.hard_failed)
        self.assertTrue(low.passed)
        self.assertTrue(all(not issue.hard for issue in low.issues))


class TemporalGateTests(unittest.TestCase):
    FFMPEG_OUTPUT = """
[blackdetect @ 0x1] black_start:0 black_end:0.72 black_duration:0.72
[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 1.25
[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 2.5
[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 3.75
"""

    def test_black_and_freeze_intervals_are_hard_failures(self) -> None:
        findings = parse_temporal_findings(
            self.FFMPEG_OUTPUT,
            video_duration=5.0,
        )
        report = analyze_temporal_video(
            "/does/not/exist.mp4",
            video_duration=5.0,
            runner=lambda command: CommandResult(
                0, "", self.FFMPEG_OUTPUT
            ),
        )

        self.assertEqual(findings["black_intervals"][0]["duration"], 0.72)
        self.assertEqual(findings["freeze_intervals"][0]["duration"], 2.5)
        self.assertTrue(report.hard_failed)
        self.assertEqual(
            {issue.code for issue in report.issues},
            {"temporal.black_frames", "temporal.frozen_video"},
        )

    def test_open_freeze_interval_uses_video_duration(self) -> None:
        findings = parse_temporal_findings(
            "lavfi.freezedetect.freeze_start: 3.0",
            video_duration=5.5,
        )

        self.assertEqual(
            findings["freeze_intervals"],
            [{"start": 3.0, "end": 5.5, "duration": 2.5}],
        )


class BoundaryGateTests(unittest.TestCase):
    FFMPEG_OUTPUT = """
[Parsed_ssim_4 @ 0x1] SSIM Y:0.991 U:0.992 V:0.993 All:0.991411 (20.65)
[Parsed_psnr_5 @ 0x2] PSNR y:40.1 u:41.2 v:42.3 average:40.540629 min:39 max:42
"""

    def test_parse_and_threshold_segment_and_continuation(self) -> None:
        parsed = parse_boundary_metrics(self.FFMPEG_OUTPUT)
        passing = evaluate_boundary_metrics(
            parsed.ssim,
            parsed.psnr,
            boundary_kind="segment",
            min_ssim=0.98,
            min_psnr=35.0,
        )
        failing = evaluate_boundary_metrics(
            0.90,
            25.0,
            boundary_kind="continuation",
            min_ssim=0.95,
            min_psnr=30.0,
        )

        self.assertTrue(passing.passed)
        self.assertFalse(failing.passed)
        self.assertEqual(len(failing.issues), 2)
        self.assertTrue(
            all(
                issue.gate == "boundary.continuation"
                for issue in failing.issues
            )
        )

    def test_ffmpeg_runner_is_injected_without_frame_files(self) -> None:
        commands: list[tuple[str, ...]] = []

        def runner(command: object) -> CommandResult:
            commands.append(tuple(command))  # type: ignore[arg-type]
            return CommandResult(0, "", self.FFMPEG_OUTPUT)

        report = measure_boundary(
            "/missing/last.png",
            "/missing/first.png",
            runner=runner,
            ffmpeg_bin="/fake/ffmpeg",
        )

        self.assertTrue(report.passed)
        self.assertEqual(commands[0][0], "/fake/ffmpeg")
        self.assertIn("-filter_complex", commands[0])

    def test_infinite_psnr_is_valid_and_json_serializable(self) -> None:
        report = evaluate_boundary_metrics(1.0, math.inf)

        self.assertTrue(report.passed)
        self.assertEqual(report.metrics["psnr"], "inf")
        json.loads(report.to_json())


class AggregateAndRetryTests(unittest.TestCase):
    def test_aggregate_and_single_retry_policy(self) -> None:
        technical = QualityReport(gate="technical")
        semantic = evaluate_vision_audit(
            valid_audit(
                instruction_followed=False,
                confidence=0.95,
                corrective_prompt="Perform the requested action.",
            )
        )

        aggregate = aggregate_reports([technical, semantic])

        self.assertTrue(aggregate.hard_failed)
        self.assertTrue(should_retry(aggregate, retries_used=0))
        self.assertFalse(should_retry(aggregate, retries_used=1))
        self.assertFalse(should_retry(technical, retries_used=0))


if __name__ == "__main__":
    unittest.main()
