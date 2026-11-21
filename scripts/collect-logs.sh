#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="logs/comfyui-${timestamp}.log"

{
    echo "Collected at: $timestamp"
    docker compose ps
    docker compose logs --no-color comfyui
} > "$destination"

echo "$destination"
