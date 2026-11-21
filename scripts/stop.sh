#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

if curl -fsS -o /dev/null http://127.0.0.1:8188/; then
    curl -fsS \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{"unload_models":true,"free_memory":true}' \
        http://127.0.0.1:8188/free
fi

if curl -fsS -o /dev/null http://127.0.0.1:8191/; then
    curl -fsS \
        -X POST \
        -H "Content-Type: application/json" \
        -d '{}' \
        http://127.0.0.1:8191/release || true
fi

docker compose --profile ltx stop
free -g
