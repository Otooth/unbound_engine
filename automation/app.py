from __future__ import annotations

import base64
import copy
import json
import os
import queue
import re
import shlex
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field, model_validator

from automation.backends import (
    BackendRegistry,
    ComfyUIBackend,
    ExclusiveBackendManager,
    LTXHTTPBackend,
)
from automation.quality import (
    QualityIssue,
    QualityReport,
    VISION_AUDIT_JSON_SCHEMA,
    aggregate_reports,
    build_temporal_analysis_command,
    evaluate_boundary_metrics,
    evaluate_temporal_findings,
    evaluate_vision_audit,
    parse_boundary_metrics,
    parse_temporal_findings,
    should_retry,
    validate_probe_result,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_ROOT = PROJECT_ROOT / "input"
OUTPUT_ROOT = PROJECT_ROOT / "output"
DATA_ROOT = PROJECT_ROOT / "data"
DB_PATH = DATA_ROOT / "jobs.sqlite3"
STATIC_ROOT = PROJECT_ROOT / "automation" / "static"
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_VIDEO_SUFFIXES = {".mp4"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_VIDEO_UPLOAD_BYTES = 250 * 1024 * 1024

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.6:35b-a3b-q8_0")
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
LTX_WORKER_URL = os.environ.get(
    "LTX_WORKER_URL", "http://127.0.0.1:8191"
).rstrip("/")
COMFY_TIMEOUT = int(os.environ.get("COMFY_TIMEOUT", "7200"))

ENGINE_CONFIG: dict[str, dict[str, Any]] = {
    "wan5b": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "video_wan2_2_5B_ti2v.api.json",
        "prompt_node": "6",
        "prompt_field": "text",
        "negative_node": "7",
        "image_node": None,
        "continuation_node": "55",
        "seed_node": "3",
        "seed_field": "seed",
        "save_node": "58",
        "prefix": "wan5b",
        "input_overrides": {
            "55": {"width": 640, "height": 640, "length": 81},
            "57": {"fps": 16},
        },
        "quality_overrides": {
            "55": {"width": 1280, "height": 704, "length": 121},
            "57": {"fps": 24},
        },
    },
    "wan14": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "compare_wan14_4step_i2v.api.json",
        "prompt_node": "129:93",
        "prompt_field": "text",
        "negative_node": "129:89",
        "image_node": "97",
        "continuation_node": "129:98",
        "seed_node": "129:86",
        "seed_field": "noise_seed",
        "save_node": "108",
        "prefix": "wan14",
        "input_overrides": {},
        "quality_overrides": {
            "129:98": {"width": 704, "height": 704},
        },
        # The official template's switch: false selects the standard
        # 20-step, CFG 3.5 path without the LightX2V 4-step LoRAs.
        "profile_overrides": {
            "quality": {
                "129:131": {"value": False},
            },
            "fast": {
                "129:131": {"value": True},
            },
        },
    },
    "ltx25": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "compare_ltx2_5_i2v.api.json",
        "prompt_node": "398:376",
        "prompt_field": "value",
        "negative_node": "398:373",
        "image_node": "395",
        "seed_node": "398:339",
        "seed_field": "noise_seed",
        "save_node": "75",
        "prefix": "ltx25",
        "input_overrides": {},
        "quality_overrides": {
            "403": {"megapixels": 0.5},
        },
    },
    "animate2": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "video_wan_animate2_distilled.api.json",
        "prompt_node": "605",
        "prompt_field": "value",
        "pose_prompt_node": "604",
        "negative_node": "261:4",
        "image_node": "189",
        "video_node": "240",
        "seed_node": "261:19",
        "seed_field": "noise_seed",
        "save_node": "246",
        "prefix": "animate2",
        "input_overrides": {},
        "quality_overrides": {
            "261:243": {
                "resize_type.width": 576,
                "resize_type.height": 992,
            },
        },
    },
}

COMFY_TERMINAL_STATUSES = {"success", "error"}
job_queue: queue.Queue[str | None] = queue.Queue()
worker_thread: threading.Thread | None = None
job_state_lock = threading.Lock()
cancelled_jobs: set[str] = set()
active_job_id: str | None = None

COMFY_BACKEND = ComfyUIBackend(
    COMFY_URL,
    wait_timeout=COMFY_TIMEOUT,
)
LTX_BACKEND = LTXHTTPBackend(
    LTX_WORKER_URL,
    submit_path="/generate",
    status_path="/jobs/{handle}",
    cancel_path="/jobs/{handle}/cancel",
    wait_timeout=COMFY_TIMEOUT,
)
BACKEND_MANAGER = ExclusiveBackendManager(
    BackendRegistry(
        {
            "comfy": COMFY_BACKEND,
            "ltx": LTX_BACKEND,
        }
    )
)


class JobCancelled(Exception):
    pass


class PlanAuditError(RuntimeError):
    """Raised before GPU work when a generated plan remains unsafe."""

    def __init__(self, message: str, plan: dict[str, Any]):
        super().__init__(message)
        self.plan = plan


class QualityGateError(RuntimeError):
    """Raised when a generated attempt fails a hard post-generation gate."""

    def __init__(self, report: QualityReport):
        self.report = report
        codes = ", ".join(issue.code for issue in report.issues if issue.hard)
        super().__init__(f"生成後品質ゲート不合格: {codes or 'unknown'}")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def image_signature_matches(path: Path, suffix: str) -> bool:
    with path.open("rb") as image:
        header = image.read(12)
    if suffix == ".png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if suffix in {".jpg", ".jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if suffix == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return False


def video_signature_matches(path: Path) -> bool:
    with path.open("rb") as video:
        header = video.read(12)
    return len(header) == 12 and header[4:8] == b"ftyp"


def db_connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with db_connect() as db:
        db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                engine TEXT NOT NULL,
                concept TEXT NOT NULL,
                input_image TEXT NOT NULL,
                reference_front TEXT NOT NULL DEFAULT '',
                reference_side TEXT NOT NULL DEFAULT '',
                reference_back TEXT NOT NULL DEFAULT '',
                driving_video TEXT NOT NULL DEFAULT '',
                shots INTEGER NOT NULL,
                seed INTEGER NOT NULL,
                enhance_prompt INTEGER NOT NULL DEFAULT 0,
                quality_mode INTEGER NOT NULL DEFAULT 0,
                precision_mode INTEGER NOT NULL DEFAULT 0,
                backend_id TEXT NOT NULL DEFAULT 'comfy',
                generation_profile TEXT NOT NULL DEFAULT '',
                attempt INTEGER NOT NULL DEFAULT 0,
                continuation_of TEXT NOT NULL DEFAULT '',
                plan_json TEXT,
                quality_json TEXT,
                continuation_json TEXT,
                output_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(jobs)").fetchall()
        }
        if "enhance_prompt" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN enhance_prompt INTEGER NOT NULL DEFAULT 0
                """
            )
        if "quality_mode" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN quality_mode INTEGER NOT NULL DEFAULT 0
                """
            )
        if "precision_mode" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN precision_mode INTEGER NOT NULL DEFAULT 0
                """
            )
        if "continuation_of" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN continuation_of TEXT NOT NULL DEFAULT ''
                """
            )
        additive_columns = {
            "backend_id": "TEXT NOT NULL DEFAULT 'comfy'",
            "generation_profile": "TEXT NOT NULL DEFAULT ''",
            "attempt": "INTEGER NOT NULL DEFAULT 0",
            "quality_json": "TEXT",
            "continuation_json": "TEXT",
        }
        for column, declaration in additive_columns.items():
            if column not in columns:
                db.execute(
                    f"ALTER TABLE jobs ADD COLUMN {column} {declaration}"
                )
        db.execute(
            """
            UPDATE jobs
            SET generation_profile = CASE
                WHEN engine = 'wan14' AND precision_mode = 1 THEN 'quality'
                WHEN engine = 'wan14' THEN 'fast'
                ELSE 'comfy'
            END
            WHERE generation_profile = ''
            """
        )
        if "driving_video" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN driving_video TEXT NOT NULL DEFAULT ''
                """
            )
        for column in ("reference_front", "reference_side", "reference_back"):
            if column not in columns:
                db.execute(
                    f"""
                    ALTER TABLE jobs
                    ADD COLUMN {column} TEXT NOT NULL DEFAULT ''
                    """
                )
        restarted = db.execute(
            """
            SELECT id FROM jobs
            WHERE status NOT IN ('succeeded', 'failed', 'cancelled')
            """
        ).fetchall()
        for row in restarted:
            db.execute(
                """
                UPDATE jobs SET status = 'failed', error = ?,
                    updated_at = ? WHERE id = ?
                """,
                ("Automation service restarted before completion", utc_now(), row["id"]),
            )
            db.execute(
                """
                INSERT INTO events(job_id, stage, message, created_at)
                VALUES (?, 'failed', ?, ?)
                """,
                (row["id"], "Service restarted before completion", utc_now()),
            )


def add_event(job_id: str, stage: str, message: str) -> None:
    with job_state_lock:
        if job_id in cancelled_jobs:
            raise JobCancelled(job_id)
    now = utc_now()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO events(job_id, stage, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, stage, message, now),
        )
        db.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (stage, now, job_id),
        )


def finish_job(
    job_id: str,
    status: Literal["succeeded", "failed", "cancelled"],
    *,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    if status == "succeeded":
        with job_state_lock:
            if job_id in cancelled_jobs:
                return
    with db_connect() as db:
        db.execute(
            """
            UPDATE jobs SET status = ?, output_path = ?, error = ?,
                updated_at = ? WHERE id = ?
            """,
            (status, output_path, error, utc_now(), job_id),
        )
        db.execute(
            """
            INSERT INTO events(job_id, stage, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, status, error or output_path or status, utc_now()),
        )


def raise_if_cancelled(job_id: str) -> None:
    with job_state_lock:
        if job_id in cancelled_jobs:
            raise JobCancelled(job_id)


def request_json(
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    timeout: int = 60,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"{url}: HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"{url}: {error.reason}") from error
    if not body:
        return {}
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{url}: expected a JSON object")
    return parsed


def unload_ollama() -> None:
    request_json(
        f"{OLLAMA_URL}/api/generate",
        {"model": OLLAMA_MODEL, "keep_alive": 0},
        timeout=120,
    )


MAX_PLAN_STEPS = 12
STEP_SPLIT_PATTERN = re.compile(
    r"[。．！？!?\n]+|→|->|⇒"
    r"|[、,]?\s*(?:その後|そのあと|それから|そして|次に|続いて|最後に)[、,]?"
    r"|(?<=[a-z0-9)\]])\.\s+"
    r"|[,;]?\s*\b(?:and\s+)?(?:then|after that|afterwards|finally)\b,?",
    re.IGNORECASE,
)


def ollama_structured(
    system: str,
    prompt: str,
    schema: dict[str, Any],
    temperature: float,
    num_predict: int,
    images: list[Path] | None = None,
) -> dict[str, Any]:
    payload = {
        "model": OLLAMA_MODEL,
        "system": system,
        "prompt": prompt,
        "format": schema,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if images:
        payload["images"] = [
            base64.b64encode(path.read_bytes()).decode("ascii")
            for path in images
        ]
    response = request_json(
        f"{OLLAMA_URL}/api/generate", payload, timeout=1800
    )
    raw = response.get("response")
    if not isinstance(raw, str):
        raise RuntimeError("Ollama response did not contain a response string")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise RuntimeError("Ollama returned a non-object response")
    return result


def clean_steps(raw_steps: Any) -> list[str]:
    if not isinstance(raw_steps, list):
        raise RuntimeError("Ollama returned an invalid step list")
    steps = [
        step.strip().rstrip(".")
        for step in raw_steps
        if isinstance(step, str) and step.strip()
    ]
    if not steps:
        raise RuntimeError("Ollama returned no steps")
    return steps[:MAX_PLAN_STEPS]


def clean_result_states(raw_states: Any, expected: int) -> list[str]:
    if not isinstance(raw_states, list):
        raise RuntimeError("Ollama returned an invalid result-state list")
    states = [
        state.strip().rstrip(".")
        for state in raw_states
        if isinstance(state, str) and state.strip()
    ]
    if len(states) != expected:
        raise RuntimeError("Ollama returned the wrong number of result states")
    return states


def split_concept_steps(concept: str) -> list[str]:
    parts = [
        part.strip(" 、,.;")
        for part in STEP_SPLIT_PATTERN.split(concept)
        if part and part.strip(" 、,.;")
    ]
    return parts[:MAX_PLAN_STEPS] or [concept.strip()]


def assign_steps(step_count: int, shot_count: int) -> list[list[int]]:
    if step_count >= shot_count:
        return [
            list(
                range(
                    round(index * step_count / shot_count),
                    round((index + 1) * step_count / shot_count),
                )
            )
            for index in range(shot_count)
        ]
    return [[index * step_count // shot_count] for index in range(shot_count)]


ANATOMY_PROMPT = (
    "Head, shoulders, torso, hips, and limbs stay connected and turn together "
    "with natural joints and one coherent body direction. Keep every object "
    "that was not explicitly released, using physically available hands."
)
OBJECT_CONSISTENCY_PROMPT = (
    "Every object keeps one coherent shape, orientation, count, and contact "
    "with the scene unless the requested action visibly changes it."
)
DEFAULT_ORIENTATION = (
    "Keep each subject in one natural, consistent orientation to the camera"
)
OBJECT_ORIENTATION = (
    "Keep every non-human subject in one coherent geometric orientation to "
    "the camera"
)
HUMAN_LIKE_SUBJECT_PATTERN = re.compile(
    r"\b(?:person|people|human|woman|women|man|men|girl|boy|adult|child|"
    r"warrior|character|performer|dancer|actor|actress|knight|soldier|"
    r"humanoid|android|cyborg|puppet|doll|figure|avatar)\b",
    re.IGNORECASE,
)
HUMAN_BODY_SIGNAL_PATTERN = re.compile(
    r"\b(?:hand|hands|head|face|shoulder|shoulders|torso|waist|hip|hips|"
    r"knee|knees|foot|feet|arm|arms|leg|legs|smile|smiles|walk|walks|"
    r"dance|dances|stand|stands|sit|sits)\b",
    re.IGNORECASE,
)
NEGATED_HUMAN_PATTERN = re.compile(
    r"\b(?:(?:no|without)\s+(?:visible\s+)?(?:people|persons?|humans?|"
    r"characters?|performers?|adults?|children)|empty of\s+(?:people|"
    r"persons?|humans?|characters?)|no subjects?(?:\s+are)?\s+present)\b",
    re.IGNORECASE,
)
CAMERA_VIEW_ORIENTATIONS = {
    "front": (
        "front view; face, shoulders, chest, hips, knees, and feet point "
        "toward the camera"
    ),
    "left_profile": (
        "left-facing side profile; face, shoulders, chest, hips, knees, and "
        "feet point toward frame left"
    ),
    "right_profile": (
        "right-facing side profile; face, shoulders, chest, hips, knees, and "
        "feet point toward frame right"
    ),
    "back": (
        "back view; shoulders, back, hips, knees, and feet point away from "
        "the camera, and the face is not visible"
    ),
}
IMAGE_ORIENTATION = (
    "preserve the exact natural body orientation in the input frame; all "
    "connected body parts turn together"
)


def infer_human_subject(declared: object, *descriptions: str) -> bool:
    """Fail safe to body constraints when the plan describes a human-like form."""

    text = NEGATED_HUMAN_PATTERN.sub(" ", " ".join(descriptions))
    if HUMAN_LIKE_SUBJECT_PATTERN.search(text):
        return True
    if HUMAN_BODY_SIGNAL_PATTERN.search(text):
        return True
    return declared is True and re.search(r"\bsubject\b", text, re.I) is not None


def build_segment_prompts(
    scene: str,
    orientation: str,
    steps: list[str],
    result_states: list[str],
    shot_count: int,
    has_start_image: bool,
    is_continuation: bool,
    human_subject: bool = True,
) -> list[dict[str, Any]]:
    assignments = assign_steps(len(steps), shot_count)
    shots: list[dict[str, Any]] = []
    for index, step_indices in enumerate(assignments):
        parts = ["One continuous medium-wide fixed-camera shot."]
        continuing = index > 0 and step_indices == assignments[index - 1]
        if index == 0:
            if has_start_image:
                parts.append(
                    CONTINUATION_SCENE if is_continuation else IMAGE_SCENE
                )
            elif scene:
                parts.append(f"Opening frame: {scene.rstrip(' .')}.")
        elif continuing:
            parts.append(
                "Continue directly from the input frame without restarting "
                "the action."
            )
        else:
            previous_state = result_states[assignments[index - 1][-1]]
            if previous_state:
                parts.append(f"Opening frame: {previous_state}.")
            else:
                parts.append("Continue directly from the input frame.")
        orientation_fallback = (
            DEFAULT_ORIENTATION if human_subject else OBJECT_ORIENTATION
        )
        parts.append(
            f"Natural subject orientation: "
            f"{orientation.rstrip(' .') or orientation_fallback}."
        )
        parts.append(
            ANATOMY_PROMPT if human_subject else OBJECT_CONSISTENCY_PROMPT
        )
        current = [steps[i] for i in step_indices]
        if continuing:
            parts.append(f"Continue and complete this action: {current[0]}.")
        elif len(current) == 1:
            parts.append(f"Action: {current[0]}.")
        else:
            ordered = ", then ".join(
                [current[0]]
                + [
                    step[0].lower() + step[1:]
                    if len(step) > 1 and step[1].islower()
                    else step
                    for step in current[1:]
                ]
            )
            parts.append(f"Actions in exact order: {ordered}.")
        end_state = result_states[step_indices[-1]]
        if end_state:
            parts.append(f"End state: {end_state}.")
        defect = "duplicate body part" if human_subject else "duplicate object"
        parts.append(
            "No cut, transition, reset, sudden zoom, "
            f"{defect}, or unrelated action."
        )
        shots.append(
            {
                "prompt": " ".join(parts),
                "steps": [i + 1 for i in step_indices],
            }
        )
    return shots


def build_state_transitions(
    scene: str,
    steps: list[str],
    result_states: list[str],
    *,
    has_start_image: bool,
) -> list[dict[str, str]]:
    """Pair every requested motion with its exact before/after state."""

    transitions: list[dict[str, str]] = []
    for index, (step, result_state) in enumerate(
        zip(steps, result_states, strict=True)
    ):
        if index:
            initial_state = result_states[index - 1]
        elif has_start_image:
            initial_state = "exact visible state in the supplied start image"
        else:
            initial_state = scene
        transitions.append(
            {
                "initial_state": initial_state,
                "visible_action": step,
                "result_state": result_state,
            }
        )
    return transitions


PLAN_SYSTEM = (
    "You convert a user's video request into structured data for a local "
    "image-to-video model. Write English. Return only the requested data.\n"
    "scene: who and what is visible at the very start, before the first "
    "action happens: subject count, appearance, clothing, objects, setting, "
    "lighting, and a camera framing that keeps every subject fully visible. "
    "Use a static medium-wide view. Describe the initial state, never the "
    "result of a later action. Even for continuous or cyclic movement, choose "
    "a distinct immediate pre-action phase before visible displacement: for "
    "example, a swell is formed offshore but has not yet rolled toward the "
    "sand.\n"
    "human_subject: true only when a person or human-like character is visible "
    "or explicitly requested; otherwise false. Objects and ordinary animals "
    "must not receive human body, face, limb, or handedness instructions.\n"
    "camera_view: choose exactly one of front, left_profile, right_profile, "
    "or back. It must support the whole sequence and agree with where the "
    "main object is placed in the scene. Prefer a side profile for actions "
    "toward a window, table, or other object beside the subject. Avoid any "
    "view that needs the head or torso to twist 180 degrees.\n"
    "steps: every action and change the user asked for, one per item, in "
    "the user's order. Keep each step as a concrete visible motion with its "
    "direction, speed, repetition, and who does it. Never merge, skip, "
    "summarize, reorder, or soften a step, and never invent new steps. "
    "Do not invent left/right handedness or two-handed grips unless the user "
    "specified them. Keep hands and carried objects physically consistent "
    "between steps. "
    "Reciprocal actions stay reciprocal: trading blows means both people "
    "repeatedly throw and block punches.\n"
    "result_states: exactly one visible state after each corresponding step. "
    "Carry forward changed objects and poses. Describe a coherent natural "
    "body orientation, not an action. Never anticipate a later step: grasping "
    "an object does not mean it has already been removed. Never forget a "
    "carried object in a later state. Do not add text overlays."
)


def plan_problems(
    concept: str,
    scene: str,
    orientation: str,
    steps: list[str],
    result_states: list[str],
    human_subject: bool = True,
) -> dict[str, Any]:
    schema = {
        "type": "object",
        "required": [
            "missing",
            "order_ok",
            "scene_shows_initial_state",
            "state_sequence_ok",
            "orientation_coherent",
            "physical_consistency_ok",
        ],
        "properties": {
            "missing": {"type": "array", "items": {"type": "string"}},
            "order_ok": {"type": "boolean"},
            "scene_shows_initial_state": {"type": "boolean"},
            "state_sequence_ok": {"type": "boolean"},
            "orientation_coherent": {"type": "boolean"},
            "physical_consistency_ok": {"type": "boolean"},
        },
    }
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    states = "\n".join(
        f"{i}. {state}" for i, state in enumerate(result_states, 1)
    )
    return ollama_structured(
        (
            "You audit a complete video plan against the user's request. "
            "List in missing every mandatory action, state, setting, lighting, "
            "camera, or ordering constraint absent from all of Scene, "
            "Orientation, Steps, and Result states; return an empty list when "
            "nothing is missing. Static setting, lighting, and camera "
            "constraints may be satisfied by Scene and need not be repeated "
            "inside each motion step. order_ok is false if the steps are "
            "in a different order than requested. scene_shows_initial_state "
            "is false only if the scene already shows a change that one of "
            "the steps must cause on screen, such as an object already "
            "moved, removed, added, or transformed. A scene that matches a "
            "static starting pose named in step 1 is a valid initial state. "
            "For a continuous, cyclic, or repeated action such as rolling "
            "waves, rain, rotation, or walking, Scene must use a distinct "
            "immediate phase before the requested displacement begins, not "
            "describe that same motion as already underway. "
            "state_sequence_ok is false if a result state does not follow "
            "from its action, anticipates a later action, or forgets a prior "
            "object or pose change. For example, if a step only grasps an "
            "object but its state says the object was removed, return false. "
            "If a person holds an object and a later state omits it without "
            "a release action, return false. "
            "For a human-like subject, orientation_coherent is false if the "
            "orientation could mix the front and back of a body, reverse a "
            "torso, or require an anatomically impossible twist. For a "
            "non-human subject, apply it to geometric orientation and treat "
            "human-only anatomy checks as not applicable. "
            "physical_consistency_ok is false "
            "if a later action tries to use an occupied hand without freeing "
            "it, changes handedness without instruction, loses an object, or "
            "requires an impossible limb arrangement. "
            "Return only the requested data."
        ),
        (
            f"Request:\n{concept}\n\nHuman-like subject: "
            f"{'yes' if human_subject else 'no'}\n\nScene:\n{scene}"
            f"\n\nOrientation:\n"
            f"{orientation}\n\nSteps:\n{numbered}\n\nResult states:\n{states}"
        ),
        schema,
        temperature=0.0,
        num_predict=384,
    )


IMAGE_SCENE = (
    "Keep the exact subjects, appearance, outfit, setting, lighting, and "
    "camera framing of the first frame. One continuous shot with no cuts."
)
CONTINUATION_SCENE = (
    "The input image is the exact final frame of the previous video. Begin "
    "from that exact pose, object state, subject orientation, framing, "
    "lighting, and camera position. Continue forward in time immediately; "
    "do not replay, reset, or summarize any previous action."
)
GENERIC_CONTINUITY_NEGATIVE = (
    "，镜头切换，转场，突然变焦，物体重复，形状突变，"
    "scene cut, transition, sudden zoom, duplicate object, unexplained shape "
    "change, inconsistent subject direction"
)
HUMAN_CONTINUITY_NEGATIVE = (
    "，换脸，人物外貌突变，身体前后融合，躯干反转，头部反转，"
    "关节扭曲，多余肢体，face change, front-back body fusion, face on back "
    "of head, reversed torso, mismatched body direction, twisted waist, "
    "180-degree head twist, duplicate limbs, extra body parts"
)


def create_plan(
    concept: str,
    shot_count: int,
    has_start_image: bool,
    continuation_context: str = "",
) -> dict[str, Any]:
    clauses = split_concept_steps(concept)
    fixed_count = len(clauses) > 1
    step_schema: dict[str, Any] = {
        "type": "array",
        "minItems": len(clauses) if fixed_count else 1,
        "maxItems": len(clauses) if fixed_count else MAX_PLAN_STEPS,
        "items": {"type": "string"},
    }
    schema = {
        "type": "object",
        "required": [
            "title",
            "scene",
            "human_subject",
            "camera_view",
            "steps",
            "result_states",
        ],
        "properties": {
            "title": {"type": "string"},
            "scene": {"type": "string"},
            "human_subject": {"type": "boolean"},
            "camera_view": {
                "type": "string",
                "enum": ["front", "left_profile", "right_profile", "back"],
            },
            "steps": step_schema,
            "result_states": {
                "type": "array",
                "minItems": len(clauses) if fixed_count else 1,
                "maxItems": (
                    len(clauses) if fixed_count else MAX_PLAN_STEPS
                ),
                "items": {"type": "string"},
            },
        },
    }
    request = (
        "Treat the following request as mandatory constraints, not "
        f"inspiration:\n{concept}"
    )
    if fixed_count:
        numbered = "\n".join(f"{i}. {c}" for i, c in enumerate(clauses, 1))
        request += (
            f"\n\nThe request has exactly {len(clauses)} ordered clauses. "
            f"Return exactly {len(clauses)} steps: step N is the English "
            "visible-motion version of clause N only. The first clause is "
            "step 1 even if it sounds like a pose or description; the scene "
            "must show the moment before clause 1 happens.\n" + numbered
        )
    if has_start_image:
        request += (
            "\n\nA start image defines appearance and framing, so keep the "
            "scene short and do not describe camera framing."
        )
    if continuation_context:
        request += (
            "\n\nThis job continues an already completed video. The supplied "
            "start image is its exact final frame. Perform only the new "
            "request above; never repeat any action from the prior video. "
            f"Prior final state for context only: {continuation_context}"
        )
    feedback = ""
    check: dict[str, Any] = {}
    ok = False
    for attempt in range(2):
        result = ollama_structured(
            PLAN_SYSTEM,
            request + feedback,
            schema,
            temperature=0.2 if attempt == 0 else 0.1,
            num_predict=1024,
        )
        scene = str(result.get("scene", "")).strip()
        declared_human_subject = result.get("human_subject", True)
        camera_view = str(result.get("camera_view", "")).strip()
        if camera_view not in CAMERA_VIEW_ORIENTATIONS:
            raise RuntimeError("Ollama returned an invalid camera view")
        steps = clean_steps(result.get("steps"))
        result_states = clean_result_states(
            result.get("result_states"), len(steps)
        )
        human_subject = infer_human_subject(
            declared_human_subject,
            concept,
            scene,
            *steps,
            *result_states,
        )
        if human_subject:
            orientation = (
                IMAGE_ORIENTATION
                if has_start_image
                else CAMERA_VIEW_ORIENTATIONS[camera_view]
            )
        else:
            orientation = (
                "preserve the exact coherent object orientation in the input "
                "frame"
                if has_start_image
                else OBJECT_ORIENTATION
            )
        if fixed_count and len(steps) != len(clauses):
            raise RuntimeError("Ollama changed the number of requested steps")
        check = plan_problems(
            concept,
            scene,
            orientation,
            steps,
            result_states,
            human_subject,
        )
        missing = [
            item for item in check.get("missing", []) if isinstance(item, str)
        ]
        check["scene_initial_state_applicable"] = not has_start_image
        scene_ok = (
            check.get("scene_shows_initial_state") is True
            if not has_start_image
            else True
        )
        ok = (
            not missing
            and check.get("order_ok") is True
            and scene_ok
            and check.get("state_sequence_ok") is True
            and check.get("orientation_coherent") is True
            and check.get("physical_consistency_ok") is True
        )
        if ok:
            break
        notes = []
        if missing:
            notes.append("These requested items were missing: " + "; ".join(missing))
        if check.get("order_ok") is not True:
            notes.append("The steps were out of the requested order.")
        if (
            not has_start_image
            and check.get("scene_shows_initial_state") is not True
        ):
            notes.append(
                "The scene described the requested motion or a later result "
                "instead of the immediate pre-action state. Rewrite it with "
                "no requested displacement completed yet; for a continuous "
                "action use a distinct phase just before motion begins."
            )
        if check.get("state_sequence_ok") is not True:
            notes.append(
                "One or more result states did not follow from the steps."
            )
        if check.get("orientation_coherent") is not True:
            notes.append(
                "The body orientation was anatomically inconsistent."
            )
        if check.get("physical_consistency_ok") is not True:
            notes.append(
                "Hands, carried objects, or limb use were physically "
                "inconsistent between steps."
            )
        feedback = (
            "\n\nYour previous plan was rejected. Fix it. "
            + " ".join(notes)
        )
    if has_start_image:
        scene = IMAGE_SCENE
    plan = {
        "title": str(result.get("title", concept[:80])),
        "scene": scene,
        "human_subject": human_subject,
        "camera_view": camera_view,
        "orientation": orientation,
        "steps": steps,
        "result_states": result_states,
        "transitions": build_state_transitions(
            scene,
            steps,
            result_states,
            has_start_image=has_start_image,
        ),
        "check": check,
        "audit_attempts": attempt + 1,
        "shots": build_segment_prompts(
            scene,
            orientation,
            steps,
            result_states,
            shot_count,
            has_start_image,
            bool(continuation_context),
            human_subject,
        ),
    }
    if not ok:
        plan["rejected"] = True
        raise PlanAuditError(
            "工程監査が2回不合格になったため、動画生成を中止しました: "
            + json.dumps(check, ensure_ascii=False),
            plan,
        )
    return plan


def create_literal_plan(
    concept: str,
    shot_count: int,
    has_start_image: bool,
    is_continuation: bool = False,
    human_subject: bool = True,
) -> dict[str, Any]:
    if shot_count == 1:
        prompt = concept
        if is_continuation:
            prompt = (
                f"{CONTINUATION_SCENE} Action in the continuation: {concept}"
            )
        return {
            "title": concept[:80],
            "steps": [concept],
            "shots": [{"prompt": prompt, "steps": [1]}],
        }
    steps = split_concept_steps(concept)
    scene = IMAGE_SCENE if has_start_image else ""
    result_states = ["" for _ in steps]
    return {
        "title": concept[:80],
        "scene": scene,
        "orientation": DEFAULT_ORIENTATION,
        "steps": steps,
        "result_states": result_states,
        "shots": build_segment_prompts(
            scene,
            DEFAULT_ORIENTATION,
            steps,
            result_states,
            shot_count,
            has_start_image,
            is_continuation,
            human_subject,
        ),
    }


def create_audited_literal_plan(
    concept: str,
    shot_count: int,
    has_start_image: bool,
    continuation_context: str = "",
) -> dict[str, Any]:
    """Keep the user's generation text literal while still hard-auditing it."""

    audited = create_plan(
        concept,
        shot_count,
        has_start_image,
        continuation_context,
    )
    literal = create_literal_plan(
        concept,
        shot_count,
        has_start_image,
        bool(continuation_context),
        bool(audited.get("human_subject", True)),
    )
    literal.update(
        {
            "camera_view": audited["camera_view"],
            "human_subject": audited["human_subject"],
            "orientation": audited["orientation"],
            "result_states": audited["result_states"],
            "transitions": audited["transitions"],
            "check": audited["check"],
            "audit_attempts": audited["audit_attempts"],
            "audited_steps": audited["steps"],
        }
    )
    return literal


ANIMATE_VIEW_PROMPTS = {
    "front": (
        "The performer stays front-facing: face, chest, hips, knees, and feet "
        "point toward the camera together. The performer never turns around."
    ),
    "side": (
        "The performer stays in one natural side profile: face, shoulders, "
        "chest, hips, knees, and feet point in the same side direction. The "
        "performer never turns toward or away from the camera."
    ),
    "back": (
        "The performer stays back-facing: shoulders, back, hips, knees, and "
        "feet point away from the camera together. The face is never shown."
    ),
}


def selected_reference_view(row: sqlite3.Row) -> str:
    input_image = row["input_image"]
    for view, column in (
        ("side", "reference_side"),
        ("back", "reference_back"),
        ("front", "reference_front"),
    ):
        if row[column] and input_image == row[column]:
            return view
    return "front"


def create_motion_prompt(
    concept: str, steps: list[str], reference_view: str
) -> str:
    schema = {
        "type": "object",
        "required": ["motion_prompt"],
        "properties": {"motion_prompt": {"type": "string"}},
    }
    numbered = "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))
    result = ollama_structured(
        (
            "Extract and translate only the requested physical action into an "
            "English motion-reference prompt. Replace every character, animal, "
            "creature, age, gender, outfit, and appearance with one generic "
            "adult human performer. Do not copy the requested setting. The "
            "performer must remain centered and visible from head to toe in a "
            "plain evenly lit studio. Use a static wide camera with no cuts, "
            "pan, zoom, or close-up. Preserve the exact action and its speed, "
            "direction, repetition, and interaction. Keep every body movement "
            "from the ordered steps in the same order, joined with 'then'. "
            f"{ANIMATE_VIEW_PROMPTS[reference_view]} "
            "Return only the requested structured data."
        ),
        (
            "Create a neutral motion-reference prompt for this request. "
            f"The action is mandatory: {concept}\n\nOrdered steps:\n{numbered}"
        ),
        schema,
        temperature=0.1,
        num_predict=384,
    )
    motion_prompt = result.get("motion_prompt")
    if not isinstance(motion_prompt, str) or not motion_prompt.strip():
        raise RuntimeError("Ollama returned an invalid motion prompt")
    return motion_prompt.strip()


def free_comfy() -> None:
    if BACKEND_MANAGER.active_name == "comfy":
        BACKEND_MANAGER.release()
    else:
        COMFY_BACKEND.release()


def release_video_models_for_planning() -> None:
    """Unload every video backend before Ollama is allowed to load."""

    free_comfy()
    if BACKEND_MANAGER.active_name == "ltx":
        BACKEND_MANAGER.release()
    else:
        LTX_BACKEND.release()


def apply_quality_mode(
    workflow: dict[str, Any], config: dict[str, Any]
) -> None:
    for node_id, overrides in config.get("quality_overrides", {}).items():
        workflow[node_id]["inputs"].update(overrides)
    for node in workflow.values():
        if node.get("class_type") == "CreateVideo":
            node["inputs"].update(
                {
                    "bit_depth": 8,
                    "color_space": "sRGB",
                    "chroma_subsampling": "444",
                    "codec": "none",
                }
            )
    save_inputs = workflow[config["save_node"]]["inputs"]
    save_inputs.update(
        {
            "format": "mp4",
            "codec": "h264",
            "codec.encoding": "re-encode",
            "codec.encoding.crf": 0,
        }
    )


def queue_comfy(workflow: dict[str, Any]) -> str:
    response = request_json(
        f"{COMFY_URL}/prompt", {"prompt": workflow}, timeout=60
    )
    prompt_id = response.get("prompt_id")
    if not isinstance(prompt_id, str):
        raise RuntimeError(f"ComfyUI rejected workflow: {response}")
    return prompt_id


def wait_for_comfy(prompt_id: str, job_id: str) -> dict[str, Any]:
    started = time.monotonic()
    while time.monotonic() - started < COMFY_TIMEOUT:
        raise_if_cancelled(job_id)
        history = request_json(
            f"{COMFY_URL}/history/{prompt_id}", timeout=30
        ).get(prompt_id)
        if isinstance(history, dict):
            status = history.get("status")
            if isinstance(status, dict):
                status_str = status.get("status_str")
                if (
                    status.get("completed")
                    or status_str in COMFY_TERMINAL_STATUSES
                ):
                    if status_str != "success":
                        raise RuntimeError(
                            f"ComfyUI generation failed: {status}"
                        )
                    return history
        time.sleep(2)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} timed out")


def run_comfy(workflow: dict[str, Any], job_id: str) -> dict[str, Any]:
    """Run one workflow under the process-wide exclusive GPU lease."""

    return BACKEND_MANAGER.generate(
        "comfy",
        workflow,
        cancel_callback=lambda: raise_if_cancelled(job_id),
        timeout=COMFY_TIMEOUT,
    )


def run_ltx_native(
    job_id: str,
    row: sqlite3.Row,
    plan: dict[str, Any],
    *,
    attempt: int = 1,
    corrective_prompt: str = "",
) -> str:
    """Run one official ltx-pipelines profile through its isolated worker."""

    profile = str(row["generation_profile"])
    if profile not in {"ti2_hq", "dfr", "ti2_chunked"}:
        raise ValueError(f"Unknown native LTX profile: {profile}")
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)
    final_relative = (
        f"jobs/{job_id}/attempt_{attempt}_ltx_{profile}.mp4"
    )
    shot_prompts = [
        str(shot.get("prompt", "")).strip()
        for shot in plan.get("shots", [])
        if isinstance(shot, dict) and str(shot.get("prompt", "")).strip()
    ]
    prompt = " Continue directly without a cut. ".join(shot_prompts)
    if not prompt:
        prompt = str(row["concept"])
    if corrective_prompt:
        prompt += f" Mandatory quality correction: {corrective_prompt}"
    shots = int(row["shots"])
    request: dict[str, Any] = {
        "profile": profile,
        "input_path": row["input_image"] or None,
        "output_path": final_relative,
        "prompt": prompt,
        "negative": (
            None
            if profile == "dfr"
            else (
                "scene cut, identity change, duplicate object, unexplained "
                "shape change, text, watermark"
                + (
                    ", front-back body fusion, reversed torso, duplicate "
                    "limbs, extra body parts"
                    if plan.get("human_subject", True)
                    else ""
                )
            )
        ),
        "seed": (
            int(row["seed"]) + (attempt - 1) * 1_000_003
        ) & (2**63 - 1),
        "width": 1280 if row["quality_mode"] else 960,
        "height": 704 if row["quality_mode"] else 576,
        "frames": 120 * shots + 1,
        "fps": 24,
    }
    add_event(
        job_id,
        "generating",
        f"Generating with official LTX profile {profile}, attempt {attempt}",
    )
    response = BACKEND_MANAGER.generate(
        "ltx",
        request,
        cancel_callback=lambda: raise_if_cancelled(job_id),
        timeout=COMFY_TIMEOUT,
    )
    if response.get("status") != "succeeded":
        raise RuntimeError(f"LTX worker returned an invalid result: {response}")
    output_path = (OUTPUT_ROOT / final_relative).resolve()
    if (
        not output_path.is_relative_to(OUTPUT_ROOT.resolve())
        or not output_path.is_file()
        or output_path.stat().st_size <= 0
    ):
        raise RuntimeError("LTX worker did not create its declared MP4 output")
    BACKEND_MANAGER.release()
    return final_relative


def output_relative_path(
    history: dict[str, Any], save_node: str | None = None
) -> str:
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        raise RuntimeError("ComfyUI history contained no outputs")
    selected_outputs = (
        [outputs[save_node]]
        if save_node is not None and save_node in outputs
        else outputs.values()
    )
    for node_output in selected_outputs:
        if not isinstance(node_output, dict):
            continue
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                filename = item.get("filename")
                subfolder = item.get("subfolder", "")
                if (
                    isinstance(filename, str)
                    and filename.lower().endswith(".mp4")
                    and isinstance(subfolder, str)
                ):
                    relative = Path(subfolder) / filename
                    resolved = (OUTPUT_ROOT / relative).resolve()
                    if not resolved.is_relative_to(OUTPUT_ROOT.resolve()):
                        raise RuntimeError("ComfyUI returned an unsafe path")
                    return relative.as_posix()
    raise RuntimeError("ComfyUI history contained no MP4 output")


def docker_group_run(
    command: list[str], timeout: int = 300
) -> subprocess.CompletedProcess[str]:
    direct = os.access("/var/run/docker.sock", os.R_OK | os.W_OK)
    argv = command if direct else ["sg", "docker", "-c", shlex.join(command)]
    result = subprocess.run(
        argv,
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{shlex.join(command)} failed: {result.stderr or result.stdout}"
        )
    return result


def finish_wan(
    job_id: str,
    shot_paths: list[str],
    *,
    attempt: int,
) -> str:
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    final_name = f"final_attempt_{attempt}.mp4"
    final_relative = f"jobs/{job_id}/{final_name}"
    if len(shot_paths) == 1:
        docker_group_run(
            [
                str(PROJECT_ROOT / "scripts" / "strip-metadata.sh"),
                shot_paths[0],
                final_relative,
            ],
            timeout=300,
        )
        return final_relative

    concat_name = f"concat_attempt_{attempt}.txt"
    concat_path = job_dir / concat_name
    concat_path.write_text(
        "".join(f"file '{Path(path).name}'\n" for path in shot_paths)
    )
    container_dir = f"/opt/ComfyUI/output/jobs/{job_id}"
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            f"{container_dir}/{concat_name}",
            "-map_metadata",
            "-1",
            "-c",
            "copy",
            f"{container_dir}/{final_name}",
        ],
        timeout=600,
    )
    return final_relative


def extract_continuation_frame(
    job_id: str, segment_index: int, video_relative: str
) -> str:
    video_path = (OUTPUT_ROOT / video_relative).resolve()
    if (
        not video_path.is_relative_to(OUTPUT_ROOT.resolve())
        or not video_path.is_file()
        or video_path.suffix.lower() != ".mp4"
    ):
        raise ValueError("Continuation video escaped the output directory")
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)
    frame_relative = (
        f"jobs/{job_id}/continuation_{segment_index:02d}.png"
    )
    container_video = f"/opt/ComfyUI/output/{video_relative}"
    container_frame = f"/opt/ComfyUI/output/{frame_relative}"
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            "-2",
            "-i",
            container_video,
            "-map",
            "0:v:0",
            "-update",
            "1",
            "-y",
            container_frame,
        ],
        timeout=300,
    )
    input_name = f"continuation-{job_id}-{segment_index:02d}.png"
    shutil.copy2(OUTPUT_ROOT / frame_relative, INPUT_ROOT / input_name)
    return input_name


def analyze_temporal_state(contact_sheet: Path) -> dict[str, Any]:
    schema = {
        "type": "object",
        "required": [
            "subject_count",
            "orientation",
            "pose",
            "motion_direction",
            "motion_speed",
            "held_objects",
            "camera_state",
            "confidence",
        ],
        "properties": {
            "subject_count": {"type": "integer", "minimum": 0},
            "orientation": {"type": "string"},
            "pose": {"type": "string"},
            "motion_direction": {"type": "string"},
            "motion_speed": {"type": "string"},
            "held_objects": {"type": "array", "items": {"type": "string"}},
            "camera_state": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }
    return ollama_structured(
        (
            "You inspect an ordered contact sheet from the final second of a "
            "video. Describe only visible temporal state needed to continue "
            "the same shot. Frames run left-to-right, top-to-bottom. Do not "
            "invent identity, intent, or an action that is not visible. "
            "orientation must state front, back, left profile, right profile, "
            "mixed/unclear, or no person. motion_direction describes the "
            "observed direction through time. Return only the requested JSON."
        ),
        "Extract the final temporal state from this contact sheet.",
        schema,
        temperature=0.0,
        num_predict=384,
        images=[contact_sheet],
    )


def continuation_state_text(state: dict[str, Any]) -> str:
    held = ", ".join(
        str(item) for item in state.get("held_objects", []) if str(item)
    ) or "none visible"
    return (
        f"Visible temporal state: {state.get('subject_count', 0)} subject(s); "
        f"orientation {state.get('orientation', 'unclear')}; "
        f"pose {state.get('pose', 'unclear')}; motion "
        f"{state.get('motion_direction', 'unclear')} at "
        f"{state.get('motion_speed', 'unclear')} speed; held objects {held}; "
        f"camera {state.get('camera_state', 'unclear')}."
    )


def build_continuation_bundle(
    job_id: str,
    segment_index: int,
    video_relative: str,
    *,
    anchor_image: str | None = None,
) -> dict[str, Any]:
    video_path = (OUTPUT_ROOT / video_relative).resolve()
    if (
        not video_path.is_relative_to(OUTPUT_ROOT.resolve())
        or not video_path.is_file()
        or video_path.suffix.lower() != ".mp4"
    ):
        raise ValueError("Continuation video escaped the output directory")
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)
    stem = f"continuation_{segment_index:02d}"
    tail_relative = f"jobs/{job_id}/{stem}_tail.mp4"
    contact_relative = f"jobs/{job_id}/{stem}_contact.png"
    frames_relative = f"jobs/{job_id}/{stem}_frames"
    frames_dir = OUTPUT_ROOT / frames_relative
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.chmod(0o777)
    container_video = f"/opt/ComfyUI/output/{video_relative}"
    container_tail = f"/opt/ComfyUI/output/{tail_relative}"
    container_contact = f"/opt/ComfyUI/output/{contact_relative}"
    container_frames = f"/opt/ComfyUI/output/{frames_relative}"
    probe = docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,nb_frames:format=duration",
            "-of",
            "json",
            container_video,
        ],
        timeout=120,
    )
    probe_data = json.loads(probe.stdout)
    if not isinstance(probe_data, dict):
        raise RuntimeError("Continuation ffprobe returned a non-object")
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            "-1.1",
            "-i",
            container_video,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "12",
            "-preset",
            "veryfast",
            "-y",
            container_tail,
        ],
        timeout=300,
    )
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-sseof",
            "-1.1",
            "-i",
            container_video,
            "-vf",
            "fps=8,scale=320:-2",
            "-fps_mode",
            "passthrough",
            "-y",
            f"{container_frames}/frame_%02d.png",
        ],
        timeout=300,
    )
    terminal_frame_paths = sorted(frames_dir.glob("frame_*.png"))
    if not terminal_frame_paths:
        raise RuntimeError("No terminal continuation frames were extracted")
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-framerate",
            "8",
            "-i",
            f"{container_frames}/frame_%02d.png",
            "-vf",
            (
                "drawtext=text='T%{frame_num}':fontcolor=white:"
                "fontsize=18:box=1:boxcolor=black@0.65:x=6:y=6,"
                "tile=4x3:padding=4:margin=4"
            ),
            "-frames:v",
            "1",
            "-y",
            container_contact,
        ],
        timeout=300,
    )
    if anchor_image is None:
        anchor_image = extract_continuation_frame(
            job_id, segment_index, video_relative
        )
    temporal_state = analyze_temporal_state(OUTPUT_ROOT / contact_relative)
    raw_duration = (
        probe_data.get("format", {}).get("duration")
        if isinstance(probe_data.get("format"), dict)
        else None
    )
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError):
        duration = 0.0
    terminal_start = max(0.0, duration - 1.1)
    terminal_frames = [
        {
            "index": index,
            "path": str(path.relative_to(OUTPUT_ROOT)),
            "estimated_timestamp_seconds": round(
                min(duration, terminal_start + (index - 1) / 8),
                6,
            ),
        }
        for index, path in enumerate(terminal_frame_paths, start=1)
    ]
    bundle = {
        "version": 2,
        "source_video": video_relative,
        "anchor_image": anchor_image,
        "strict_final_frame": anchor_image,
        "tail_video": tail_relative,
        "contact_sheet": contact_relative,
        "terminal_window_seconds": 1.1,
        "terminal_sample_fps": 8,
        "terminal_frame_timestamps": "estimated_from_video_duration",
        "terminal_frames": terminal_frames,
        "probe": probe_data,
        "temporal_state": temporal_state,
        "temporal_state_text": continuation_state_text(temporal_state),
        "created_at": utc_now(),
    }
    bundle_path = job_dir / f"{stem}.json"
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n"
    )
    return bundle


def probe_output_video(video_relative: str) -> dict[str, Any]:
    result = docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            f"/opt/ComfyUI/output/{video_relative}",
        ],
        timeout=120,
    )
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, dict):
        raise RuntimeError("ffprobe returned a non-object")
    return parsed


def create_quality_contact_sheet(
    job_id: str,
    attempt: int,
    video_relative: str,
    probe: dict[str, Any],
) -> Path:
    raw_duration = (probe.get("format") or {}).get("duration")
    try:
        duration = max(0.1, float(raw_duration))
    except (TypeError, ValueError):
        duration = 5.0
    sample_rate = min(12.0, 12.0 / duration)
    relative = f"jobs/{job_id}/quality_attempt_{attempt}_contact.png"
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            f"/opt/ComfyUI/output/{video_relative}",
            "-vf",
            (
                f"fps={sample_rate:.8f},scale=320:-2,"
                "tile=4x3:padding=4:margin=4"
            ),
            "-frames:v",
            "1",
            "-y",
            f"/opt/ComfyUI/output/{relative}",
        ],
        timeout=300,
    )
    return OUTPUT_ROOT / relative


def extract_first_frame(
    job_id: str,
    attempt: int,
    video_relative: str,
) -> str:
    relative = f"jobs/{job_id}/quality_attempt_{attempt}_first.png"
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            f"/opt/ComfyUI/output/{video_relative}",
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            "-y",
            f"/opt/ComfyUI/output/{relative}",
        ],
        timeout=120,
    )
    return relative


def boundary_report(
    previous_input_image: str,
    next_frame_relative: str,
    *,
    continuation: bool,
) -> QualityReport:
    result = docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "info",
            "-i",
            f"/opt/ComfyUI/input/{previous_input_image}",
            "-i",
            f"/opt/ComfyUI/output/{next_frame_relative}",
            "-filter_complex",
            (
                "[0:v]scale=640:640:force_original_aspect_ratio=decrease,"
                "pad=640:640:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
                "split=2[a1][a2];"
                "[1:v]scale=640:640:force_original_aspect_ratio=decrease,"
                "pad=640:640:(ow-iw)/2:(oh-ih)/2,format=yuv420p,"
                "split=2[b1][b2];"
                "[a1][b1]ssim[ssim_out];[a2][b2]psnr[psnr_out]"
            ),
            "-map",
            "[ssim_out]",
            "-map",
            "[psnr_out]",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        timeout=120,
    )
    metrics = parse_boundary_metrics(f"{result.stdout}\n{result.stderr}")
    return evaluate_boundary_metrics(
        metrics.ssim,
        metrics.psnr,
        boundary_kind="continuation" if continuation else "segment",
    )


def temporal_quality_report(
    video_relative: str,
    *,
    duration: float | None,
) -> QualityReport:
    container_video = f"/opt/ComfyUI/output/{video_relative}"
    ffmpeg_command = build_temporal_analysis_command(container_video)
    result = docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            *ffmpeg_command,
        ],
        timeout=900,
    )
    findings = parse_temporal_findings(
        f"{result.stdout}\n{result.stderr}",
        video_duration=duration,
    )
    return evaluate_temporal_findings(findings)


def quality_corrective_prompt(report: QualityReport) -> str:
    corrections: list[str] = []
    for subreport in report.metrics.get("reports", []):
        if not isinstance(subreport, dict):
            continue
        metrics = subreport.get("metrics")
        if not isinstance(metrics, dict):
            continue
        audit = metrics.get("audit")
        if isinstance(audit, dict):
            value = audit.get("corrective_prompt")
            if isinstance(value, str) and value.strip():
                corrections.append(value.strip())
    if not corrections:
        corrections.extend(
            issue.message for issue in report.issues if issue.hard
        )
    return " ".join(dict.fromkeys(corrections))


def run_quality_gate(
    job_id: str,
    attempt: int,
    video_relative: str,
    concept: str,
    *,
    reference_image: str = "",
    boundary_image: str = "",
    continuation_boundary: bool = False,
) -> QualityReport:
    add_event(job_id, "validating", f"Validating generated attempt {attempt}")
    free_comfy()
    probe = probe_output_video(video_relative)
    technical = validate_probe_result(probe)
    duration_value = technical.metrics.get("duration")
    duration = (
        float(duration_value)
        if isinstance(duration_value, (int, float))
        else None
    )
    temporal = temporal_quality_report(
        video_relative,
        duration=duration,
    )
    contact_sheet = create_quality_contact_sheet(
        job_id,
        attempt,
        video_relative,
        probe,
    )
    image_paths: list[Path] = []
    if reference_image:
        reference_path = (INPUT_ROOT / reference_image).resolve()
        if reference_path.is_relative_to(INPUT_ROOT.resolve()) and (
            reference_path.is_file()
        ):
            image_paths.append(reference_path)
    image_paths.append(contact_sheet)
    audit = ollama_structured(
        (
            "You are a strict quality inspector for a locally generated "
            "general-audience video. The final supplied image is a contact "
            "sheet ordered left-to-right and top-to-bottom. If another image "
            "precedes it, that image is the identity and appearance reference. "
            "Reject anatomically impossible bodies, a face on the back of a "
            "head, front/back body fusion, reversed torso, duplicate limbs, "
            "unexplained identity or clothing changes, inconsistent body "
            "orientation, and failure to visibly perform the request. "
            "confidence measures confidence in the whole verdict. "
            "corrective_prompt must be a concise English generation constraint "
            "when any verdict is false, otherwise an empty string. Return only "
            "the requested JSON."
        ),
        f"Requested video action:\n{concept}",
        VISION_AUDIT_JSON_SCHEMA,
        temperature=0.0,
        num_predict=512,
        images=image_paths,
    )
    semantic_result = evaluate_vision_audit(audit)
    semantic = QualityReport(
        gate=semantic_result.gate,
        issues=semantic_result.issues,
        metrics={
            **semantic_result.metrics,
            "contact_sheet": str(contact_sheet.relative_to(OUTPUT_ROOT)),
            "reference_image": reference_image or None,
        },
    )
    reports = [technical, temporal, semantic]
    if boundary_image:
        first_frame = extract_first_frame(
            job_id,
            attempt,
            video_relative,
        )
        reports.append(
            boundary_report(
                boundary_image,
                first_frame,
                continuation=continuation_boundary,
            )
        )
    unload_ollama()
    return aggregate_reports(reports)


def quality_report_payload(
    report: QualityReport,
    *,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = report.to_dict()
    payload.update(
        {
            "passed": report.passed,
            "hard_failed": report.hard_failed,
            "attempts": attempts,
        }
    )
    return payload


def persist_quality_report(
    job_id: str,
    report: QualityReport,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = quality_report_payload(report, attempts=attempts)
    report_path = OUTPUT_ROOT / "jobs" / job_id / "quality-report.json"
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    with db_connect() as db:
        db.execute(
            """
            UPDATE jobs SET quality_json = ?, attempt = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(payload, ensure_ascii=False),
                max(
                    (
                        int(item.get("attempt", 0))
                        for item in attempts
                        if isinstance(item, dict)
                    ),
                    default=0,
                ),
                utc_now(),
                job_id,
            ),
        )
    return payload


def continuation_context(parent: sqlite3.Row) -> str:
    temporal_context = ""
    raw_continuation = parent["continuation_json"]
    if isinstance(raw_continuation, str) and raw_continuation:
        try:
            bundle = json.loads(raw_continuation)
        except json.JSONDecodeError:
            bundle = {}
        if isinstance(bundle, dict):
            value = bundle.get("temporal_state_text")
            if isinstance(value, str) and value.strip():
                temporal_context = value.strip()
    raw_plan = parent["plan_json"]
    if isinstance(raw_plan, str) and raw_plan:
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError:
            plan = {}
        states = plan.get("result_states") if isinstance(plan, dict) else None
        if isinstance(states, list):
            for state in reversed(states):
                if isinstance(state, str) and state.strip():
                    return " ".join(
                        part
                        for part in (state.strip(), temporal_context)
                        if part
                    )
    return temporal_context or (
        "Use the supplied final frame as the complete prior state."
    )


def persist_final_continuation_bundle(
    job_id: str,
    final_relative: str,
    segment_index: int,
) -> dict[str, Any]:
    add_event(
        job_id,
        "analyzing",
        "Capturing final frames and temporal state",
    )
    free_comfy()
    bundle = build_continuation_bundle(
        job_id,
        segment_index,
        final_relative,
    )
    unload_ollama()
    with db_connect() as db:
        db.execute(
            """
            UPDATE jobs SET continuation_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                json.dumps(bundle, ensure_ascii=False),
                utc_now(),
                job_id,
            ),
        )
    return bundle


def combine_continuation_videos(
    job_id: str,
    parent_relative: str,
    continuation_relative: str,
    *,
    attempt: int,
) -> str:
    output_root = OUTPUT_ROOT.resolve()
    for relative in (parent_relative, continuation_relative):
        resolved = (OUTPUT_ROOT / relative).resolve()
        if (
            not resolved.is_relative_to(output_root)
            or not resolved.is_file()
            or resolved.suffix.lower() != ".mp4"
            or "'" in relative
        ):
            raise ValueError("Continuation source is not a safe MP4 output")
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    concat_name = f"extend-concat-attempt-{attempt}.txt"
    concat_path = job_dir / concat_name
    concat_path.write_text(
        "".join(
            f"file '/opt/ComfyUI/output/{relative}'\n"
            for relative in (parent_relative, continuation_relative)
        )
    )
    extended_relative = (
        f"jobs/{job_id}/extended_attempt_{attempt}.mp4"
    )
    docker_group_run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "comfyui",
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            f"/opt/ComfyUI/output/jobs/{job_id}/{concat_name}",
            "-map_metadata",
            "-1",
            "-c",
            "copy",
            "-y",
            f"/opt/ComfyUI/output/{extended_relative}",
        ],
        timeout=600,
    )
    return extended_relative


def process_job(job_id: str) -> None:
    with db_connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return
    if row["status"] == "cancelled":
        with job_state_lock:
            cancelled_jobs.discard(job_id)
        return

    engine = row["engine"]
    try:
        raise_if_cancelled(job_id)
        config = ENGINE_CONFIG[engine]
        backend_id = row["backend_id"] or "comfy"
        parent: sqlite3.Row | None = None
        if row["continuation_of"]:
            with db_connect() as db:
                parent = db.execute(
                    "SELECT * FROM jobs WHERE id = ?",
                    (row["continuation_of"],),
                ).fetchone()
            if (
                parent is None
                or parent["status"] != "succeeded"
                or not parent["output_path"]
                or parent["engine"] != engine
            ):
                raise RuntimeError(
                    "The source video is unavailable for continuation"
                )
            if not parent["continuation_json"]:
                add_event(
                    job_id,
                    "analyzing",
                    "Analyzing the source video's final second",
                )
                source_bundle = build_continuation_bundle(
                    parent["id"],
                    99,
                    parent["output_path"],
                )
                unload_ollama()
                with db_connect() as db:
                    db.execute(
                        """
                        UPDATE jobs SET continuation_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(source_bundle, ensure_ascii=False),
                            utc_now(),
                            parent["id"],
                        ),
                    )
                    parent = db.execute(
                        "SELECT * FROM jobs WHERE id = ?",
                        (parent["id"],),
                    ).fetchone()
        if config["image_node"] is not None and row["input_image"]:
            input_path = (INPUT_ROOT / row["input_image"]).resolve()
            if not input_path.is_relative_to(INPUT_ROOT.resolve()):
                raise ValueError("Input image escaped the input directory")
            if not input_path.is_file():
                raise FileNotFoundError(
                    f"Input image does not exist: {input_path}"
                )
        elif config["image_node"] is not None and backend_id != "ltx":
            raise FileNotFoundError("This video engine requires an input image")
        if engine == "animate2" and row["driving_video"]:
            driving_path = (INPUT_ROOT / row["driving_video"]).resolve()
            if (
                not driving_path.is_relative_to(INPUT_ROOT.resolve())
                or not driving_path.is_file()
                or driving_path.suffix.lower() != ".mp4"
            ):
                raise FileNotFoundError("Driving video does not exist")

        release_video_models_for_planning()
        planning_message = (
            f"Planning with {OLLAMA_MODEL}"
            if row["enhance_prompt"]
            or engine == "animate2"
            else "Using the prompt exactly as entered"
        )
        add_event(job_id, "planning", planning_message)
        try:
            continuation_state = (
                continuation_context(parent) if parent is not None else ""
            )
            has_start_image = (
                bool(row["input_image"]) or parent is not None
            )
            try:
                if row["enhance_prompt"]:
                    plan = create_plan(
                        row["concept"],
                        row["shots"],
                        has_start_image,
                        continuation_state,
                    )
                else:
                    plan = create_audited_literal_plan(
                        row["concept"],
                        row["shots"],
                        has_start_image,
                        continuation_state,
                    )
            except PlanAuditError as error:
                rejected_plan = dict(error.plan)
                rejected_plan["rejected"] = True
                with db_connect() as db:
                    db.execute(
                        """
                        UPDATE jobs SET plan_json = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            json.dumps(rejected_plan, ensure_ascii=False),
                            utc_now(),
                            job_id,
                        ),
                    )
                add_event(job_id, "plan_rejected", str(error))
                raise
            if parent is not None:
                plan["continuation_of"] = parent["id"]
                plan["prior_final_state"] = continuation_state
            add_event(
                job_id,
                "steps",
                "工程: "
                + " → ".join(
                    f"{i}) {step}" for i, step in enumerate(plan["steps"], 1)
                ),
            )
            if engine == "animate2":
                reference_view = selected_reference_view(row)
                full_body_constraint = (
                    " Wide full-body composition; the entire character from "
                    "head to toe remains visible throughout. Static camera, "
                    "no cuts, no zoom, and no close-up. "
                    + ANIMATE_VIEW_PROMPTS[reference_view]
                )
                for shot in plan["shots"]:
                    shot["prompt"] = (
                        shot["prompt"].rstrip(" .") + "." + full_body_constraint
                    )
                plan["reference_view"] = reference_view
                plan["motion_prompt"] = create_motion_prompt(
                    row["concept"], plan["steps"], reference_view
                )
        finally:
            unload_ollama()
        raise_if_cancelled(job_id)
        with db_connect() as db:
            db.execute(
                "UPDATE jobs SET plan_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(plan, ensure_ascii=False), utc_now(), job_id),
            )

        if backend_id == "ltx":
            native_attempts: list[dict[str, Any]] = []
            corrective_prompt = ""
            native_report: QualityReport | None = None
            final_relative = ""
            for attempt in (1, 2):
                final_relative = run_ltx_native(
                    job_id,
                    row,
                    plan,
                    attempt=attempt,
                    corrective_prompt=corrective_prompt,
                )
                native_report = run_quality_gate(
                    job_id,
                    attempt,
                    final_relative,
                    str(row["concept"]),
                    reference_image=row["input_image"],
                )
                native_attempts.append(
                    {
                        "segment": 1,
                        "attempt": attempt,
                        "output": final_relative,
                        "report": native_report.to_dict(),
                    }
                )
                if native_report.passed:
                    break
                if should_retry(native_report, retries_used=attempt - 1):
                    corrective_prompt = quality_corrective_prompt(
                        native_report
                    )
                    add_event(
                        job_id,
                        "quality_retry",
                        "Quality gate rejected the first LTX attempt; "
                        "regenerating once",
                    )
                    continue
                persist_quality_report(
                    job_id,
                    native_report,
                    native_attempts,
                )
                raise QualityGateError(native_report)
            if native_report is None or not native_report.passed:
                raise RuntimeError("No native LTX output passed quality gates")
            persist_quality_report(
                job_id,
                native_report,
                native_attempts,
            )
            persist_final_continuation_bundle(
                job_id,
                final_relative,
                int(row["shots"]) + 1,
            )
            raise_if_cancelled(job_id)
            finish_job(job_id, "succeeded", output_path=final_relative)
            return

        request_json(f"{COMFY_URL}/system_stats", timeout=30)
        raise_if_cancelled(job_id)
        free_comfy()
        job_dir = OUTPUT_ROOT / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        # The hardened container has all Linux capabilities dropped. Its
        # process therefore needs ordinary DAC permission on this bind mount.
        job_dir.chmod(0o777)

        driving_video = row["driving_video"]
        if engine == "animate2" and not driving_video:
            add_event(job_id, "motion", "Generating motion guide from text")
            draft_config = ENGINE_CONFIG["wan5b"]
            draft = json.loads(draft_config["workflow"].read_text())
            draft[draft_config["prompt_node"]]["inputs"][
                draft_config["prompt_field"]
            ] = plan["motion_prompt"]
            draft[draft_config["seed_node"]]["inputs"][
                draft_config["seed_field"]
            ] = row["seed"]
            for node_id, overrides in draft_config["input_overrides"].items():
                draft[node_id]["inputs"].update(overrides)
            draft[draft_config["save_node"]]["inputs"]["filename_prefix"] = (
                f"jobs/{job_id}/motion_guide"
            )
            draft_history = run_comfy(draft, job_id)
            draft_relative = output_relative_path(
                draft_history, draft_config["save_node"]
            )
            driving_video = f"motion-guide-{job_id}.mp4"
            shutil.copy2(
                OUTPUT_ROOT / draft_relative,
                INPUT_ROOT / driving_video,
            )
            free_comfy()

        template = json.loads(config["workflow"].read_text())
        quality_attempts: list[dict[str, Any]] = []
        generation_profile = (
            row["generation_profile"]
            or ("quality" if engine == "wan14" else "comfy")
        )
        mode_label = "".join(
            [
                " in quality mode" if row["quality_mode"] else "",
                (
                    " with 20-step quality"
                    if engine == "wan14"
                    and generation_profile == "quality"
                    else ""
                ),
                (
                    " with 4-step fast LoRA"
                    if engine == "wan14" and generation_profile == "fast"
                    else ""
                ),
            ]
        )
        corrective_prompt = ""
        final_relative = ""
        final_quality: QualityReport | None = None
        final_shot_count = 0
        for attempt in (1, 2):
            shot_paths: list[str] = []
            quality_reports: list[QualityReport] = []
            temporal_context = (
                continuation_context(parent) if parent is not None else ""
            )
            continuation_image: str | None = (
                row["input_image"]
                if parent is not None and engine == "wan5b"
                else None
            )
            failed_report: QualityReport | None = None
            failed_stage = ""
            for index, shot in enumerate(plan["shots"], start=1):
                raise_if_cancelled(job_id)
                add_event(
                    job_id,
                    "generating",
                    f"Generating continuous segment {index}, attempt "
                    f"{attempt}{mode_label}",
                )
                workflow = copy.deepcopy(template)
                segment_prompt = shot["prompt"]
                if temporal_context:
                    segment_prompt = (
                        f"{segment_prompt} Continue the visible motion state "
                        f"without resetting: {temporal_context}"
                    )
                if corrective_prompt:
                    segment_prompt = (
                        f"{segment_prompt} Mandatory quality correction: "
                        f"{corrective_prompt}"
                    )
                workflow[config["prompt_node"]]["inputs"][
                    config["prompt_field"]
                ] = segment_prompt
                negative_node = config.get("negative_node")
                if negative_node is not None:
                    negative = (
                        workflow[negative_node]["inputs"]["text"].rstrip("，, ")
                        + GENERIC_CONTINUITY_NEGATIVE
                    )
                    if plan.get("human_subject", True):
                        negative += HUMAN_CONTINUITY_NEGATIVE
                    if corrective_prompt:
                        negative += (
                            "，do not repeat the prior detected quality defect"
                        )
                    workflow[negative_node]["inputs"]["text"] = negative
                pose_prompt_node = config.get("pose_prompt_node")
                if pose_prompt_node is not None:
                    motion_prompt = str(
                        plan.get("motion_prompt", shot["prompt"])
                    )
                    if corrective_prompt:
                        motion_prompt += (
                            f" Mandatory quality correction: "
                            f"{corrective_prompt}"
                        )
                    workflow[pose_prompt_node]["inputs"]["value"] = (
                        motion_prompt
                    )
                if continuation_image and engine == "wan5b":
                    continuation_load_node = "continuation_image"
                    workflow[continuation_load_node] = {
                        "class_type": "LoadImage",
                        "inputs": {"image": continuation_image},
                    }
                    workflow[config["continuation_node"]]["inputs"][
                        "start_image"
                    ] = [continuation_load_node, 0]
                elif config["image_node"] is not None:
                    workflow[config["image_node"]]["inputs"]["image"] = row[
                        "input_image"
                    ]
                if continuation_image and engine == "wan14":
                    workflow[config["image_node"]]["inputs"][
                        "image"
                    ] = continuation_image
                video_node = config.get("video_node")
                if video_node is not None:
                    workflow[video_node]["inputs"]["file"] = driving_video
                workflow[config["seed_node"]]["inputs"][
                    config["seed_field"]
                ] = (
                    int(row["seed"])
                    + index
                    - 1
                    + (attempt - 1) * 1_000_003
                ) & (2**63 - 1)
                for node_id, overrides in config["input_overrides"].items():
                    workflow[node_id]["inputs"].update(overrides)
                if row["quality_mode"]:
                    apply_quality_mode(workflow, config)
                if engine == "wan14":
                    profile_overrides = config["profile_overrides"].get(
                        generation_profile
                    )
                    if profile_overrides is None:
                        raise ValueError(
                            f"Unknown Wan 14B generation profile: "
                            f"{generation_profile}"
                        )
                    for node_id, overrides in profile_overrides.items():
                        workflow[node_id]["inputs"].update(overrides)
                workflow[config["save_node"]]["inputs"][
                    "filename_prefix"
                ] = (
                    f"jobs/{job_id}/attempt_{attempt}_shot_{index:02d}_"
                    f"{config['prefix']}"
                )
                history = run_comfy(workflow, job_id)
                shot_relative = output_relative_path(
                    history, config["save_node"]
                )
                boundary_image = continuation_image or row["input_image"] or ""
                report = run_quality_gate(
                    job_id,
                    (index - 1) * 2 + attempt,
                    shot_relative,
                    str(row["concept"]),
                    reference_image=row["input_image"],
                    boundary_image=boundary_image,
                    continuation_boundary=parent is not None and index == 1,
                )
                quality_attempts.append(
                    {
                        "kind": "segment",
                        "segment": index,
                        "attempt": attempt,
                        "output": shot_relative,
                        "report": report.to_dict(),
                    }
                )
                if report.passed:
                    quality_reports.append(report)
                    shot_paths.append(shot_relative)
                else:
                    failed_report = report
                    failed_stage = f"segment {index}"
                    break
                if (
                    index < len(plan["shots"])
                    and engine in {"wan5b", "wan14"}
                ):
                    add_event(
                        job_id,
                        "continuing",
                        f"Capturing segment {index} temporal context for "
                        f"segment {index + 1}",
                    )
                    free_comfy()
                    bundle = build_continuation_bundle(
                        job_id,
                        index,
                        shot_relative,
                    )
                    unload_ollama()
                    continuation_image = str(bundle["anchor_image"])
                    temporal_context = str(bundle["temporal_state_text"])

            if failed_report is None:
                raise_if_cancelled(job_id)
                add_event(job_id, "finishing", "Finalizing local MP4")
                if engine != "ltx25":
                    final_relative = finish_wan(
                        job_id,
                        shot_paths,
                        attempt=attempt,
                    )
                else:
                    # LTX AUP requires preserving provenance metadata.
                    final_relative = shot_paths[0]
                if parent is not None:
                    add_event(
                        job_id,
                        "extending",
                        "Combining the source video and its continuation",
                    )
                    final_relative = combine_continuation_videos(
                        job_id,
                        parent["output_path"],
                        final_relative,
                        attempt=attempt,
                    )
                final_report = run_quality_gate(
                    job_id,
                    attempt * 100 + 99,
                    final_relative,
                    str(row["concept"]),
                    reference_image=row["input_image"],
                )
                quality_attempts.append(
                    {
                        "kind": "final",
                        "segment": None,
                        "attempt": attempt,
                        "output": final_relative,
                        "report": final_report.to_dict(),
                    }
                )
                quality_reports.append(final_report)
                final_quality = aggregate_reports(
                    quality_reports,
                    gate="job",
                )
                if final_quality.passed:
                    final_shot_count = len(shot_paths)
                    break
                failed_report = final_quality
                failed_stage = "final output"

            if should_retry(failed_report, retries_used=attempt - 1):
                corrective_prompt = quality_corrective_prompt(failed_report)
                add_event(
                    job_id,
                    "quality_retry",
                    f"Quality gate rejected the {failed_stage}; "
                    "regenerating the complete job once with corrective "
                    "constraints",
                )
                continue
            rejected = (
                failed_report
                if failed_stage == "final output"
                else aggregate_reports(
                    [*quality_reports, failed_report],
                    gate="job",
                )
            )
            persist_quality_report(
                job_id,
                rejected,
                quality_attempts,
            )
            raise QualityGateError(failed_report)

        if not final_relative or final_quality is None:
            raise RuntimeError("No complete output passed the quality gates")
        persist_quality_report(
            job_id,
            final_quality,
            quality_attempts,
        )
        persist_final_continuation_bundle(
            job_id,
            final_relative,
            final_shot_count + 1,
        )
        raise_if_cancelled(job_id)
        finish_job(job_id, "succeeded", output_path=final_relative)
    except JobCancelled:
        pass
    except Exception as error:
        with job_state_lock:
            was_cancelled = job_id in cancelled_jobs
        if not was_cancelled:
            finish_job(
                job_id, "failed", error=f"{type(error).__name__}: {error}"
            )
    finally:
        try:
            unload_ollama()
        except Exception:
            pass
        try:
            BACKEND_MANAGER.release()
        except Exception:
            pass
        try:
            free_comfy()
        except Exception:
            pass
        with job_state_lock:
            cancelled_jobs.discard(job_id)


def worker_loop() -> None:
    global active_job_id
    while True:
        job_id = job_queue.get()
        try:
            if job_id is None:
                return
            with job_state_lock:
                active_job_id = job_id
            process_job(job_id)
        finally:
            with job_state_lock:
                active_job_id = None
            job_queue.task_done()


class JobCreate(BaseModel):
    concept: str = Field(min_length=10, max_length=2000)
    engine: Literal["wan5b", "wan14", "ltx25", "animate2"] = "wan5b"
    input_image: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$",
    )
    reference_front: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$",
    )
    reference_side: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$",
    )
    reference_back: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$",
    )
    driving_video: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.mp4$",
    )
    shots: int = Field(default=1, ge=1, le=3)
    seed: int = Field(
        default_factory=lambda: int.from_bytes(os.urandom(8), "big")
        & (2**63 - 1),
        ge=0,
        le=2**63 - 1,
    )
    enhance_prompt: bool = True
    quality_mode: bool = False
    precision_mode: bool = False
    generation_profile: Literal[
        "quality",
        "fast",
        "comfy",
        "ti2_hq",
        "dfr",
        "ti2_chunked",
    ] | None = None
    continuation_of: str | None = Field(
        default=None,
        pattern=(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        ),
    )
    ltx_license_acknowledged: bool = False

    @model_validator(mode="after")
    def validate_ltx(self) -> "JobCreate":
        if self.continuation_of and self.engine not in {"wan5b", "wan14"}:
            raise ValueError(
                "Continuation is currently available for Wan 5B and Wan 14B"
            )
        if self.precision_mode and self.engine != "wan14":
            raise ValueError("precision_mode is only available for Wan 14B")
        if self.engine == "wan14" and self.generation_profile not in {
            None,
            "quality",
            "fast",
        }:
            raise ValueError(
                "Wan 14B generation_profile must be quality or fast"
            )
        if self.engine == "ltx25" and self.generation_profile not in {
            None,
            "comfy",
            "ti2_hq",
            "dfr",
            "ti2_chunked",
        }:
            raise ValueError("Invalid LTX-2.5 generation_profile")
        if self.engine not in {"wan14", "ltx25"} and (
            self.generation_profile not in {None, "comfy"}
        ):
            raise ValueError(
                "generation_profile is not available for this engine"
            )
        image_required = self.engine in {"wan14", "animate2"} or (
            self.engine == "ltx25"
            and self.generation_profile in {None, "comfy"}
        )
        if image_required and not self.input_image and not self.continuation_of:
            raise ValueError(
                "An input image is required for image-based video engines"
            )
        if self.engine == "animate2" and self.shots != 1:
            raise ValueError("Wan Animate 2 jobs are limited to one shot")
        if self.engine == "ltx25":
            if not self.ltx_license_acknowledged:
                raise ValueError(
                    "ltx_license_acknowledged must be true for LTX-2.5"
                )
            if (
                self.generation_profile != "ti2_chunked"
                and self.shots != 1
            ):
                raise ValueError(
                    "This LTX-2.5 profile is limited to one shot"
                )
        return self


def resolve_generation_profile(request: JobCreate) -> tuple[str, str]:
    """Return the persisted profile and its model backend."""

    if request.engine == "wan14":
        profile = request.generation_profile or "quality"
    elif request.engine == "ltx25":
        profile = request.generation_profile or "comfy"
    else:
        profile = "comfy"
    backend = (
        "ltx"
        if request.engine == "ltx25" and profile != "comfy"
        else "comfy"
    )
    return profile, backend


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for source, target in (
        ("plan_json", "plan"),
        ("quality_json", "quality"),
        ("continuation_json", "continuation"),
    ):
        if result.get(source):
            try:
                result[target] = json.loads(result.pop(source))
            except json.JSONDecodeError:
                result.pop(source, None)
                result[target] = None
        else:
            result.pop(source, None)
            result[target] = None
    output_path = result.get("output_path")
    result["output_url"] = (
        f"/outputs/{quote(output_path, safe='/')}"
        if isinstance(output_path, str)
        else None
    )
    return result


@asynccontextmanager
async def lifespan(_: FastAPI):
    global worker_thread
    init_db()
    worker_thread = threading.Thread(target=worker_loop, daemon=True)
    worker_thread.start()
    yield
    job_queue.put(None)
    worker_thread.join(timeout=10)
    try:
        BACKEND_MANAGER.close()
    except Exception:
        pass


app = FastAPI(
    title="Unbound Local Video Automation",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/assets/logo.svg", include_in_schema=False)
def logo() -> FileResponse:
    return FileResponse(
        STATIC_ROOT / "logo.svg", media_type="image/svg+xml"
    )


@app.get("/", response_class=HTMLResponse)
def web_app() -> HTMLResponse:
    return HTMLResponse((STATIC_ROOT / "index.html").read_text())


@app.get("/inputs")
def list_inputs() -> list[str]:
    return sorted(
        path.name
        for path in INPUT_ROOT.iterdir()
        if (
            path.is_file()
            and path.suffix.lower() in ALLOWED_IMAGE_SUFFIXES
            and not path.name.startswith("continuation-")
        )
    )


@app.post("/inputs", status_code=201)
async def upload_input(file: UploadFile = File(...)) -> dict[str, str]:
    original = Path(file.filename or "input")
    suffix = original.suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise HTTPException(
            status_code=415, detail="PNG, JPEG, or WebP images only"
        )
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", original.stem).strip("-")
    safe_stem = (safe_stem or "input")[:64]
    filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}{suffix}"
    destination = INPUT_ROOT / filename
    total = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Image exceeds 20 MB"
                    )
                output.write(chunk)
        if not image_signature_matches(destination, suffix):
            raise HTTPException(
                status_code=415,
                detail="File content does not match its image extension",
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"filename": filename}


@app.post("/input-videos", status_code=201)
async def upload_input_video(file: UploadFile = File(...)) -> dict[str, str]:
    original = Path(file.filename or "motion.mp4")
    suffix = original.suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail="MP4 videos only")
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "-", original.stem).strip("-")
    safe_stem = (safe_stem or "motion")[:64]
    filename = f"{safe_stem}-{uuid.uuid4().hex[:8]}.mp4"
    destination = INPUT_ROOT / filename
    total = 0
    try:
        with destination.open("xb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_VIDEO_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Video exceeds 250 MB"
                    )
                output.write(chunk)
        if not video_signature_matches(destination):
            raise HTTPException(
                status_code=415, detail="File content is not an MP4 video"
            )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return {"filename": filename}


@app.get("/inputs/{filename}")
def get_input(filename: str) -> FileResponse:
    if Path(filename).name != filename:
        raise HTTPException(status_code=404, detail="Input not found")
    path = (INPUT_ROOT / filename).resolve()
    if (
        not path.is_relative_to(INPUT_ROOT.resolve())
        or not path.is_file()
        or path.suffix.lower() not in ALLOWED_IMAGE_SUFFIXES
    ):
        raise HTTPException(status_code=404, detail="Input not found")
    return FileResponse(path)


@app.get("/outputs/{output_path:path}")
def get_output(output_path: str) -> FileResponse:
    path = (OUTPUT_ROOT / output_path).resolve()
    if (
        not path.is_relative_to(OUTPUT_ROOT.resolve())
        or not path.is_file()
        or path.suffix.lower() != ".mp4"
    ):
        raise HTTPException(status_code=404, detail="Output not found")
    with db_connect() as db:
        published = db.execute(
            """
            SELECT 1 FROM jobs
            WHERE status = 'succeeded' AND output_path = ?
            LIMIT 1
            """,
            (output_path,),
        ).fetchone()
    if published is None:
        raise HTTPException(status_code=404, detail="Output not published")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/health")
def health() -> dict[str, Any]:
    result: dict[str, Any] = {"service": "ok"}
    for name, url in (
        ("ollama", f"{OLLAMA_URL}/api/version"),
        ("comfyui", f"{COMFY_URL}/system_stats"),
        ("ltx", f"{LTX_WORKER_URL}/health"),
    ):
        try:
            result[name] = {"ok": True, "detail": request_json(url, timeout=5)}
        except Exception as error:
            result[name] = {"ok": False, "detail": str(error)}
    return result


@app.post("/jobs", status_code=202)
def create_job(request: JobCreate) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    now = utc_now()
    continuation_of = request.continuation_of or ""
    input_image = request.input_image or ""
    quality_mode = int(request.quality_mode)
    generation_profile, backend_id = resolve_generation_profile(request)
    precision_mode = int(
        request.engine == "wan14" and generation_profile == "quality"
    )
    queued_message = "Job accepted"
    if continuation_of:
        with db_connect() as db:
            parent = db.execute(
                "SELECT * FROM jobs WHERE id = ?", (continuation_of,)
            ).fetchone()
        if parent is None:
            raise HTTPException(
                status_code=404, detail="Continuation source job not found"
            )
        if parent["status"] != "succeeded" or not parent["output_path"]:
            raise HTTPException(
                status_code=409,
                detail="Only a completed video can be continued",
            )
        if parent["engine"] != request.engine:
            raise HTTPException(
                status_code=409,
                detail="A continuation must use the same video engine",
            )
        quality_mode = int(parent["quality_mode"])
        precision_mode = int(parent["precision_mode"])
        generation_profile = parent["generation_profile"] or (
            "quality" if precision_mode else "fast"
        )
        backend_id = parent["backend_id"] or "comfy"
        try:
            input_image = extract_continuation_frame(
                job_id, 0, parent["output_path"]
            )
        except Exception as error:
            shutil.rmtree(
                OUTPUT_ROOT / "jobs" / job_id, ignore_errors=True
            )
            (INPUT_ROOT / f"continuation-{job_id}-00.png").unlink(
                missing_ok=True
            )
            raise HTTPException(
                status_code=409,
                detail=f"Could not read the source video's final frame: {error}",
            ) from error
        queued_message = f"Continuation of {continuation_of} accepted"
    try:
        with db_connect() as db:
            db.execute(
                """
                INSERT INTO jobs(
                    id, status, engine, concept, input_image, reference_front,
                    reference_side, reference_back, driving_video, shots, seed,
                    enhance_prompt, quality_mode, precision_mode,
                    backend_id, generation_profile, attempt,
                    continuation_of, created_at, updated_at
                ) VALUES (
                    ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0,
                    ?, ?, ?
                )
                """,
                (
                    job_id,
                    request.engine,
                    request.concept,
                    input_image,
                    request.reference_front or "",
                    request.reference_side or "",
                    request.reference_back or "",
                    request.driving_video or "",
                    request.shots,
                    request.seed,
                    int(request.enhance_prompt),
                    quality_mode,
                    precision_mode,
                    backend_id,
                    generation_profile,
                    continuation_of,
                    now,
                    now,
                ),
            )
            db.execute(
                """
                INSERT INTO events(job_id, stage, message, created_at)
                VALUES (?, 'queued', ?, ?)
                """,
                (job_id, queued_message, now),
            )
    except Exception:
        if continuation_of:
            shutil.rmtree(
                OUTPUT_ROOT / "jobs" / job_id, ignore_errors=True
            )
            (INPUT_ROOT / f"continuation-{job_id}-00.png").unlink(
                missing_ok=True
            )
        raise
    job_queue.put(job_id)
    return {
        "id": job_id,
        "status": "queued",
        "continuation_of": continuation_of or None,
    }


@app.get("/jobs")
def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    with db_connect() as db:
        rows = db.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [row_to_job(row) for row in rows]


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    with db_connect() as db:
        row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return row_to_job(row)


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, str]:
    global active_job_id
    with db_connect() as db:
        row = db.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if row["status"] in {"succeeded", "failed", "cancelled"}:
            return {"id": job_id, "status": row["status"]}
        with job_state_lock:
            cancelled_jobs.add(job_id)
            should_interrupt = active_job_id == job_id
        now = utc_now()
        db.execute(
            """
            UPDATE jobs SET status = 'cancelled', error = ?,
                updated_at = ? WHERE id = ?
            """,
            ("Cancelled by user", now, job_id),
        )
        db.execute(
            """
            INSERT INTO events(job_id, stage, message, created_at)
            VALUES (?, 'cancelled', 'Cancelled by user', ?)
            """,
            (job_id, now),
        )
    if should_interrupt:
        try:
            if not BACKEND_MANAGER.cancel():
                COMFY_BACKEND.cancel()
        except Exception:
            pass
    return {"id": job_id, "status": "cancelled"}


def remove_job_files(job_id: str) -> None:
    jobs_root = (OUTPUT_ROOT / "jobs").resolve()
    job_dir = (jobs_root / job_id).resolve()
    if not job_dir.is_relative_to(jobs_root):
        raise ValueError("Job directory escaped the output directory")
    if job_dir.exists():
        try:
            shutil.rmtree(job_dir)
        except PermissionError:
            docker_group_run(
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "comfyui",
                    "rm",
                    "-rf",
                    "--",
                    f"/opt/ComfyUI/output/jobs/{job_id}",
                ],
                timeout=120,
            )
    for pattern in (
        f"continuation-{job_id}-*.png",
        f"motion-guide-{job_id}.mp4",
    ):
        for path in INPUT_ROOT.glob(pattern):
            path.unlink(missing_ok=True)


@app.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict[str, str]:
    try:
        uuid.UUID(job_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Job not found") from error
    with db_connect() as db:
        row = db.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        active_continuation = db.execute(
            """
            SELECT id FROM jobs
            WHERE continuation_of = ?
              AND status NOT IN ('succeeded', 'failed', 'cancelled')
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] not in {"succeeded", "failed", "cancelled"}:
        raise HTTPException(
            status_code=409, detail="Cancel the job before deleting it"
        )
    if active_continuation is not None:
        raise HTTPException(
            status_code=409,
            detail="Wait for or cancel the active continuation before "
            "deleting its source video",
        )
    remove_job_files(job_id)
    with db_connect() as db:
        db.execute("DELETE FROM events WHERE job_id = ?", (job_id,))
        db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    return {"id": job_id, "status": "deleted"}


@app.get("/jobs/{job_id}/events")
def get_job_events(job_id: str) -> list[dict[str, Any]]:
    with db_connect() as db:
        exists = db.execute(
            "SELECT 1 FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Job not found")
        rows = db.execute(
            """
            SELECT stage, message, created_at FROM events
            WHERE job_id = ? ORDER BY id
            """,
            (job_id,),
        ).fetchall()
    return [dict(row) for row in rows]
