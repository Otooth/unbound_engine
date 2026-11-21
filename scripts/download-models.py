#!/usr/bin/env python3
"""Download pinned Wan 2.2, LTX-2.5, and Z-Image model profiles and verify them."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

# Xet/CAS reconstruction has proved unreliable for multi-GB files on this host.
# Use the Hub's resumable HTTPS path instead.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import hf_hub_download


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"
STAGING_ROOT = MODELS_ROOT / ".downloads"

WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
WAN_REVISION = "ee6f4a40737a995bf5818954cfce6d59443b0f04"
LTX_REPO = "Lightricks/LTX-2.5"
LTX_REVISION = "49b093a39c19169a8a81721dcc541e07e945232f"
ANIMATE_REPO = "Comfy-Org/Wan-Animate-2"
ANIMATE_REVISION = "e7181fe1896b7f2ce34120d1a3663413548240d7"
ZIMAGE_REPO = "Comfy-Org/z_image_turbo"
ZIMAGE_REVISION = "6fc90a3b1b653e935a0d175e260736de25b84df5"

PROFILES = {
    "wan5b": (
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
            "diffusion_models/wan2.2_ti2v_5B_fp16.safetensors",
            9_999_658_848,
            "456f901338bd9eadbded3828b819109a9b68e8a525ca5cf8d0049a69fcfeca1e",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            6_735_906_897,
            "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/vae/wan2.2_vae.safetensors",
            "vae/wan2.2_vae.safetensors",
            1_409_400_960,
            "e40321bd36b9709991dae2530eb4ac303dd168276980d3e9bc4b6e2b75fed156",
        ),
    ),
    "wan14": (
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            "diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
            14_294_742_832,
            "6122e79d55e0f235698d11d657f3b196c5273c830da00b2b013c5a048d5e6a42",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            "diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
            14_294_742_832,
            "5471a457b6ac404202a5fbe6c11595a3d5641fc766b00f38763f72303fffc21e",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
            1_226_977_424,
            "d176c808d6fc461999b68e321efcb7501b20b8c3797523ed0df14f7d1deff11e",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            "loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
            1_226_977_424,
            "024f21de095bc8fad9809ded3e9e49a2e170dcf27075da8145ba7d60d8aab7f9",
        ),
        (
            WAN_REPO,
            WAN_REVISION,
            "split_files/vae/wan_2.1_vae.safetensors",
            "vae/wan_2.1_vae.safetensors",
            253_815_318,
            "2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b",
        ),
    ),
    "ltx25": (
        (
            LTX_REPO,
            LTX_REVISION,
            "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            "diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors",
            21_504_034_224,
            "c4279eeff115cbeaca494bd2183e7d768c38fe85a184dc6afbb7159157c44334",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors",
            15_372_969_374,
            "6ce688a0aa98a5fa36a9f1e6c3f42152a498cc2b53ee8c15674c64244f91487f",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "vae/ltx-2.5-video-vae-bf16.safetensors",
            "vae/ltx-2.5-video-vae-bf16.safetensors",
            1_472_223_346,
            "847e14ca7f3355debca0cea4eaa24ac0fbcdf0061da054ac89ca638a869ddba3",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "vae/ltx-2.5-audio-vae-bf16.safetensors",
            "vae/ltx-2.5-audio-vae-bf16.safetensors",
            364_866_540,
            "c52733d37f6a7fb7949c3dc0fb468c6cb2169e4d836983a73babb9f0d54837a5",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            "latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            995_778_752,
            "eb5a71fe4068ee87ccdb1c3aa635e547ca76bd2d30ae20ae889f2c325c0677e8",
        ),
    ),
    "animate2": (
        (
            ANIMATE_REPO,
            ANIMATE_REVISION,
            "diffusion_models/wan_animate_2_distill_int8_convrot.safetensors",
            "diffusion_models/wan_animate_2_distill_int8_convrot.safetensors",
            16_653_175_528,
            "d2e566ecac3f164cb1a6b63dc7d868d6678c9affa49fbceb3abe4f9458f89c8a",
        ),
        (
            ANIMATE_REPO,
            ANIMATE_REVISION,
            "clip_vision/clip_vision_h.safetensors",
            "clip_vision/clip_vision_h.safetensors",
            1_264_219_396,
            "64a7ef761bfccbadbaa3da77366aac4185a6c58fa5de5f589b42a65bcc21f161",
        ),
        (
            ANIMATE_REPO,
            ANIMATE_REVISION,
            "vae/Wan2_1_VAE_bf16.safetensors",
            "vae/Wan2_1_VAE_bf16.safetensors",
            253_806_278,
            "1ab9a32cc2c740f6e39d80d367ce5dcc28db8c71b79b28670546b8973e9d75f9",
        ),
    ),
    "zimage": (
        (
            ZIMAGE_REPO,
            ZIMAGE_REVISION,
            "split_files/diffusion_models/z_image_turbo_bf16.safetensors",
            "diffusion_models/z_image_turbo_bf16.safetensors",
            12_309_866_400,
            "2407613050b809ffdff18a4ac99af83ea6b95443ecebdf80e064a79c825574a6",
        ),
        (
            ZIMAGE_REPO,
            ZIMAGE_REVISION,
            "split_files/text_encoders/qwen_3_4b.safetensors",
            "text_encoders/qwen_3_4b.safetensors",
            8_044_982_048,
            "6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a",
        ),
        (
            ZIMAGE_REPO,
            ZIMAGE_REVISION,
            "split_files/vae/ae.safetensors",
            "vae/ae.safetensors",
            335_304_388,
            "afc8e28272cd15db3919bacdb6918ce9c1ed22e96cb12c4d5ed0fba823529e38",
        ),
    ),
}

# Type shape for one asset:
# (repository, revision, source path, local path, byte size, SHA256)
Asset = tuple[str, str, str, str, int, str]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path, expected_size: int, expected_sha256: str) -> None:
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise RuntimeError(
            f"{path}: size mismatch: expected {expected_size}, got {actual_size}"
        )
    actual_sha256 = sha256(path)
    if actual_sha256 != expected_sha256:
        raise RuntimeError(
            f"{path}: SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "profiles",
        nargs="*",
        choices=tuple(PROFILES),
        default=["wan5b"],
    )
    args = parser.parse_args()

    assets: list[Asset] = []
    seen_destinations: set[str] = set()
    for profile in args.profiles:
        for asset in PROFILES[profile]:
            destination_name = asset[3]
            if destination_name not in seen_destinations:
                assets.append(asset)
                seen_destinations.add(destination_name)

    for (
        repo_id,
        revision,
        source_name,
        destination_name,
        expected_size,
        expected_sha256,
    ) in assets:
        destination = MODELS_ROOT / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            print(f"Verifying existing file: {destination}")
            verify(destination, expected_size, expected_sha256)
            print(f"OK {expected_sha256}  {destination}")
            continue

        print(f"Downloading {repo_id}@{revision}:{source_name}")
        repository_staging = STAGING_ROOT / repo_id.replace("/", "--")
        downloaded = Path(
            hf_hub_download(
                repo_id=repo_id,
                filename=source_name,
                revision=revision,
                local_dir=repository_staging,
            )
        )
        verify(downloaded, expected_size, expected_sha256)
        os.replace(downloaded, destination)
        print(f"OK {expected_sha256}  {destination}")


if __name__ == "__main__":
    main()
