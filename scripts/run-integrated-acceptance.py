#!/usr/bin/env python3
"""Exercise the complete local automation path and save an acceptance report."""

from __future__ import annotations

import argparse
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})


def mem_available_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemAvailable is missing")


def mem_total_bytes() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("MemTotal is missing")


def request_json(
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: float = 60,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        base_url + path,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise RuntimeError(f"{path} returned a non-object JSON value")
    return result


def generate(
    base_url: str,
    label: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    job_id = request_json(base_url, "/jobs", payload)["id"]
    print(f"{label} acceptance job: {job_id}", flush=True)
    deadline = time.monotonic() + 12 * 60 * 60
    while time.monotonic() < deadline:
        job = request_json(base_url, f"/jobs/{job_id}")
        status = job["status"]
        if status == "succeeded":
            assert job["output_url"], job
            assert job["quality"]["passed"], job["quality"]
            assert job["continuation"]["tail_video"], job["continuation"]
            return job
        if status in TERMINAL_STATES:
            raise RuntimeError(f"{label} failed: {job}")
        time.sleep(5)
    raise TimeoutError(f"{label} timed out: {job_id}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument(
        "--isolation",
        choices=("host_disconnected", "service_network_none"),
        required=True,
    )
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    health = request_json(base_url, "/health")
    if health.get("service") != "ok":
        raise RuntimeError(f"automation is unhealthy: {health}")

    stop_sampling = threading.Event()
    minimum_available = mem_available_bytes()

    def sample_memory() -> None:
        nonlocal minimum_available
        while not stop_sampling.wait(1):
            minimum_available = min(
                minimum_available,
                mem_available_bytes(),
            )

    sampler = threading.Thread(target=sample_memory, daemon=True)
    sampler.start()
    try:
        wan = generate(
            base_url,
            "ComfyUI Wan",
            {
                "concept": (
                    "Gentle ocean waves roll continuously toward a quiet "
                    "sandy shore under soft daylight while the camera "
                    "remains fixed."
                ),
                "engine": "wan5b",
                "shots": 1,
                "seed": 20261120,
                "enhance_prompt": True,
                "quality_mode": False,
                "generation_profile": "comfy",
            },
        )
        ltx = generate(
            base_url,
            "Native LTX DFR",
            {
                "concept": (
                    "A red paper kite rises steadily above a green field in "
                    "a light breeze while the wide camera remains fixed."
                ),
                "engine": "ltx25",
                "shots": 1,
                "seed": 20261120,
                "enhance_prompt": True,
                "quality_mode": False,
                "generation_profile": "dfr",
                "ltx_license_acknowledged": True,
            },
        )
        cancelled_id = request_json(
            base_url,
            "/jobs",
            {
                "concept": (
                    "A small wooden boat moves slowly across a calm pond "
                    "while the camera remains fixed."
                ),
                "engine": "wan5b",
                "shots": 1,
                "seed": 20261121,
            },
        )["id"]
        request_json(base_url, f"/jobs/{cancelled_id}/cancel", {})
        cancelled = request_json(base_url, f"/jobs/{cancelled_id}")
        assert cancelled["status"] == "cancelled", cancelled
    finally:
        stop_sampling.set()
        sampler.join(timeout=5)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "isolation": args.isolation,
        "host_total_memory_bytes": mem_total_bytes(),
        "minimum_mem_available_bytes": minimum_available,
        "peak_host_memory_used_bytes": (
            mem_total_bytes() - minimum_available
        ),
        "wan_job": wan,
        "ltx_job": ltx,
        "cancelled_job": cancelled,
        "health": health,
    }
    destination = (
        PROJECT_ROOT
        / "logs"
        / (
            "offline-acceptance-"
            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
    )
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(destination)


if __name__ == "__main__":
    main()
