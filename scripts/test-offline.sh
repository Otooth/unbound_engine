#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

container_id="$(docker compose ps -q comfyui)"
if [[ -z "$container_id" ]]; then
    echo "ComfyUI is not running. Run scripts/start.sh first." >&2
    exit 1
fi

network="$(
    docker inspect "$container_id" \
        --format '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{"\n"}}{{end}}' \
        | awk 'NF { print; exit }'
)"
if [[ -z "$network" ]]; then
    echo "ComfyUI is already disconnected from Docker networks." >&2
    exit 1
fi

reconnect() {
    if ! docker inspect "$container_id" \
        --format '{{json .NetworkSettings.Networks}}' | grep -q "\"$network\""; then
        docker network connect "$network" "$container_id"
    fi
}
trap reconnect EXIT

docker network disconnect "$network" "$container_id"

docker exec -i "$container_id" python - <<'PY'
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen

try:
    urlopen("https://huggingface.co", timeout=3)
except Exception as error:
    print(f"External network is unavailable as expected: {type(error).__name__}")
else:
    raise SystemExit("External network is unexpectedly reachable")

base = "http://127.0.0.1:8188"
workflow_path = Path(
    "/opt/ComfyUI/user/default/workflows/video_wan2_2_5B_ti2v.api.json"
)
workflow = json.loads(workflow_path.read_text())
request = Request(
    f"{base}/prompt",
    data=json.dumps({"prompt": workflow}).encode(),
    headers={"Content-Type": "application/json"},
)
start = time.monotonic()
with urlopen(request, timeout=30) as response:
    prompt_id = json.load(response)["prompt_id"]
print(f"Queued offline prompt {prompt_id}", flush=True)

while True:
    with urlopen(f"{base}/history/{prompt_id}", timeout=30) as response:
        history = json.load(response).get(prompt_id)
    if history and history.get("status", {}).get("completed"):
        status = history["status"]
        result = {
            "prompt_id": prompt_id,
            "elapsed_seconds": time.monotonic() - start,
            "status": status,
            "outputs": history.get("outputs", {}),
        }
        print(json.dumps(result, indent=2), flush=True)
        if status.get("status_str") != "success":
            raise SystemExit(1)
        break
    time.sleep(2)
PY

echo "Offline generation succeeded; Docker network will now be reconnected."
