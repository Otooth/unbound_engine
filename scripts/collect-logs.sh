#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="logs/comfyui-${timestamp}.log"

{
    echo "Collected at: $timestamp"
    docker compose --profile ltx ps
    docker compose logs --no-color comfyui
    if [[ -n "$(docker compose --profile ltx ps -q ltx)" ]]; then
        docker compose --profile ltx logs --no-color ltx
    fi
} > "$destination"

echo "$destination"
