#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

pid_file="run/automation-api.pid"
if [[ -f "$pid_file" ]]; then
    api_pid="$(cat "$pid_file")"
    command=""
    if [[ -r "/proc/$api_pid/cmdline" ]]; then
        command="$(tr '\0' ' ' < "/proc/$api_pid/cmdline")"
    fi
    if [[ "$command" == *"uvicorn automation.app:app"* ]]; then
        kill "$api_pid"
        for _ in $(seq 1 30); do
            if ! kill -0 "$api_pid" 2>/dev/null; then
                break
            fi
            sleep 1
        done
    fi
    rm -f "$pid_file"
fi

if docker compose ps >/dev/null 2>&1; then
    scripts/stop.sh
else
    sg docker -c scripts/stop.sh
fi
