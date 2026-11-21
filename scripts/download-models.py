#!/usr/bin/env python3
"""Download pinned Wan 2.2 and LTX-2.5 model profiles and verify them."""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# Xet/CAS reconstruction has proved unreliable for multi-GB files on this host.
# Use the Hub's resumable HTTPS path instead.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from huggingface_hub import get_token, hf_hub_url


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MODELS_ROOT = PROJECT_ROOT / "models"
STAGING_ROOT = MODELS_ROOT / ".downloads"

WAN_REPO = "Comfy-Org/Wan_2.2_ComfyUI_Repackaged"
WAN_REVISION = "ee6f4a40737a995bf5818954cfce6d59443b0f04"
LTX_REPO = "Lightricks/LTX-2.5"
LTX_REVISION = "49b093a39c19169a8a81721dcc541e07e945232f"
LTX_DETAIL_REPO = (
    "Lightricks/LTX-2.5-22b-IC-LoRA-Pixel-Spatial-Upscaler"
)
LTX_DETAIL_REVISION = "59f26f1e0c11af42f1affa924b4ef231e9e98b2a"
ANIMATE_REPO = "Comfy-Org/Wan-Animate-2"
ANIMATE_REVISION = "e7181fe1896b7f2ce34120d1a3663413548240d7"

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
    "ltx25-hq": (
        (
            LTX_REPO,
            LTX_REVISION,
            "diffusion_models/ltx-2.5-22b-dev-transformer-bf16.safetensors",
            "diffusion_models/ltx-2.5-22b-dev-transformer-bf16.safetensors",
            42_018_190_584,
            "792a2bad501ca03262c0bc2ce7a2949e85b142ce18e30894aad5bc849c8e7584",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
            26_263_858_182,
            "ef7243612fdae7a75cb4d5cee9433e81380675fb6c213bd98ae74a9cd16561d1",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
            "loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
            8_899_889_568,
            "86370bbf79a9eb4edaa158907e2b48a5188fe4c5dc8ce30c7eb8f2f131a9bbf5",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "model_patches/ltx-2.5-duration-head-bf16.safetensors",
            "model_patches/ltx-2.5-duration-head-bf16.safetensors",
            3_843_690,
            "2ec71e4206ed365d015f00c05a48caccfb0ee862986809d06ae376c09f5d9190",
        ),
    ),
    "ltx25-dfr": (
        (
            LTX_REPO,
            LTX_REVISION,
            "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
            "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
            42_018_190_584,
            "31eb3cad89b9e54e99dd3baf286f70825ac4f6c660a70d9184d895be76d7bff4",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
            "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
            26_263_858_182,
            "ef7243612fdae7a75cb4d5cee9433e81380675fb6c213bd98ae74a9cd16561d1",
        ),
        (
            LTX_REPO,
            LTX_REVISION,
            "model_patches/ltx-2.5-duration-head-bf16.safetensors",
            "model_patches/ltx-2.5-duration-head-bf16.safetensors",
            3_843_690,
            "2ec71e4206ed365d015f00c05a48caccfb0ee862986809d06ae376c09f5d9190",
        ),
        (
            LTX_DETAIL_REPO,
            LTX_DETAIL_REVISION,
            "ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors",
            "loras/ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors",
            327_322_640,
            "984851b769ea2bcb4c9e0a239a7676239e42c6a6001ddc69943b41ff0b283c1d",
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
}

PROFILE_DEPENDENCIES = {
    "ltx25-hq": ("ltx25",),
    "ltx25-dfr": ("ltx25",),
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


def download_with_retries(
    *,
    repo_id: str,
    filename: str,
    revision: str,
    local_dir: Path,
    expected_size: int,
    expected_sha256: str,
) -> Path:
    """Append authenticated byte ranges without ever truncating valid progress."""

    maximum_stalled_attempts = int(
        os.environ.get("HF_DOWNLOAD_STALLED_ATTEMPTS", "100")
    )
    if maximum_stalled_attempts < 1:
        raise ValueError("HF_DOWNLOAD_STALLED_ATTEMPTS must be at least 1")
    timeout = float(os.environ.get("HF_DOWNLOAD_TIMEOUT", "60"))
    if timeout <= 0:
        raise ValueError("HF_DOWNLOAD_TIMEOUT must be positive")

    partial = local_dir / ".unbound" / f"{filename}.incomplete"
    partial.parent.mkdir(parents=True, exist_ok=True)
    legacy_directory = (
        local_dir
        / ".cache"
        / "huggingface"
        / "download"
        / Path(filename).parent
    )
    if not partial.exists() and legacy_directory.is_dir():
        legacy_candidates = list(
            legacy_directory.glob(
                f"*.{expected_sha256}.*.incomplete"
            )
        )
        if legacy_candidates:
            legacy = max(legacy_candidates, key=lambda path: path.stat().st_size)
            os.replace(legacy, partial)
            print(
                f"Adopted {partial.stat().st_size} resumable bytes from "
                "the Hub staging file.",
                flush=True,
            )

    url = hf_hub_url(repo_id, filename, revision=revision)
    token = get_token()
    stalled_attempts = 0
    while True:
        resume_size = partial.stat().st_size if partial.exists() else 0
        if resume_size == expected_size:
            return partial
        if resume_size > expected_size:
            raise RuntimeError(
                f"{partial}: partial file exceeds expected size: "
                f"{resume_size} > {expected_size}"
            )

        headers = {
            "Accept-Encoding": "identity",
            "Range": f"bytes={resume_size}-",
            "User-Agent": "unbound-engine-model-downloader/1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = Request(url, headers=headers)
        before = resume_size
        failure: Exception = RuntimeError(
            "connection ended before the expected file size"
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status = response.status
                content_range = response.headers.get("Content-Range", "")
                if status != 206 or not content_range.startswith(
                    f"bytes {resume_size}-"
                ):
                    raise RuntimeError(
                        "server did not honor the exact resume range: "
                        f"status={status}, Content-Range={content_range!r}"
                    )
                with partial.open("ab") as output:
                    while chunk := response.read(16 * 1024 * 1024):
                        output.write(chunk)
                        resume_size += len(chunk)
                        if resume_size > expected_size:
                            raise RuntimeError(
                                "server returned more bytes than expected"
                            )
        except HTTPError as error:
            if error.code in {401, 403, 404}:
                raise RuntimeError(
                    f"non-retryable Hub HTTP {error.code} for "
                    f"{repo_id}:{filename}"
                ) from error
            failure: Exception = error
        except Exception as error:
            failure = error

        current_size = partial.stat().st_size if partial.exists() else 0
        if current_size == expected_size:
            return partial
        if current_size > before:
            stalled_attempts = 0
            delay = 1
        else:
            stalled_attempts += 1
            if stalled_attempts >= maximum_stalled_attempts:
                raise failure
            delay = min(30, stalled_attempts * 5)
        print(
            f"Transfer interrupted at {current_size}/{expected_size} bytes: "
            f"{type(failure).__name__}: {failure}",
            flush=True,
        )
        print(f"Resuming in {delay} seconds...", flush=True)
        time.sleep(delay)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "profiles",
        nargs="*",
        choices=tuple(PROFILES),
        default=["wan5b"],
    )
    parser.add_argument(
        "--plan",
        action="store_true",
        help="List pinned assets and missing bytes without downloading",
    )
    args = parser.parse_args()

    assets: list[Asset] = []
    seen_destinations: set[str] = set()
    for profile in args.profiles:
        expanded = (*PROFILE_DEPENDENCIES.get(profile, ()), profile)
        for expanded_profile in expanded:
            for asset in PROFILES[expanded_profile]:
                destination_name = asset[3]
                if destination_name not in seen_destinations:
                    assets.append(asset)
                    seen_destinations.add(destination_name)

    if args.plan:
        missing_bytes = 0
        for asset in assets:
            destination = MODELS_ROOT / asset[3]
            present = (
                destination.is_file()
                and destination.stat().st_size == asset[4]
            )
            if not present:
                missing_bytes += asset[4]
            print(
                f"{'present' if present else 'missing'} "
                f"{asset[4]:>14} {asset[3]}"
            )
        print(f"Missing total: {missing_bytes} bytes")
        print(f"Missing total: {missing_bytes / 1024**3:.2f} GiB")
        return

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
        downloaded = download_with_retries(
            repo_id=repo_id,
            filename=source_name,
            revision=revision,
            local_dir=repository_staging,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        verify(downloaded, expected_size, expected_sha256)
        os.replace(downloaded, destination)
        print(f"OK {expected_sha256}  {destination}")


if __name__ == "__main__":
    main()
