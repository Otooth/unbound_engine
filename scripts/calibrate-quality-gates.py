#!/usr/bin/env python3
"""Create deterministic local fixtures and verify temporal gate thresholds."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from automation.app import (
    OUTPUT_ROOT,
    probe_output_video,
    temporal_quality_report,
)


FIXTURE_RELATIVE_ROOT = "quality-fixtures"
FIXTURE_ROOT = OUTPUT_ROOT / FIXTURE_RELATIVE_ROOT
REPORT_PATH = PROJECT_ROOT / "benchmarks" / "quality-gate-calibration.json"


def run_ffmpeg(name: str, source: str) -> str:
    relative = f"{FIXTURE_RELATIVE_ROOT}/{name}.mp4"
    subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-t",
            "5",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-y",
            f"/opt/ComfyUI/output/{relative}",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    return relative


def inspect(relative: str) -> dict[str, object]:
    probe = probe_output_video(relative)
    duration = float(probe["format"]["duration"])
    report = temporal_quality_report(relative, duration=duration)
    return {
        "path": relative,
        "duration": duration,
        "report": report.to_dict(),
        "passed": report.passed,
    }


def main() -> int:
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    FIXTURE_ROOT.chmod(0o777)
    fixtures = {
        "moving": run_ffmpeg(
            "moving",
            "testsrc2=size=640x360:rate=24",
        ),
        "frozen": run_ffmpeg(
            "frozen",
            "color=c=white:size=640x360:rate=24",
        ),
        "black": run_ffmpeg(
            "black",
            "color=c=black:size=640x360:rate=24",
        ),
    }
    results = {name: inspect(path) for name, path in fixtures.items()}
    expected = {
        "moving": True,
        "frozen": False,
        "black": False,
    }
    for name, should_pass in expected.items():
        if results[name]["passed"] is not should_pass:
            raise RuntimeError(
                f"{name} fixture expected passed={should_pass}, "
                f"got {results[name]['passed']}"
            )
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixtures_are_synthetic_general_video": True,
        "expected": expected,
        "results": results,
    }
    REPORT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
