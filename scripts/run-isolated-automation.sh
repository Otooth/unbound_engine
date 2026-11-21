#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET_ROOT="${OFFLINE_SOCKET_ROOT:-$PROJECT_ROOT/run/offline-sockets}"
relay="$PROJECT_ROOT/scripts/socket-relay.py"
pids=()
cd "$PROJECT_ROOT"

cleanup() {
    for pid in "${pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

for socket in ollama comfy ltx; do
    if [[ ! -S "$SOCKET_ROOT/$socket.sock" ]]; then
        echo "Missing offline service socket: $SOCKET_ROOT/$socket.sock" >&2
        exit 1
    fi
done

python3 "$relay" tcp-listen \
    --listen-port 11434 --path "$SOCKET_ROOT/ollama.sock" &
pids+=("$!")
python3 "$relay" tcp-listen \
    --listen-port 8188 --path "$SOCKET_ROOT/comfy.sock" &
pids+=("$!")
python3 "$relay" tcp-listen \
    --listen-port 8191 --path "$SOCKET_ROOT/ltx.sock" &
pids+=("$!")

for _ in $(seq 1 50); do
    if (
        exec 3<>/dev/tcp/127.0.0.1/11434
        exec 4<>/dev/tcp/127.0.0.1/8188
        exec 5<>/dev/tcp/127.0.0.1/8191
    ) 2>/dev/null; then
        break
    fi
    sleep 0.1
done

export OLLAMA_URL="http://127.0.0.1:11434"
export COMFY_URL="http://127.0.0.1:8188"
export LTX_WORKER_URL="http://127.0.0.1:8191"
export HF_HUB_OFFLINE=1
export HF_DATASETS_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export DO_NOT_TRACK=1

"$PROJECT_ROOT/automation-env/bin/uvicorn" automation.app:app \
    --host 127.0.0.1 --port 8001 &
api_pid="$!"
pids+=("$api_pid")
wait "$api_pid"
