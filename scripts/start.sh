#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

compose_args=()
ltx_enabled=0
if docker image inspect unbound-ltx-worker:1.6.2 >/dev/null 2>&1; then
    compose_args=(--profile ltx)
    ltx_enabled=1
else
    echo "LTX worker image is not built; starting the ComfyUI baseline only."
fi

docker compose "${compose_args[@]}" up -d

for _ in $(seq 1 60); do
    if curl -fs -o /dev/null http://127.0.0.1:8188/; then
        published="$(docker compose port comfyui 8188)"
        if [[ "$published" != 127.0.0.1:* ]]; then
            echo "Refusing non-loopback port binding: $published" >&2
            exit 1
        fi
        if (( ltx_enabled )); then
            network_mode="$(
                docker inspect unbound-ltx-worker \
                    --format '{{.HostConfig.NetworkMode}}'
            )"
            if [[ "$network_mode" != host ]]; then
                echo "LTX worker must use host networking: $network_mode" >&2
                exit 1
            fi
            ready=0
            for _ in $(seq 1 60); do
                if curl -fsS --max-time 5 \
                    http://127.0.0.1:8191/health >/dev/null; then
                    ready=1
                    break
                fi
                sleep 2
            done
            if (( ! ready )); then
                docker compose --profile ltx logs --no-color ltx >&2
                echo "LTX worker did not become ready within 120 seconds." >&2
                exit 1
            fi
            echo "LTX worker is ready at http://127.0.0.1:8191/"
            curl -fsS http://127.0.0.1:8191/health
            echo
        fi
        echo "ComfyUI is ready at http://127.0.0.1:8188/"
        docker compose "${compose_args[@]}" ps
        exit 0
    fi
    sleep 2
done

docker compose logs --no-color comfyui >&2
echo "ComfyUI did not become ready within 120 seconds." >&2
exit 1
