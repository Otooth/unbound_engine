#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

temporary="$(mktemp -d)"
cleanup() {
    rm -rf "$temporary"
}
trap cleanup EXIT

docker build \
    --file Dockerfile.ltx \
    --target lock-export \
    --output "type=local,dest=$temporary" \
    .

if [[ ! -s "$temporary/uv.lock" ]]; then
    echo "LTX lock export did not produce uv.lock" >&2
    exit 1
fi

install -m 0644 "$temporary/uv.lock" workers/ltx/uv.lock
sha256sum workers/ltx/uv.lock
