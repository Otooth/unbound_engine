#!/usr/bin/env python3
"""Run one benign native LTX worker smoke job and record host UMA use."""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKER_URL = "http://127.0.0.1:8191"
DEFAULT_COMFY_URL = "http://127.0.0.1:8188"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.6:35b-a3b-q8_0"
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", "replace")
        raise RuntimeError(f"{url}: HTTP {error.code}: {detail}") from error
    if not raw:
        return {}
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError(f"{url}: expected a JSON object")
    return result


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "profile",
        choices=("ti2_hq", "dfr", "ti2_chunked"),
    )
    parser.add_argument("--width", type=int, default=256)
    parser.add_argument("--height", type=int, default=256)
    parser.add_argument("--frames", type=int, default=9)
    parser.add_argument("--fps", type=float, default=24)
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument(
        "--input",
        help="Image filename relative to the project's input directory",
    )
    parser.add_argument(
        "--prompt",
        default=(
            "A matte blue ball rolls slowly from left to right across a plain "
            "wooden table under soft daylight while the camera remains fixed."
        ),
    )
    parser.add_argument("--worker-url", default=DEFAULT_WORKER_URL)
    parser.add_argument("--timeout", type=float, default=12 * 60 * 60)
    args = parser.parse_args()

    worker_url = args.worker_url.rstrip("/")
    input_path: str | None = None
    if args.input:
        if Path(args.input).name != args.input:
            raise ValueError("--input must be a filename without directories")
        host_input = PROJECT_ROOT / "input" / args.input
        if not host_input.is_file():
            raise FileNotFoundError(host_input)
        input_path = args.input
    health = request_json(f"{worker_url}/health")
    readiness = health.get("profiles_ready", {})
    if not isinstance(readiness, dict) or not readiness.get(args.profile):
        raise RuntimeError(f"LTX profile is not ready: {args.profile}: {health}")

    # Enforce the one-model-at-a-time policy before the worker loads LTX.
    request_json(
        f"{DEFAULT_OLLAMA_URL}/api/generate",
        {"model": DEFAULT_OLLAMA_MODEL, "keep_alive": 0},
        timeout=120,
    )
    request_json(
        f"{DEFAULT_COMFY_URL}/free",
        {"unload_models": True, "free_memory": True},
        timeout=120,
    )

    started_at = datetime.now(timezone.utc)
    stamp = started_at.strftime("%Y%m%dT%H%M%SZ")
    output_directory_name = f"ltx-smoke-{args.profile}-{stamp}"
    host_output_directory = (
        PROJECT_ROOT / "output" / "jobs" / output_directory_name
    )
    host_output_directory.mkdir(parents=True, exist_ok=False)
    host_output_directory.chmod(0o777)
    output_path = f"jobs/{output_directory_name}/output.mp4"
    payload: dict[str, Any] = {
        "profile": args.profile,
        "input_path": input_path,
        "output_path": output_path,
        "prompt": args.prompt,
        "negative": (
            None
            if args.profile == "dfr"
            else (
                "scene cut, text, watermark, duplicate objects, "
                "deformed geometry"
            )
        ),
        "seed": args.seed,
        "width": args.width,
        "height": args.height,
        "frames": args.frames,
        "fps": args.fps,
    }

    stop_sampling = threading.Event()
    minimum_available = mem_available_bytes()

    def sample_memory() -> None:
        nonlocal minimum_available
        while not stop_sampling.wait(1):
            minimum_available = min(minimum_available, mem_available_bytes())

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    started = time.monotonic()
    job: dict[str, Any] = {}
    try:
        submitted = request_json(f"{worker_url}/generate", payload)
        job_id = submitted.get("job_id", submitted.get("id"))
        if not isinstance(job_id, str) or not job_id:
            raise RuntimeError(f"worker returned no job_id: {submitted}")
        print(f"LTX smoke job: {job_id}", flush=True)
        deadline = started + args.timeout
        while time.monotonic() < deadline:
            job = request_json(f"{worker_url}/jobs/{job_id}")
            status = job.get("status")
            if status in TERMINAL_STATES:
                break
            time.sleep(2)
        else:
            request_json(f"{worker_url}/jobs/{job_id}/cancel", {})
            raise TimeoutError(f"LTX smoke job timed out: {job_id}")
        if job.get("status") != "succeeded":
            raise RuntimeError(f"LTX smoke job failed: {job}")
    finally:
        stop_sampling.set()
        sampler.join(timeout=5)
        try:
            request_json(f"{worker_url}/release", {})
        except Exception as error:
            print(f"LTX release warning: {error}", flush=True)

    elapsed = time.monotonic() - started
    total_memory = int(
        next(
            line.split()[1]
            for line in Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemTotal:")
        )
    ) * 1024
    report = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_seconds": elapsed,
        "host_total_memory_bytes": total_memory,
        "minimum_mem_available_bytes": minimum_available,
        "peak_host_memory_used_bytes": total_memory - minimum_available,
        "request": payload,
        "worker_health": health,
        "job": job,
    }
    report_path = (
        PROJECT_ROOT
        / "logs"
        / "generations"
        / f"ltx-smoke-{args.profile}-{stamp}.json"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(report_path)


if __name__ == "__main__":
    main()
