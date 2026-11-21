#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

docker compose up -d

for _ in $(seq 1 60); do
    if curl -fs -o /dev/null http://127.0.0.1:8188/; then
        published="$(docker compose port comfyui 8188)"
        if [[ "$published" != 127.0.0.1:* ]]; then
            echo "Refusing non-loopback port binding: $published" >&2
            exit 1
        fi
        echo "ComfyUI is ready at http://127.0.0.1:8188/"
        docker compose ps
        exit 0
    fi
    sleep 2
done

docker compose logs --no-color comfyui >&2
echo "ComfyUI did not become ready within 120 seconds." >&2
exit 1
