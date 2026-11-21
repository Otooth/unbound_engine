#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

assert_external_offline() {
    if curl -fsS --max-time 3 https://huggingface.co/ >/dev/null 2>&1; then
        echo "External network is still reachable. Disconnect it before acceptance." >&2
        return 2
    fi
}

assert_external_offline

for url in \
    http://127.0.0.1:8001/health \
    http://127.0.0.1:8188/system_stats \
    http://127.0.0.1:8191/health \
    http://127.0.0.1:11434/api/version; do
    curl -fsS --max-time 10 "$url" >/dev/null
done

published="$(docker compose port comfyui 8188)"
if [[ "$published" != 127.0.0.1:* ]]; then
    echo "ComfyUI is not loopback-only: $published" >&2
    exit 1
fi

network_mode="$(
    docker inspect unbound-ltx-worker --format '{{.HostConfig.NetworkMode}}'
)"
if [[ "$network_mode" != host ]]; then
    echo "LTX worker is not in the reviewed host-network mode." >&2
    exit 1
fi

automation-env/bin/python - <<'PY'
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "http://127.0.0.1:8001"
PROJECT_ROOT = Path.cwd()
stop_sampling = threading.Event()
minimum_available_kib = 2**63 - 1


def mem_available_kib() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1])
    raise RuntimeError("MemAvailable is missing")


def sample_memory() -> None:
    global minimum_available_kib
    while not stop_sampling.wait(1):
        minimum_available_kib = min(
            minimum_available_kib,
            mem_available_kib(),
        )


def request_json(path: str, payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        BASE + path,
        data=body,
        headers={"Content-Type": "application/json"} if body else {},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def generate(label: str, payload: dict) -> dict:
    job_id = request_json("/jobs", payload)["id"]
    print(f"{label} offline acceptance job: {job_id}", flush=True)
    deadline = time.monotonic() + 12 * 60 * 60
    while time.monotonic() < deadline:
        job = request_json(f"/jobs/{job_id}")
        if job["status"] == "succeeded":
            assert job["output_url"], job
            assert job["quality"]["passed"], job["quality"]
            assert job["continuation"]["tail_video"], job["continuation"]
            print(json.dumps(job, ensure_ascii=False, indent=2), flush=True)
            return job
        if job["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"{label} failed: {job}")
        time.sleep(5)
    raise TimeoutError(f"{label} timed out: {job_id}")


sampler = threading.Thread(target=sample_memory, daemon=True)
minimum_available_kib = mem_available_kib()
sampler.start()
try:
    wan = generate(
        "ComfyUI Wan",
        {
            "concept": (
                "Gentle ocean waves roll continuously toward a quiet sandy "
                "shore under soft daylight while the camera remains fixed."
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
        "Native LTX DFR",
        {
            "concept": (
                "A red paper kite rises steadily above a green field in a "
                "light breeze while the wide camera remains fixed."
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
        "/jobs",
        {
            "concept": (
                "A small wooden boat moves slowly across a calm pond while "
                "the camera remains fixed."
            ),
            "engine": "wan5b",
            "shots": 1,
            "seed": 20261121,
        },
    )["id"]
    request_json(f"/jobs/{cancelled_id}/cancel", {})
    cancelled = request_json(f"/jobs/{cancelled_id}")
    assert cancelled["status"] == "cancelled", cancelled
finally:
    stop_sampling.set()
    sampler.join(timeout=5)

report = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "network_disconnected": True,
    "minimum_mem_available_bytes": minimum_available_kib * 1024,
    "wan_job": wan,
    "ltx_job": ltx,
    "cancelled_job": cancelled,
}
destination = (
    PROJECT_ROOT
    / "logs"
    / f"offline-acceptance-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
)
destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(destination)
PY

assert_external_offline
free -g
echo "Full offline acceptance succeeded for ComfyUI and native LTX."
