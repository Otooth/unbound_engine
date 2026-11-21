#!/usr/bin/env python3
"""Create reproducible Wan 14B/LTX-2.5 workflows with matched settings."""

from __future__ import annotations

import copy
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / "workflows"
PROMPT = (
    "The white dragon warrior stands still, eyes full of determination and "
    "strength. The camera slowly moves closer or circles around the warrior, "
    "highlighting the powerful presence and heroic spirit of the character."
)
INPUT_IMAGE = "video_wan2_2_14B_i2v_input_image.jpg"
SEED = 264244520398999


def read_workflow(name: str) -> dict[str, object]:
    return json.loads((WORKFLOWS / name).read_text())


def node(workflow: dict[str, object], node_id: int) -> dict[str, object]:
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("Workflow has no nodes list")
    match = next(
        (
            item
            for item in nodes
            if isinstance(item, dict) and item.get("id") == node_id
        ),
        None,
    )
    if match is None:
        raise ValueError(f"Workflow is missing node {node_id}")
    return match


def widgets(workflow: dict[str, object], node_id: int) -> list[object]:
    values = node(workflow, node_id).get("widgets_values")
    if not isinstance(values, list):
        raise ValueError(f"Node {node_id} has no widgets_values list")
    return values


def write_workflow(name: str, workflow: dict[str, object]) -> None:
    path = WORKFLOWS / name
    path.write_text(json.dumps(workflow, indent=2, ensure_ascii=False) + "\n")
    print(path)


def main() -> None:
    wan = copy.deepcopy(read_workflow("video_wan2_2_14B_i2v.json"))
    wan_values = widgets(wan, 129)
    wan_values[0:5] = [PROMPT, 640, 640, 5, SEED]
    wan_values[11] = True
    widgets(wan, 97)[0] = INPUT_IMAGE
    widgets(wan, 108)[0] = "video/compare_wan14_4step"
    write_workflow("compare_wan14_4step_i2v.json", wan)

    ltx = copy.deepcopy(read_workflow("video_ltx2_5_i2v.json"))
    ltx_values = widgets(ltx, 398)
    ltx_values[0:7] = [PROMPT, False, 5, 640, 640, SEED, 16]
    # ComfyUI validates combo values even when prompt enhancement is disabled.
    # Point the unused optional loader at an installed model.
    ltx_values[12] = (
        "gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
    )
    widgets(ltx, 403)[0:3] = ["1:1 (Square)", 0.4, 32]
    widgets(ltx, 395)[0] = INPUT_IMAGE
    widgets(ltx, 75)[0] = "video/compare_ltx2_5_i2v"
    write_workflow("compare_ltx2_5_i2v.json", ltx)


if __name__ == "__main__":
    main()
