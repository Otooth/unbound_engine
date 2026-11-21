#!/usr/bin/env python3
"""Download pinned official workflows and their input assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen


COMMIT = "85984c2b1ee8c0aa39179bf122a7d01fa1b21c09"
PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROFILES = {
    "wan5b": (
        (
            "templates/video_wan2_2_5B_ti2v.json",
            "workflows/video_wan2_2_5B_ti2v.json",
            "634303feba82471ef3df3356178de4c037ae213e",
        ),
    ),
    "wan14": (
        (
            "templates/video_wan2_2_14B_i2v.json",
            "workflows/video_wan2_2_14B_i2v.json",
            "54d456e20340d36fa3edbb13864d0d49c3ae2273",
        ),
        (
            "input/video_wan2_2_14B_i2v_input_image.jpg",
            "input/video_wan2_2_14B_i2v_input_image.jpg",
            "61851496c348f88546d8007f6d1aa23c446ed35a",
        ),
    ),
    "ltx25": (
        (
            "templates/video_ltx2_5_i2v.json",
            "workflows/video_ltx2_5_i2v.json",
            "15a7d911874f48957dd0fe0d5e3dd15047fc73a9",
        ),
        (
            "input/neon_cyborg_portrait.png",
            "input/neon_cyborg_portrait.png",
            "800f0a103d4851afcb320a58fd2876f96dc84d3d",
        ),
    ),
    "animate2": (
        (
            "templates/video_wan_animate2_distilled.json",
            "workflows/video_wan_animate2_distilled.json",
            "b2771d09110f00a5ab9690df23b118dc695f129b",
        ),
        (
            "input/ref_doll_realistic.png",
            "input/ref_doll_realistic.png",
            "8a248143f124c04d50d35c22a520f21f17fdc0e8",
        ),
        (
            "input/pink_hair_mech_arms_ref.png",
            "input/pink_hair_mech_arms_ref.png",
            "7e0f0fbc9dd2aeed46d30c72930caa0ce0994bd4",
        ),
        (
            "input/drive_doll_street_dance.mp4",
            "input/drive_doll_street_dance.mp4",
            "f10288ce6e96ed0fe46dfde71977d35a72a36a51",
        ),
    ),
}


def git_blob_sha1(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode()
    return hashlib.sha1(header + data, usedforsecurity=False).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "profiles",
        nargs="*",
        choices=tuple(PROFILES),
        default=["wan5b"],
    )
    args = parser.parse_args()

    seen_destinations: set[str] = set()
    for profile in args.profiles:
        for source_path, destination_path, expected_blob in PROFILES[profile]:
            if destination_path in seen_destinations:
                continue
            seen_destinations.add(destination_path)

            url = (
                "https://raw.githubusercontent.com/Comfy-Org/"
                f"workflow_templates/{COMMIT}/{source_path}"
            )
            with urlopen(url, timeout=60) as response:
                data = response.read()

            actual_blob = git_blob_sha1(data)
            if actual_blob != expected_blob:
                raise RuntimeError(
                    f"{source_path}: Git blob mismatch: "
                    f"expected {expected_blob}, got {actual_blob}"
                )

            if destination_path.endswith(".json"):
                json.loads(data)
            destination = PROJECT_ROOT / destination_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            print(f"OK {actual_blob}  {destination}")


if __name__ == "__main__":
    main()
