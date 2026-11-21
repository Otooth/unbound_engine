#!/usr/bin/env python3
"""Queue an API workflow, or monitor a queued prompt, and record metrics."""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = (
    PROJECT_ROOT / "workflows" / "video_wan2_2_5B_ti2v.api.json"
)


def request_json(
    url: str, payload: dict[str, object] | None = None
) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if data is None else "POST",
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def host_memory() -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        values[name] = int(value.strip().split()[0]) * 1024
    return values["MemTotal"], values["MemAvailable"]


def monitor_memory(
    base_url: str,
    start: float,
    stop: threading.Event,
    samples: list[dict[str, int | float]],
) -> None:
    while not stop.wait(1):
        total, available = host_memory()
        sample: dict[str, int | float] = {
            "elapsed_seconds": time.monotonic() - start,
            "host_total_bytes": total,
            "host_available_bytes": available,
            "host_used_bytes": total - available,
        }
        try:
            stats = request_json(f"{base_url}/system_stats")
            devices = stats.get("devices", [])
            if isinstance(devices, list) and devices:
                device = devices[0]
                if isinstance(device, dict):
                    sample["comfy_vram_free_bytes"] = int(
                        device.get("vram_free", 0)
                    )
        except Exception:
            pass
        samples.append(sample)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument(
        "--prompt-id",
        help="Monitor an already queued prompt instead of submitting API JSON",
    )
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    workflow_path = args.workflow.resolve()

    queued_at = datetime.now(timezone.utc)
    start = time.monotonic()
    if args.prompt_id:
        prompt_id = args.prompt_id
        print(f"Monitoring prompt {prompt_id}")
    else:
        workflow = json.loads(workflow_path.read_text())
        response = request_json(f"{args.url}/prompt", {"prompt": workflow})
        prompt_id = str(response["prompt_id"])
        print(f"Queued prompt {prompt_id}")

    samples: list[dict[str, int | float]] = []
    stop = threading.Event()
    monitor = threading.Thread(
        target=monitor_memory,
        args=(args.url, start, stop, samples),
        daemon=True,
    )
    monitor.start()

    history: dict[str, object] | None = None
    try:
        while time.monotonic() - start < args.timeout:
            all_history = request_json(f"{args.url}/history/{prompt_id}")
            candidate = all_history.get(prompt_id)
            if isinstance(candidate, dict):
                status = candidate.get("status", {})
                if isinstance(status, dict):
                    status_str = status.get("status_str")
                    if status.get("completed") or status_str in {
                        "success",
                        "error",
                    }:
                        history = candidate
                        break
            time.sleep(2)
        else:
            raise TimeoutError(f"Generation exceeded {args.timeout} seconds")
    finally:
        stop.set()
        monitor.join(timeout=5)

    elapsed = time.monotonic() - start
    assert history is not None
    status = history.get("status", {})
    if isinstance(status, dict) and status.get("status_str") != "success":
        raise RuntimeError(f"ComfyUI generation failed: {status}")

    min_available = min(
        (int(sample["host_available_bytes"]) for sample in samples),
        default=0,
    )
    peak_used = max(
        (int(sample["host_used_bytes"]) for sample in samples),
        default=0,
    )
    min_comfy_vram_free = min(
        (
            int(sample["comfy_vram_free_bytes"])
            for sample in samples
            if "comfy_vram_free_bytes" in sample
        ),
        default=0,
    )
    result = {
        "prompt_id": prompt_id,
        "queued_at": queued_at.isoformat(),
        "elapsed_seconds": elapsed,
        "workflow": str(workflow_path.relative_to(PROJECT_ROOT)),
        "host_min_available_bytes": min_available,
        "host_peak_used_bytes": peak_used,
        "comfy_min_vram_free_bytes": min_comfy_vram_free,
        "outputs": history.get("outputs", {}),
        "status": status,
    }

    log_dir = PROJECT_ROOT / "logs" / "generations"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{queued_at:%Y%m%dT%H%M%SZ}-{prompt_id}.json"
    log_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"Metrics written to {log_path}")


if __name__ == "__main__":
    main()
