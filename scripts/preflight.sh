#!/usr/bin/env bash
set -euo pipefail

echo "== Architecture =="
uname -m

echo "== Operating system =="
cat /etc/os-release

echo "== NVIDIA GPU =="
nvidia-smi

echo "== Docker =="
docker --version
docker compose version

echo "== Container GPU access =="
docker run --rm --gpus all \
    nvidia/cuda:13.0.0-base-ubuntu24.04 \
    nvidia-smi

echo "== Storage =="
df -h /

echo "== Memory =="
free -g
