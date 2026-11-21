#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <input-relative-to-output> <output-relative-to-output>" >&2
    exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="$PROJECT_ROOT/output"
INPUT_REL="$1"
OUTPUT_REL="$2"

INPUT_ABS="$(realpath -m "$OUTPUT_ROOT/$INPUT_REL")"
OUTPUT_ABS="$(realpath -m "$OUTPUT_ROOT/$OUTPUT_REL")"

for path in "$INPUT_ABS" "$OUTPUT_ABS"; do
    if [[ "$path" != "$OUTPUT_ROOT/"* ]]; then
        echo "Path must remain under $OUTPUT_ROOT: $path" >&2
        exit 2
    fi
done

if [[ ! -f "$INPUT_ABS" ]]; then
    echo "Input does not exist: $INPUT_ABS" >&2
    exit 1
fi
if [[ -e "$OUTPUT_ABS" ]]; then
    echo "Refusing to overwrite: $OUTPUT_ABS" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_ABS")"
cd "$PROJECT_ROOT"

CONTAINER_INPUT="/opt/ComfyUI/output/$INPUT_REL"
CONTAINER_OUTPUT="/opt/ComfyUI/output/$OUTPUT_REL"

if docker compose ps --status running --services | grep -qx comfyui; then
    docker compose exec -T comfyui \
        ffmpeg -hide_banner -loglevel error \
        -i "$CONTAINER_INPUT" -map_metadata -1 -c copy "$CONTAINER_OUTPUT"
    PROBE=(docker compose exec -T comfyui ffprobe)
else
    docker compose run --rm --no-deps --entrypoint ffmpeg comfyui \
        -hide_banner -loglevel error \
        -i "$CONTAINER_INPUT" -map_metadata -1 -c copy "$CONTAINER_OUTPUT"
    PROBE=(docker compose run --rm --no-deps --entrypoint ffprobe comfyui)
fi

metadata="$("${PROBE[@]}" -v error -show_entries format_tags -of json "$CONTAINER_OUTPUT")"
if [[ "$metadata" == *'"prompt"'* || "$metadata" == *'"workflow"'* ]]; then
    echo "Sensitive metadata remains in $OUTPUT_ABS" >&2
    exit 1
fi

echo "Metadata removed: $OUTPUT_ABS"
printf '%s\n' "$metadata"
