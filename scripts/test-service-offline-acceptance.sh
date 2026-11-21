#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

SOCKET_ROOT="$PROJECT_ROOT/run/offline-sockets"
OFFLINE_COMPOSE=(
    docker compose
    -f docker-compose.yml
    -f docker-compose.offline.yml
    --profile ltx
)
AUTOMATION_CONTAINER="unbound-automation-offline"
OLLAMA_CONTAINER="unbound-ollama-offline"
api_was_running=0
cleanup_started=0

stop_normal_api() {
    local pid_file="run/automation-api.pid"
    if [[ ! -f "$pid_file" ]]; then
        return
    fi
    local pid
    pid="$(cat "$pid_file")"
    if kill -0 "$pid" 2>/dev/null; then
        api_was_running=1
        kill "$pid"
        for _ in $(seq 1 60); do
            if ! kill -0 "$pid" 2>/dev/null; then
                break
            fi
            sleep 0.5
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "Normal automation API did not stop." >&2
            return 1
        fi
    fi
    rm -f "$pid_file"
}

restore_normal_services() {
    local original_status=$?
    if (( cleanup_started )); then
        return
    fi
    cleanup_started=1
    set +e
    docker rm -f "$AUTOMATION_CONTAINER" "$OLLAMA_CONTAINER" \
        >/dev/null 2>&1
    "${OFFLINE_COMPOSE[@]}" down --remove-orphans >/dev/null 2>&1
    rm -f "$SOCKET_ROOT"/*.sock
    docker compose --profile ltx up -d --force-recreate
    scripts/start.sh
    if (( api_was_running )); then
        scripts/start-api.sh
    fi
    set -e
    return "$original_status"
}
trap restore_normal_services EXIT INT TERM

wait_in_container() {
    local container="$1"
    local url="$2"
    local python="$3"
    for _ in $(seq 1 120); do
        if docker exec "$container" "$python" -c \
            "from urllib.request import urlopen; urlopen('$url', timeout=3)" \
            >/dev/null 2>&1; then
            return
        fi
        sleep 1
    done
    echo "$container did not become ready: $url" >&2
    docker logs "$container" >&2 || true
    return 1
}

assert_container_offline() {
    local container="$1"
    local python="$2"
    docker exec -i "$container" "$python" - <<'PY'
import socket

try:
    socket.create_connection(("1.1.1.1", 443), timeout=2).close()
except OSError as error:
    print(f"external network denied as expected: {type(error).__name__}")
else:
    raise SystemExit("external network is unexpectedly reachable")
PY
}

normal_health="$(curl -fsS http://127.0.0.1:8001/health || true)"
if [[ -n "$normal_health" ]]; then
    active_jobs="$(
        automation-env/bin/python - <<'PY'
import json
from urllib.request import urlopen

with urlopen("http://127.0.0.1:8001/jobs", timeout=10) as response:
    jobs = json.load(response)
print(sum(job["status"] in {"queued", "planning", "generating", "validating",
                           "analyzing", "quality_retry", "finishing",
                           "continuing", "extending", "motion"}
          for job in jobs))
PY
    )"
    if [[ "$active_jobs" != 0 ]]; then
        echo "Refusing offline acceptance while jobs are active." >&2
        exit 1
    fi
fi

stop_normal_api
mkdir -p "$SOCKET_ROOT"
chmod 0777 "$SOCKET_ROOT"
rm -f "$SOCKET_ROOT"/*.sock

"${OFFLINE_COMPOSE[@]}" up -d --force-recreate comfyui ltx
wait_in_container \
    unbound-comfyui http://127.0.0.1:8188/ python
wait_in_container \
    unbound-ltx-worker http://127.0.0.1:8191/health \
    /opt/ltx/.venv/bin/python

"${OFFLINE_COMPOSE[@]}" exec -d comfyui \
    python /opt/unbound/socket-relay.py unix-listen \
    --path /offline/comfy.sock --target-port 8188
"${OFFLINE_COMPOSE[@]}" exec -d ltx \
    /opt/ltx/.venv/bin/python /opt/unbound/socket-relay.py unix-listen \
    --path /offline/ltx.sock --target-port 8191

docker run -d \
    --name "$OLLAMA_CONTAINER" \
    --network none \
    --gpus all \
    --init \
    --user 0:0 \
    -e HOME=/root \
    -e OLLAMA_HOST=127.0.0.1:11434 \
    -e OLLAMA_MODELS=/root/.ollama/models \
    -e OLLAMA_KEEP_ALIVE=0 \
    -e DO_NOT_TRACK=1 \
    -v /usr/local/bin/ollama:/usr/local/bin/ollama:ro \
    -v /usr/local/lib/ollama:/usr/local/lib/ollama:ro \
    -v /usr/share/ollama/.ollama:/root/.ollama:ro \
    -v "$PROJECT_ROOT/scripts/socket-relay.py:/opt/unbound/socket-relay.py:ro" \
    -v "$SOCKET_ROOT:/offline" \
    --entrypoint /usr/local/bin/ollama \
    unbound-ltx-worker:1.6.2 serve >/dev/null
wait_in_container \
    "$OLLAMA_CONTAINER" http://127.0.0.1:11434/api/version \
    /opt/ltx/.venv/bin/python
docker exec -d "$OLLAMA_CONTAINER" \
    /opt/ltx/.venv/bin/python /opt/unbound/socket-relay.py unix-listen \
    --path /offline/ollama.sock --target-port 11434

for _ in $(seq 1 100); do
    if [[ -S "$SOCKET_ROOT/comfy.sock" \
        && -S "$SOCKET_ROOT/ltx.sock" \
        && -S "$SOCKET_ROOT/ollama.sock" ]]; then
        break
    fi
    sleep 0.1
done
for socket in comfy ltx ollama; do
    [[ -S "$SOCKET_ROOT/$socket.sock" ]] || {
        echo "Missing relay socket: $socket" >&2
        exit 1
    }
done

docker_socket_gid="$(stat -c '%g' /var/run/docker.sock)"
docker run -d \
    --name "$AUTOMATION_CONTAINER" \
    --network none \
    --init \
    --user 0:0 \
    -e HOME=/home/ubuntu \
    -e OFFLINE_SOCKET_ROOT="$SOCKET_ROOT" \
    -e PYTHONUNBUFFERED=1 \
    -v "$PROJECT_ROOT:$PROJECT_ROOT" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v /usr/bin/docker:/usr/bin/docker:ro \
    -v /usr/libexec/docker/cli-plugins/docker-compose:/usr/libexec/docker/cli-plugins/docker-compose:ro \
    --entrypoint sh \
    unbound-comfyui:0.45.0 \
    -c "groupadd -f -g '$docker_socket_gid' docker; exec setpriv \
        --reuid=1000 --regid=1000 --groups='$docker_socket_gid' \
        bash '$PROJECT_ROOT/scripts/run-isolated-automation.sh'" >/dev/null
wait_in_container \
    "$AUTOMATION_CONTAINER" http://127.0.0.1:8001/health \
    "$PROJECT_ROOT/automation-env/bin/python"

for service in \
    "unbound-comfyui:python" \
    "unbound-ltx-worker:/opt/ltx/.venv/bin/python" \
    "$OLLAMA_CONTAINER:/opt/ltx/.venv/bin/python" \
    "$AUTOMATION_CONTAINER:$PROJECT_ROOT/automation-env/bin/python"; do
    assert_container_offline "${service%%:*}" "${service#*:}"
done

for container in \
    unbound-comfyui \
    unbound-ltx-worker \
    "$OLLAMA_CONTAINER" \
    "$AUTOMATION_CONTAINER"; do
    mode="$(docker inspect "$container" --format '{{.HostConfig.NetworkMode}}')"
    if [[ "$mode" != none ]]; then
        echo "$container is not network-isolated: $mode" >&2
        exit 1
    fi
done

docker exec -i --user 1000:1000 "$AUTOMATION_CONTAINER" \
    "$PROJECT_ROOT/automation-env/bin/python" \
    "$PROJECT_ROOT/scripts/run-integrated-acceptance.py" \
    --isolation service_network_none

docker exec -i --user 1000:1000 "$AUTOMATION_CONTAINER" \
    "$PROJECT_ROOT/automation-env/bin/python" - <<'PY'
import json
from urllib.request import urlopen

with urlopen("http://127.0.0.1:11434/api/ps", timeout=10) as response:
    assert json.load(response)["models"] == []
with urlopen("http://127.0.0.1:8191/health", timeout=10) as response:
    assert json.load(response)["active_job_id"] is None
print("All isolated models released.")
PY

echo "Service-isolated offline acceptance succeeded; management LAN stayed up."
