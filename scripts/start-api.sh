#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -x automation-env/bin/uvicorn ]]; then
    python3 -m venv automation-env
    automation-env/bin/pip install -r requirements-automation.txt
fi

if ! curl -fsS -o /dev/null http://127.0.0.1:8188/ 2>/dev/null; then
    if docker compose ps >/dev/null 2>&1; then
        scripts/start.sh
    else
        sg docker -c scripts/start.sh
    fi
fi

mkdir -p logs run
pid_file="run/automation-api.pid"
if [[ -f "$pid_file" ]]; then
    existing_pid="$(cat "$pid_file")"
    existing_command=""
    if [[ -r "/proc/$existing_pid/cmdline" ]]; then
        existing_command="$(tr '\0' ' ' < "/proc/$existing_pid/cmdline")"
    fi
    if [[ "$existing_command" == *"uvicorn automation.app:app"* ]]; then
        echo "Automation API is already running (PID $existing_pid)."
        exit 0
    fi
    rm -f "$pid_file"
fi

nohup automation-env/bin/uvicorn automation.app:app \
    --host 127.0.0.1 \
    --port 8001 \
    > logs/automation-api.log 2>&1 &
api_pid=$!
echo "$api_pid" > "$pid_file"

for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null http://127.0.0.1:8001/health 2>/dev/null; then
        echo "Automation API is ready at http://127.0.0.1:8001/"
        exit 0
    fi
    if ! kill -0 "$api_pid" 2>/dev/null; then
        cat logs/automation-api.log >&2
        rm -f "$pid_file"
        exit 1
    fi
    sleep 1
done

kill "$api_pid" 2>/dev/null || true
rm -f "$pid_file"
cat logs/automation-api.log >&2
echo "Automation API did not become ready within 60 seconds." >&2
exit 1
