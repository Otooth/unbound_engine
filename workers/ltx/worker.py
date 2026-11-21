"""Loopback-only HTTP worker for the official LTX Python pipelines.

The worker deliberately imports no LTX or PyTorch modules.  A pipeline is
loaded only in a short-lived subprocess after a validated job is accepted.
When that subprocess exits, its model and CUDA allocations are released.
"""

from __future__ import annotations

import gc
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit


LTX_REPOSITORY_COMMIT = "daa6b16fc7f0f1628f2c9e36a303e3312b333cc6"
LTX_PIPELINES_VERSION = "1.6.2"

TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "cancelled"})
ALLOWED_IMAGE_SUFFIXES = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".exr"}
)
MAX_PROMPT_LENGTH = 20_000
MAX_ERROR_LENGTH = 16_384

OFFLINE_ENVIRONMENT = {
    "DO_NOT_TRACK": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_OFFLINE": "1",
    "PIP_NO_INDEX": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "UV_OFFLINE": "1",
    "WANDB_MODE": "offline",
}


class ValidationError(ValueError):
    """The HTTP request is invalid."""


class BusyError(RuntimeError):
    """The single worker slot is occupied."""


class NotFoundError(LookupError):
    """The requested job does not exist."""


class ConflictError(RuntimeError):
    """The requested state transition is not valid."""


class ConfigurationError(RuntimeError):
    """The worker runtime is not configured correctly."""


class Profile(str, Enum):
    TI2_HQ = "ti2_hq"
    DFR = "dfr"
    TI2_CHUNKED = "ti2_chunked"


@dataclass(frozen=True)
class ModelPaths:
    """Split-checkpoint paths consumed by the official LTX 1.6.2 CLIs."""

    dev_transformer: Path
    distilled_transformer: Path
    text_encoder: Path
    video_vae: Path
    audio_vae: Path
    duration_head: Path
    spatial_upsampler: Path
    distilled_lora: Path
    detailing_lora: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ModelPaths":
        env = os.environ if environ is None else environ
        root = Path(env.get("LTX_MODEL_ROOT", "/models"))

        def configured(name: str, relative: str) -> Path:
            return Path(env.get(name, str(root / relative)))

        return cls(
            dev_transformer=configured(
                "LTX_DEV_TRANSFORMER_PATH",
                "diffusion_models/ltx-2.5-22b-dev-transformer-bf16.safetensors",
            ),
            distilled_transformer=configured(
                "LTX_DISTILLED_TRANSFORMER_PATH",
                "diffusion_models/ltx-2.5-22b-distilled-transformer-bf16.safetensors",
            ),
            text_encoder=configured(
                "LTX_TEXT_ENCODER_PATH",
                "text_encoders/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
            ),
            video_vae=configured(
                "LTX_VIDEO_VAE_PATH",
                "vae/ltx-2.5-video-vae-bf16.safetensors",
            ),
            audio_vae=configured(
                "LTX_AUDIO_VAE_PATH",
                "vae/ltx-2.5-audio-vae-bf16.safetensors",
            ),
            duration_head=configured(
                "LTX_DURATION_HEAD_PATH",
                "model_patches/ltx-2.5-duration-head-bf16.safetensors",
            ),
            spatial_upsampler=configured(
                "LTX_SPATIAL_UPSAMPLER_PATH",
                "latent_upscale_models/"
                "ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors",
            ),
            distilled_lora=configured(
                "LTX_DISTILLED_LORA_PATH",
                "loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors",
            ),
            detailing_lora=configured(
                "LTX_DETAILING_LORA_PATH",
                "loras/"
                "ltx-2.5-22b-ic-lora-pixel-spatial-upscaler-x2-1.0.safetensors",
            ),
        )

    def required_for(self, profile: Profile) -> dict[str, Path]:
        common = {
            "LTX_TEXT_ENCODER_PATH": self.text_encoder,
            "LTX_VIDEO_VAE_PATH": self.video_vae,
            "LTX_AUDIO_VAE_PATH": self.audio_vae,
            "LTX_DURATION_HEAD_PATH": self.duration_head,
            "LTX_SPATIAL_UPSAMPLER_PATH": self.spatial_upsampler,
        }
        if profile is Profile.DFR:
            return {
                **common,
                "LTX_DISTILLED_TRANSFORMER_PATH": self.distilled_transformer,
                "LTX_DETAILING_LORA_PATH": self.detailing_lora,
            }
        return {
            **common,
            "LTX_DEV_TRANSFORMER_PATH": self.dev_transformer,
            "LTX_DISTILLED_LORA_PATH": self.distilled_lora,
        }


@dataclass(frozen=True)
class WorkerSettings:
    input_root: Path
    output_root: Path
    repository_root: Path
    python_executable: str
    host: str
    port: int
    max_request_bytes: int
    cancel_grace_seconds: float
    offload_mode: str
    model_paths: ModelPaths

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "WorkerSettings":
        env = os.environ if environ is None else environ
        host = env.get("LTX_WORKER_HOST", "127.0.0.1")
        if host != "127.0.0.1":
            raise ConfigurationError(
                "LTX_WORKER_HOST must be 127.0.0.1; this worker is loopback-only"
            )

        try:
            port = int(env.get("LTX_WORKER_PORT", "8191"))
            max_request_bytes = int(env.get("LTX_MAX_REQUEST_BYTES", "65536"))
            cancel_grace_seconds = float(
                env.get("LTX_CANCEL_GRACE_SECONDS", "10")
            )
        except ValueError as exc:
            raise ConfigurationError("invalid numeric worker environment value") from exc

        if not 1 <= port <= 65535:
            raise ConfigurationError("LTX_WORKER_PORT must be between 1 and 65535")
        if not 1024 <= max_request_bytes <= 10 * 1024 * 1024:
            raise ConfigurationError(
                "LTX_MAX_REQUEST_BYTES must be between 1024 and 10485760"
            )
        if not 0 < cancel_grace_seconds <= 300:
            raise ConfigurationError(
                "LTX_CANCEL_GRACE_SECONDS must be greater than 0 and at most 300"
            )
        offload_mode = env.get("LTX_OFFLOAD_MODE", "cpu")
        if offload_mode not in {"none", "cpu", "disk"}:
            raise ConfigurationError(
                "LTX_OFFLOAD_MODE must be one of: none, cpu, disk"
            )

        return cls(
            input_root=Path(env.get("LTX_INPUT_ROOT", "/data/input")),
            output_root=Path(env.get("LTX_OUTPUT_ROOT", "/data/output")),
            repository_root=Path(env.get("LTX_REPOSITORY_ROOT", "/opt/ltx")),
            python_executable=env.get("LTX_PYTHON", sys.executable),
            host=host,
            port=port,
            max_request_bytes=max_request_bytes,
            cancel_grace_seconds=cancel_grace_seconds,
            offload_mode=offload_mode,
            model_paths=ModelPaths.from_env(env),
        )


@dataclass(frozen=True)
class GenerateRequest:
    profile: Profile
    input_path: Path | None
    output_path: Path
    prompt: str
    negative: str | None
    seed: int
    width: int
    height: int
    frames: int
    fps: float

    @classmethod
    def from_dict(
        cls,
        payload: object,
        settings: WorkerSettings,
    ) -> "GenerateRequest":
        if not isinstance(payload, dict):
            raise ValidationError("JSON body must be an object")

        allowed = {
            "profile",
            "input_path",
            "output_path",
            "prompt",
            "negative",
            "seed",
            "width",
            "height",
            "frames",
            "fps",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValidationError(f"unknown fields: {', '.join(unknown)}")

        profile_value = _required_string(payload, "profile", 64)
        try:
            profile = Profile(profile_value)
        except ValueError as exc:
            choices = ", ".join(item.value for item in Profile)
            raise ValidationError(f"profile must be one of: {choices}") from exc

        prompt = _required_string(payload, "prompt", MAX_PROMPT_LENGTH)
        negative = _optional_string(payload, "negative", MAX_PROMPT_LENGTH)
        if profile is Profile.DFR and negative:
            raise ValidationError(
                "profile dfr does not support negative prompts in "
                "ltx-pipelines 1.6.2"
            )

        seed = _required_int(payload, "seed")
        width = _required_int(payload, "width")
        height = _required_int(payload, "height")
        frames = _required_int(payload, "frames")
        fps = _required_float(payload, "fps")

        if not 0 <= seed <= (2**63 - 1):
            raise ValidationError("seed must be between 0 and 2^63-1")
        for field_name, value in (("width", width), ("height", height)):
            if not 64 <= value <= 4096:
                raise ValidationError(f"{field_name} must be between 64 and 4096")
            if value % 64 != 0:
                raise ValidationError(f"{field_name} must be divisible by 64")
        if not 1 <= frames <= 4097 or (frames - 1) % 8 != 0:
            raise ValidationError(
                "frames must be between 1 and 4097 and follow the 8k+1 grid"
            )
        if not math.isfinite(fps) or not 1 <= fps <= 120:
            raise ValidationError("fps must be finite and between 1 and 120")

        input_value = _optional_string(payload, "input_path", 4096)
        input_path = (
            _resolve_input_path(settings.input_root, input_value)
            if input_value
            else None
        )
        output_value = _required_string(payload, "output_path", 4096)
        output_path = _resolve_output_path(settings.output_root, output_value)

        return cls(
            profile=profile,
            input_path=input_path,
            output_path=output_path,
            prompt=prompt,
            negative=negative,
            seed=seed,
            width=width,
            height=height,
            frames=frames,
            fps=fps,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "input_path": str(self.input_path) if self.input_path else None,
            "output_path": str(self.output_path),
            "prompt": self.prompt,
            "negative": self.negative,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "fps": self.fps,
        }


def _required_string(
    payload: Mapping[str, object],
    name: str,
    maximum_length: int,
) -> str:
    if name not in payload:
        raise ValidationError(f"missing required field: {name}")
    value = payload[name]
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    value = value.strip()
    if not value:
        raise ValidationError(f"{name} must not be empty")
    if len(value) > maximum_length:
        raise ValidationError(f"{name} is too long")
    if "\x00" in value:
        raise ValidationError(f"{name} must not contain NUL")
    return value


def _optional_string(
    payload: Mapping[str, object],
    name: str,
    maximum_length: int,
) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string or null")
    value = value.strip()
    if not value:
        return None
    if len(value) > maximum_length:
        raise ValidationError(f"{name} is too long")
    if "\x00" in value:
        raise ValidationError(f"{name} must not contain NUL")
    return value


def _required_int(payload: Mapping[str, object], name: str) -> int:
    if name not in payload:
        raise ValidationError(f"missing required field: {name}")
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer")
    return value


def _required_float(payload: Mapping[str, object], name: str) -> float:
    if name not in payload:
        raise ValidationError(f"missing required field: {name}")
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{name} must be a number")
    return float(value)


def _resolve_candidate(root: Path, raw_path: str, *, strict: bool) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = resolved_root / candidate
        resolved = candidate.resolve(strict=strict)
        resolved.relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValidationError(f"path is invalid or outside {root}: {raw_path}") from exc
    return resolved


def _resolve_input_path(root: Path, raw_path: str) -> Path:
    path = _resolve_candidate(root, raw_path, strict=True)
    if not path.is_file():
        raise ValidationError(f"input_path is not a regular file: {raw_path}")
    if path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES:
        suffixes = ", ".join(sorted(ALLOWED_IMAGE_SUFFIXES))
        raise ValidationError(f"input_path must use one of: {suffixes}")
    return path


def _resolve_output_path(root: Path, raw_path: str) -> Path:
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValidationError(f"cannot create output root: {root}") from exc
    path = _resolve_candidate(root, raw_path, strict=False)
    if path.suffix.lower() != ".mp4":
        raise ValidationError("output_path must end in .mp4")
    if path.exists():
        raise ValidationError("output_path already exists")
    return path


def build_command(
    request: GenerateRequest,
    settings: WorkerSettings,
) -> list[str]:
    """Build an argv list matching the official LTX 1.6.2 pipeline CLIs."""

    models = settings.model_paths
    if request.profile is Profile.TI2_HQ:
        module = "ltx_pipelines.ti2vid_two_stages_hq"
        transformer = models.dev_transformer
    elif request.profile is Profile.DFR:
        module = "ltx_pipelines.dfr_pipeline"
        transformer = models.distilled_transformer
    else:
        module = "ltx_pipelines.ti2vid_two_stages"
        transformer = models.dev_transformer

    command = [
        settings.python_executable,
        "-m",
        module,
        "--transformer-path",
        str(transformer),
        "--text-encoder-path",
        str(models.text_encoder),
        "--video-vae-path",
        str(models.video_vae),
        "--audio-vae-path",
        str(models.audio_vae),
        "--duration-head-path",
        str(models.duration_head),
        "--spatial-upsampler-path",
        str(models.spatial_upsampler),
        "--offload",
        settings.offload_mode,
    ]

    if request.profile is Profile.DFR:
        command.extend(["--detailing-lora", str(models.detailing_lora)])
    else:
        command.extend(["--distilled-lora", str(models.distilled_lora)])

    command.extend(["--prompt", request.prompt])
    if request.negative:
        command.extend(["--negative-prompt", request.negative])
    if request.input_path:
        command.extend(
            ["--image", str(request.input_path), "0", "1.0"]
        )

    command.extend(
        [
            "--output-path",
            str(request.output_path),
            "--seed",
            str(request.seed),
            "--width",
            str(request.width),
            "--height",
            str(request.height),
            "--num-frames",
            str(request.frames),
            "--frame-rate",
            format(request.fps, ".12g"),
        ]
    )
    if request.profile is Profile.TI2_CHUNKED:
        command.extend(
            [
                "--chunked",
                "--chunk-pixel-frames",
                "97",
                "--chunk-carry-frames",
                "25",
                "--chunk-blend-frames",
                "25",
            ]
        )
    return command


def offline_environment(
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a child environment with all supported offline switches forced."""

    environment = dict(os.environ if base is None else base)
    environment.update(OFFLINE_ENVIRONMENT)
    return environment


def missing_model_paths(
    profile: Profile,
    model_paths: ModelPaths,
) -> dict[str, str]:
    return {
        name: str(path)
        for name, path in model_paths.required_for(profile).items()
        if not path.is_file()
    }


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    request: GenerateRequest
    status: str = "queued"
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    error: str | None = None
    cancel_requested: bool = False
    process: subprocess.Popen[bytes] | None = field(
        default=None,
        repr=False,
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "request": self.request.as_dict(),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "error": self.error,
        }


class JobManager:
    """In-memory single-slot job manager."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self._jobs: dict[str, Job] = {}
        self._active_job_id: str | None = None
        self._lock = threading.Lock()

    def submit(self, request: GenerateRequest) -> dict[str, Any]:
        with self._lock:
            if self._active_job_id is not None:
                raise BusyError(
                    f"worker already has active job {self._active_job_id}"
                )
            job = Job(id=str(uuid.uuid4()), request=request)
            self._jobs[job.id] = job
            self._active_job_id = job.id
            thread = threading.Thread(
                target=self._run_job,
                args=(job.id,),
                name=f"ltx-job-{job.id}",
                daemon=True,
            )
            try:
                thread.start()
            except Exception:
                self._active_job_id = None
                del self._jobs[job.id]
                raise
            return job.as_dict()

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._get_locked(job_id).as_dict()

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._get_locked(job_id)
            if job.status in TERMINAL_JOB_STATES:
                raise ConflictError(f"job is already {job.status}")
            job.cancel_requested = True
            job.status = "cancelling"
            process = job.process
            snapshot = job.as_dict()

        if process is not None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            threading.Thread(
                target=self._kill_after_grace,
                args=(process,),
                name=f"ltx-cancel-{job_id}",
                daemon=True,
            ).start()
        return snapshot

    def release(self) -> dict[str, Any]:
        with self._lock:
            if self._active_job_id is not None:
                raise BusyError(
                    "cannot release while a job is active; cancel it first"
                )
        collected = gc.collect()
        return {
            "released": True,
            "strategy": "per-job subprocess exit",
            "python_objects_collected": collected,
        }

    def health(self) -> dict[str, Any]:
        readiness = {
            profile.value: not bool(
                missing_model_paths(profile, self.settings.model_paths)
            )
            for profile in Profile
        }
        with self._lock:
            active_job_id = self._active_job_id
        return {
            "status": "ok",
            "active_job_id": active_job_id,
            "offline": True,
            "ltx_commit": LTX_REPOSITORY_COMMIT,
            "ltx_pipelines_version": LTX_PIPELINES_VERSION,
            "profiles_ready": readiness,
        }

    def shutdown(self) -> None:
        with self._lock:
            process = None
            if self._active_job_id is not None:
                process = self._jobs[self._active_job_id].process
        if process is not None:
            try:
                process.terminate()
                process.wait(timeout=self.settings.cancel_grace_seconds)
            except ProcessLookupError:
                return
            except subprocess.TimeoutExpired:
                process.kill()

    def _get_locked(self, job_id: str) -> Job:
        try:
            uuid.UUID(job_id)
        except ValueError as exc:
            raise NotFoundError("job not found") from exc
        job = self._jobs.get(job_id)
        if job is None:
            raise NotFoundError("job not found")
        return job

    def _run_job(self, job_id: str) -> None:
        try:
            with self._lock:
                job = self._jobs[job_id]
                if job.cancel_requested:
                    self._finish_locked(job, "cancelled")
                    return

            missing = missing_model_paths(
                job.request.profile,
                self.settings.model_paths,
            )
            if missing:
                details = ", ".join(
                    f"{name}={path}" for name, path in sorted(missing.items())
                )
                raise ConfigurationError(f"missing model files: {details}")

            job.request.output_path.parent.mkdir(parents=True, exist_ok=True)
            if job.request.output_path.exists():
                raise ConflictError("output_path appeared after job validation")

            command = build_command(job.request, self.settings)
            with tempfile.TemporaryFile() as process_log:
                with self._lock:
                    if job.cancel_requested:
                        self._finish_locked(job, "cancelled")
                        return
                    job.status = "running"
                    job.started_at = utc_now()
                    process = subprocess.Popen(
                        command,
                        cwd=self.settings.repository_root,
                        env=offline_environment(),
                        stdin=subprocess.DEVNULL,
                        stdout=process_log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    job.process = process

                return_code = process.wait()
                error_tail = _read_process_log_tail(process_log)

            with self._lock:
                job.process = None
                job.return_code = return_code
                if job.cancel_requested:
                    self._finish_locked(job, "cancelled")
                elif (
                    return_code == 0
                    and job.request.output_path.is_file()
                    and job.request.output_path.stat().st_size > 0
                ):
                    self._finish_locked(job, "succeeded")
                else:
                    error = f"LTX CLI exited with code {return_code}"
                    if return_code == 0:
                        error += " without a non-empty output file"
                    if error_tail:
                        error += f":\n{error_tail}"
                    self._finish_locked(job, "failed", error=error)
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.process = None
                if job.cancel_requested:
                    self._finish_locked(job, "cancelled")
                else:
                    self._finish_locked(job, "failed", error=str(exc))

    def _finish_locked(
        self,
        job: Job,
        status: str,
        *,
        error: str | None = None,
    ) -> None:
        job.status = status
        job.error = error[:MAX_ERROR_LENGTH] if error else None
        job.finished_at = utc_now()
        if self._active_job_id == job.id:
            self._active_job_id = None

    def _kill_after_grace(self, process: subprocess.Popen[bytes]) -> None:
        try:
            process.wait(timeout=self.settings.cancel_grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass


def _read_process_log_tail(process_log: Any) -> str:
    process_log.flush()
    process_log.seek(0, os.SEEK_END)
    size = process_log.tell()
    process_log.seek(max(0, size - MAX_ERROR_LENGTH))
    return process_log.read().decode("utf-8", errors="replace").strip()


class WorkerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        manager: JobManager,
    ) -> None:
        self.manager = manager
        self.settings = manager.settings
        super().__init__(address, WorkerRequestHandler)


class WorkerRequestHandler(BaseHTTPRequestHandler):
    server: WorkerHTTPServer
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            self._send_json(HTTPStatus.OK, self.server.manager.health())
            return
        if path.startswith("/jobs/"):
            job_id = path.removeprefix("/jobs/")
            if "/" in job_id or not job_id:
                self._send_error(HTTPStatus.NOT_FOUND, "route not found")
                return
            try:
                self._send_json(
                    HTTPStatus.OK,
                    self.server.manager.get(job_id),
                )
            except NotFoundError as exc:
                self._send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "route not found")

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            if path == "/generate":
                payload = self._read_json(required=True)
                request = GenerateRequest.from_dict(
                    payload,
                    self.server.settings,
                )
                job = self.server.manager.submit(request)
                self._send_json(HTTPStatus.ACCEPTED, job)
                return
            if path == "/release":
                self._read_json(required=False)
                self._send_json(
                    HTTPStatus.OK,
                    self.server.manager.release(),
                )
                return
            if path.startswith("/jobs/") and path.endswith("/cancel"):
                job_id = path.removeprefix("/jobs/").removesuffix("/cancel")
                if not job_id or "/" in job_id:
                    self._send_error(HTTPStatus.NOT_FOUND, "route not found")
                    return
                self._read_json(required=False)
                self._send_json(
                    HTTPStatus.ACCEPTED,
                    self.server.manager.cancel(job_id),
                )
                return
            self._send_error(HTTPStatus.NOT_FOUND, "route not found")
        except ValidationError as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))
        except NotFoundError as exc:
            self._send_error(HTTPStatus.NOT_FOUND, str(exc))
        except (BusyError, ConflictError) as exc:
            self._send_error(HTTPStatus.CONFLICT, str(exc))

    def _read_json(self, *, required: bool) -> object:
        content_length = self.headers.get("Content-Length")
        if content_length is None:
            if required:
                raise ValidationError("Content-Length is required")
            return {}
        try:
            length = int(content_length)
        except ValueError as exc:
            raise ValidationError("invalid Content-Length") from exc
        if length < 0 or length > self.server.settings.max_request_bytes:
            raise ValidationError("request body is too large")
        body = self.rfile.read(length)
        if not body:
            if required:
                raise ValidationError("JSON body is required")
            return {}
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0]
        if content_type.strip().lower() != "application/json":
            raise ValidationError("Content-Type must be application/json")
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError("request body is not valid UTF-8 JSON") from exc

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send_json(status, {"error": message})

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, message_format: str, *args: object) -> None:
        sys.stderr.write(
            f"{self.log_date_time_string()} "
            f"{self.client_address[0]} "
            f"{message_format % args}\n"
        )


def main() -> int:
    try:
        settings = WorkerSettings.from_env()
        manager = JobManager(settings)
        server = WorkerHTTPServer((settings.host, settings.port), manager)
    except (ConfigurationError, OSError) as exc:
        print(f"ltx worker startup failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"LTX worker listening on http://{settings.host}:{settings.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        manager.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
