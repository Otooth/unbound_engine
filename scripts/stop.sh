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

docker compose stop
free -g
