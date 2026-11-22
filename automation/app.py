from __future__ import annotations

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
        "precision_overrides": {
            "129:131": {"value": False},
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
    "zimage": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "image_z_image_turbo.api.json",
        "prompt_node": "27",
        "prompt_field": "text",
        "image_node": None,
        "size_node": "13",
        "seed_node": "3",
        "seed_field": "seed",
        "save_node": "9",
        "prefix": "zimage",
    },
    "outfit": {
        "workflow": PROJECT_ROOT
        / "workflows"
        / "image_flux2_klein_outfit_edit.api.json",
        "prompt_node": "109",
        "prompt_field": "text",
        "image_node": "76",
        "garment_node": "81",
        "seed_node": "106",
        "seed_field": "noise_seed",
        "save_node": "9",
        "prefix": "outfit",
    },
}
VIDEO_ENGINES = ("wan5b", "wan14", "ltx25", "animate2")
IMAGE_ENGINES = ("zimage", "outfit")
OUTFIT_EDIT_INSTRUCTION = (
    "Dress the person in image 1 in the outfit shown in image 2. Replace "
    "only their clothing with the garments from image 2, reproducing the "
    "garments' colors, fabric, patterns, and fit. Keep the person's face, "
    "hairstyle, skin tone, body shape, pose, framing, lighting, and the "
    "background from image 1 unchanged. The result must show exactly one "
    "person and must not include the mannequin, hanger, or background of "
    "image 2."
)
IMAGE_SIZES: dict[str, tuple[int, int]] = {
    "square": (1024, 1024),
    "landscape": (1280, 720),
    "portrait": (720, 1280),
}

COMFY_TERMINAL_STATUSES = {"success", "error"}
job_queue: queue.Queue[str | None] = queue.Queue()
worker_thread: threading.Thread | None = None
job_state_lock = threading.Lock()
cancelled_jobs: set[str] = set()
active_job_id: str | None = None


class JobCancelled(Exception):
    pass


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
                continuation_of TEXT NOT NULL DEFAULT '',
                image_size TEXT NOT NULL DEFAULT '',
                garment_image TEXT NOT NULL DEFAULT '',
                plan_json TEXT,
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
        if "image_size" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN image_size TEXT NOT NULL DEFAULT ''
                """
            )
        if "garment_image" not in columns:
            db.execute(
                """
                ALTER TABLE jobs
                ADD COLUMN garment_image TEXT NOT NULL DEFAULT ''
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
OLLAMA_STRUCTURED_ATTEMPTS = 3
# GB10 cuBLAS aborts MoE prefill for some prompt shapes at larger ubatches
# (llama.cpp #28377); 256 avoids every reported case.
OLLAMA_NUM_BATCH = int(os.environ.get("OLLAMA_NUM_BATCH", "256"))
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
) -> dict[str, Any]:
    last_error = ""
    for attempt in range(OLLAMA_STRUCTURED_ATTEMPTS):
        payload = {
            "model": OLLAMA_MODEL,
            "system": system,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": temperature + 0.15 * attempt,
                "num_predict": min(num_predict * 2**attempt, 4096),
                "num_batch": OLLAMA_NUM_BATCH,
            },
        }
        try:
            response = request_json(
                f"{OLLAMA_URL}/api/generate", payload, timeout=1800
            )
        except RuntimeError as error:
            if "CUDA error" not in str(error):
                raise
            last_error = str(error)
            continue
        raw = response.get("response")
        if not isinstance(raw, str):
            last_error = "Ollama response did not contain a response string"
            continue
        if response.get("done_reason") == "length":
            last_error = (
                f"Ollama output hit the {payload['options']['num_predict']}"
                "-token limit before the JSON was complete"
            )
            continue
        try:
            result = json.loads(raw)
        except json.JSONDecodeError as error:
            last_error = f"Ollama returned malformed JSON: {error}"
            continue
        if isinstance(result, dict):
            return result
        last_error = "Ollama returned a non-object response"
    raise RuntimeError(
        f"{last_error} (after {OLLAMA_STRUCTURED_ATTEMPTS} attempts)"
    )


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
DEFAULT_ORIENTATION = (
    "Keep each subject in one natural, consistent orientation to the camera"
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


def build_segment_prompts(
    scene: str,
    orientation: str,
    steps: list[str],
    result_states: list[str],
    shot_count: int,
    has_start_image: bool,
    is_continuation: bool,
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
        parts.append(
            f"Natural subject orientation: "
            f"{orientation.rstrip(' .') or DEFAULT_ORIENTATION}."
        )
        parts.append(ANATOMY_PROMPT)
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
        parts.append(
            "No cut, transition, reset, sudden zoom, duplicate body part, "
            "or unrelated action."
        )
        shots.append(
            {
                "prompt": " ".join(parts),
                "steps": [i + 1 for i in step_indices],
            }
        )
    return shots


PLAN_SYSTEM = (
    "You convert a user's video request into structured data for a local "
    "image-to-video model. Write English. Return only the requested data.\n"
    "scene: who and what is visible at the very start, before the first "
    "action happens: subject count, appearance, clothing, objects, setting, "
    "lighting, and a camera framing that keeps every subject fully visible. "
    "Use a static medium-wide view. Describe the initial state, never the "
    "result of a later action.\n"
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
            "missing": {
                "type": "array",
                "maxItems": MAX_PLAN_STEPS,
                "items": {"type": "string"},
            },
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
            "You audit a video plan against the user's request. List in "
            "missing every action, change, or required ordering from the "
            "request that the steps omit or weaken; return an empty list "
            "when nothing is missing. order_ok is false if the steps are "
            "in a different order than requested. scene_shows_initial_state "
            "is false only if the scene already shows a change that one of "
            "the steps must cause on screen, such as an object already "
            "moved, removed, added, or transformed. A scene that matches a "
            "static starting pose named in step 1 is a valid initial state. "
            "state_sequence_ok is false if a result state does not follow "
            "from its action, anticipates a later action, or forgets a prior "
            "object or pose change. For example, if a step only grasps an "
            "object but its state says the object was removed, return false. "
            "If a person holds an object and a later state omits it without "
            "a release action, return false. "
            "orientation_coherent is false if the orientation could mix the "
            "front and back of a body, reverse a torso, or require an "
            "anatomically impossible twist. physical_consistency_ok is false "
            "if a later action tries to use an occupied hand without freeing "
            "it, changes handedness without instruction, loses an object, or "
            "requires an impossible limb arrangement. "
            "Return only the requested data."
        ),
        (
            f"Request:\n{concept}\n\nScene:\n{scene}\n\nOrientation:\n"
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
CONTINUITY_NEGATIVE = (
    "，镜头切换，转场，突然变焦，换脸，人物外貌突变，身体前后融合，"
    "躯干反转，头部反转，关节扭曲，多余肢体，"
    "scene cut, transition, sudden zoom, face change, front-back body fusion, "
    "face on back of head, reversed torso, mismatched body direction, twisted "
    "waist, 180-degree head twist, duplicate limbs, extra body parts"
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
            "camera_view",
            "steps",
            "result_states",
        ],
        "properties": {
            "title": {"type": "string"},
            "scene": {"type": "string"},
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
    for attempt in range(2):
        result = ollama_structured(
            PLAN_SYSTEM,
            request + feedback,
            schema,
            temperature=0.2 if attempt == 0 else 0.1,
            num_predict=1024,
        )
        scene = str(result.get("scene", "")).strip()
        camera_view = str(result.get("camera_view", "")).strip()
        if camera_view not in CAMERA_VIEW_ORIENTATIONS:
            raise RuntimeError("Ollama returned an invalid camera view")
        orientation = (
            IMAGE_ORIENTATION
            if has_start_image
            else CAMERA_VIEW_ORIENTATIONS[camera_view]
        )
        steps = clean_steps(result.get("steps"))
        result_states = clean_result_states(
            result.get("result_states"), len(steps)
        )
        if fixed_count and len(steps) != len(clauses):
            raise RuntimeError("Ollama changed the number of requested steps")
        try:
            check = plan_problems(
                concept, scene, orientation, steps, result_states
            )
        except RuntimeError as error:
            check = {"audit_error": str(error)}
            break
        missing = [
            item for item in check.get("missing", []) if isinstance(item, str)
        ]
        if has_start_image:
            check["scene_shows_initial_state"] = True
        ok = (
            not missing
            and check.get("order_ok") is True
            and check.get("scene_shows_initial_state") is True
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
        if check.get("scene_shows_initial_state") is not True:
            notes.append(
                "The scene described a later result instead of the "
                "initial state."
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
    return {
        "title": str(result.get("title", concept[:80])),
        "scene": scene,
        "camera_view": camera_view,
        "orientation": orientation,
        "steps": steps,
        "result_states": result_states,
        "check": check,
        "shots": build_segment_prompts(
            scene,
            orientation,
            steps,
            result_states,
            shot_count,
            has_start_image,
            bool(continuation_context),
        ),
    }


def create_literal_plan(
    concept: str,
    shot_count: int,
    has_start_image: bool,
    is_continuation: bool = False,
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
        ),
    }


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


IMAGE_PROMPT_SYSTEM = (
    "You convert a user's request into one English prompt for a local "
    "text-to-image model. Return only the requested data.\n"
    "prompt: one detailed paragraph describing a single still image. Keep "
    "every requested subject, subject count, appearance, clothing, object, "
    "setting, lighting, and art style. Never add people, animals, objects, or "
    "text that the user did not request, and never soften or drop a "
    "requested detail. The image will be the first frame of a video, so show "
    "every subject fully inside the frame in a natural, stable pose before "
    "any action happens, with clear space around them, sharp focus, and "
    "coherent anatomy. Do not describe motion blur, cuts, or text overlays."
)


def create_image_prompt(concept: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "required": ["title", "prompt"],
        "properties": {
            "title": {"type": "string"},
            "prompt": {"type": "string"},
        },
    }
    result = ollama_structured(
        IMAGE_PROMPT_SYSTEM,
        "Treat the following request as mandatory constraints, not "
        f"inspiration:\n{concept}",
        schema,
        temperature=0.2,
        num_predict=768,
    )
    prompt = result.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise RuntimeError("Ollama returned an invalid image prompt")
    return {
        "title": str(result.get("title") or concept[:80]).strip(),
        "prompt": prompt.strip(),
    }


OUTFIT_NOTE_SYSTEM = (
    "You translate a user's note about an outfit-swap image edit into one "
    "short English instruction. Image 1 is the person and image 2 is the "
    "outfit. Keep every requested detail, add nothing the user did not ask "
    "for, and return only the requested data."
)


def create_outfit_prompt(notes: str, enhance: bool) -> dict[str, Any]:
    notes = notes.strip()
    if notes and enhance:
        result = ollama_structured(
            OUTFIT_NOTE_SYSTEM,
            f"User note:\n{notes}",
            {
                "type": "object",
                "required": ["instruction"],
                "properties": {"instruction": {"type": "string"}},
            },
            temperature=0.2,
            num_predict=384,
        )
        translated = result.get("instruction")
        if not isinstance(translated, str) or not translated.strip():
            raise RuntimeError("Ollama returned an invalid outfit note")
        notes = translated.strip()
    prompt = OUTFIT_EDIT_INSTRUCTION
    if notes:
        prompt = f"{prompt} Additional request: {notes}"
    return {"title": "Outfit edit", "prompt": prompt}


def free_comfy() -> None:
    request_json(
        f"{COMFY_URL}/free",
        {"unload_models": True, "free_memory": True},
        timeout=120,
    )


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


def output_relative_path(
    history: dict[str, Any],
    save_node: str | None = None,
    suffix: str = ".mp4",
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
                    and filename.lower().endswith(suffix)
                    and isinstance(subfolder, str)
                ):
                    relative = Path(subfolder) / filename
                    resolved = (OUTPUT_ROOT / relative).resolve()
                    if not resolved.is_relative_to(OUTPUT_ROOT.resolve()):
                        raise RuntimeError("ComfyUI returned an unsafe path")
                    return relative.as_posix()
    raise RuntimeError(
        f"ComfyUI history contained no {suffix.lstrip('.').upper()} output"
    )


def docker_group_run(command: list[str], timeout: int = 300) -> None:
    result = subprocess.run(
        ["sg", "docker", "-c", shlex.join(command)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(
            f"{shlex.join(command)} failed: {result.stderr or result.stdout}"
        )


def finish_wan(job_id: str, shot_paths: list[str]) -> str:
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    final_relative = f"jobs/{job_id}/final.mp4"
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

    concat_path = job_dir / "concat.txt"
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
            f"{container_dir}/concat.txt",
            "-map_metadata",
            "-1",
            "-c",
            "copy",
            f"{container_dir}/final.mp4",
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


def continuation_context(parent: sqlite3.Row) -> str:
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
                    return state.strip()
    return "Use the supplied final frame as the complete prior state."


def combine_continuation_videos(
    job_id: str,
    parent_relative: str,
    continuation_relative: str,
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
    concat_path = job_dir / "extend-concat.txt"
    concat_path.write_text(
        "".join(
            f"file '/opt/ComfyUI/output/{relative}'\n"
            for relative in (parent_relative, continuation_relative)
        )
    )
    extended_relative = f"jobs/{job_id}/extended.mp4"
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
            f"/opt/ComfyUI/output/jobs/{job_id}/extend-concat.txt",
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


def generated_input_name(job_id: str) -> str:
    return f"generated-{job_id}.png"


def resolved_input(filename: str, label: str) -> Path:
    path = (INPUT_ROOT / filename).resolve()
    if not path.is_relative_to(INPUT_ROOT.resolve()):
        raise ValueError(f"{label} escaped the input directory")
    if not filename or not path.is_file():
        raise FileNotFoundError(f"{label} does not exist: {filename}")
    return path


def generate_image(job_id: str, row: sqlite3.Row) -> str:
    engine = row["engine"]
    config = ENGINE_CONFIG[engine]
    is_outfit = engine == "outfit"
    if is_outfit:
        resolved_input(row["input_image"], "Person image")
        resolved_input(row["garment_image"], "Outfit image")
    use_ollama = bool(row["enhance_prompt"]) and (
        not is_outfit or bool(row["concept"].strip())
    )
    if use_ollama:
        add_event(job_id, "planning", f"Planning with {OLLAMA_MODEL}")
        try:
            plan = (
                create_outfit_prompt(row["concept"], True)
                if is_outfit
                else create_image_prompt(row["concept"])
            )
        finally:
            unload_ollama()
    else:
        add_event(job_id, "planning", "Using the prompt exactly as entered")
        plan = (
            create_outfit_prompt(row["concept"], False)
            if is_outfit
            else {"title": row["concept"][:80], "prompt": row["concept"]}
        )
    if is_outfit:
        plan.update(
            {
                "person_image": row["input_image"],
                "outfit_image": row["garment_image"],
            }
        )
        size_message = "Editing the outfit"
    else:
        width, height = IMAGE_SIZES[row["image_size"] or "square"]
        plan.update({"width": width, "height": height})
        size_message = f"Generating a {width}×{height} image"
    raise_if_cancelled(job_id)
    with db_connect() as db:
        db.execute(
            "UPDATE jobs SET plan_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(plan, ensure_ascii=False), utc_now(), job_id),
        )

    free_comfy()
    job_dir = OUTPUT_ROOT / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_dir.chmod(0o777)
    add_event(job_id, "generating", size_message)
    workflow = json.loads(config["workflow"].read_text())
    workflow[config["prompt_node"]]["inputs"][config["prompt_field"]] = plan[
        "prompt"
    ]
    workflow[config["seed_node"]]["inputs"][config["seed_field"]] = row["seed"]
    if is_outfit:
        workflow[config["image_node"]]["inputs"]["image"] = row["input_image"]
        workflow[config["garment_node"]]["inputs"]["image"] = row[
            "garment_image"
        ]
    else:
        workflow[config["size_node"]]["inputs"].update(
            {"width": plan["width"], "height": plan["height"]}
        )
    workflow[config["save_node"]]["inputs"]["filename_prefix"] = (
        f"jobs/{job_id}/image_{config['prefix']}"
    )
    history = wait_for_comfy(queue_comfy(workflow), job_id)
    raw_relative = output_relative_path(
        history, config["save_node"], suffix=".png"
    )

    raise_if_cancelled(job_id)
    add_event(job_id, "finishing", "Removing embedded workflow metadata")
    final_relative = f"jobs/{job_id}/image.png"
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
            f"/opt/ComfyUI/output/{raw_relative}",
            "-map_metadata",
            "-1",
            "-y",
            f"/opt/ComfyUI/output/{final_relative}",
        ],
        timeout=120,
    )
    shutil.copy2(
        OUTPUT_ROOT / final_relative, INPUT_ROOT / generated_input_name(job_id)
    )
    return final_relative


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
        if engine in IMAGE_ENGINES:
            final_relative = generate_image(job_id, row)
            raise_if_cancelled(job_id)
            finish_job(job_id, "succeeded", output_path=final_relative)
            return
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
        if config["image_node"] is not None:
            input_path = (INPUT_ROOT / row["input_image"]).resolve()
            if not input_path.is_relative_to(INPUT_ROOT.resolve()):
                raise ValueError("Input image escaped the input directory")
            if not input_path.is_file():
                raise FileNotFoundError(
                    f"Input image does not exist: {input_path}"
                )
        if engine == "animate2" and row["driving_video"]:
            driving_path = (INPUT_ROOT / row["driving_video"]).resolve()
            if (
                not driving_path.is_relative_to(INPUT_ROOT.resolve())
                or not driving_path.is_file()
                or driving_path.suffix.lower() != ".mp4"
            ):
                raise FileNotFoundError("Driving video does not exist")

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
                config["image_node"] is not None or parent is not None
            )
            if row["enhance_prompt"]:
                plan = create_plan(
                    row["concept"],
                    row["shots"],
                    has_start_image,
                    continuation_state,
                )
            else:
                plan = create_literal_plan(
                    row["concept"],
                    row["shots"],
                    has_start_image,
                    parent is not None,
                )
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
            check = plan.get("check") or {}
            if check and (
                check.get("missing")
                or check.get("order_ok") is not True
                or check.get("scene_shows_initial_state") is not True
                or check.get("state_sequence_ok") is not True
                or check.get("orientation_coherent") is not True
                or check.get("physical_consistency_ok") is not True
            ):
                add_event(
                    job_id,
                    "plan_warning",
                    "工程チェックで未解決の指摘があります: "
                    + json.dumps(check, ensure_ascii=False),
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
            draft_prompt_id = queue_comfy(draft)
            draft_history = wait_for_comfy(draft_prompt_id, job_id)
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
        shot_paths: list[str] = []
        continuation_image: str | None = (
            row["input_image"]
            if parent is not None and engine == "wan5b"
            else None
        )

        for index, shot in enumerate(plan["shots"], start=1):
            raise_if_cancelled(job_id)
            mode_label = "".join(
                [
                    " in quality mode" if row["quality_mode"] else "",
                    " with 20-step precision" if row["precision_mode"] else "",
                ]
            )
            add_event(
                job_id,
                "generating",
                f"Generating continuous segment {index}{mode_label}",
            )
            workflow = copy.deepcopy(template)
            workflow[config["prompt_node"]]["inputs"][
                config["prompt_field"]
            ] = shot["prompt"]
            negative_node = config.get("negative_node")
            if negative_node is not None:
                workflow[negative_node]["inputs"]["text"] = (
                    workflow[negative_node]["inputs"]["text"].rstrip("，, ")
                    + CONTINUITY_NEGATIVE
                )
            pose_prompt_node = config.get("pose_prompt_node")
            if pose_prompt_node is not None:
                workflow[pose_prompt_node]["inputs"]["value"] = plan.get(
                    "motion_prompt", shot["prompt"]
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
            ] = row["seed"] + index - 1
            for node_id, overrides in config["input_overrides"].items():
                workflow[node_id]["inputs"].update(overrides)
            if row["quality_mode"]:
                apply_quality_mode(workflow, config)
            if row["precision_mode"]:
                for node_id, overrides in config["precision_overrides"].items():
                    workflow[node_id]["inputs"].update(overrides)
            workflow[config["save_node"]]["inputs"]["filename_prefix"] = (
                f"jobs/{job_id}/shot_{index:02d}_{config['prefix']}"
            )
            prompt_id = queue_comfy(workflow)
            history = wait_for_comfy(prompt_id, job_id)
            shot_relative = output_relative_path(
                history, config["save_node"]
            )
            shot_paths.append(shot_relative)
            if index < len(plan["shots"]) and engine in {"wan5b", "wan14"}:
                add_event(
                    job_id,
                    "continuing",
                    f"Passing segment {index} end frame to segment {index + 1}",
                )
                continuation_image = extract_continuation_frame(
                    job_id, index, shot_relative
                )

        raise_if_cancelled(job_id)
        add_event(job_id, "finishing", "Finalizing local MP4")
        if engine != "ltx25":
            final_relative = finish_wan(job_id, shot_paths)
        else:
            # LTX AUP prohibits removing provenance information. Preserve the
            # exact ComfyUI output instead of remuxing it through FFmpeg.
            final_relative = shot_paths[0]
        if parent is not None:
            add_event(
                job_id,
                "extending",
                "Combining the source video and its continuation",
            )
            final_relative = combine_continuation_videos(
                job_id, parent["output_path"], final_relative
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
    concept: str = Field(default="", max_length=2000)
    engine: Literal[
        "wan5b", "wan14", "ltx25", "animate2", "zimage", "outfit"
    ] = "wan5b"
    image_size: Literal["square", "landscape", "portrait"] = "square"
    input_image: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9_.-]+\.(?:png|jpg|jpeg|webp)$",
    )
    garment_image: str | None = Field(
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
    enhance_prompt: bool = False
    quality_mode: bool = False
    precision_mode: bool = False
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
        if self.engine != "outfit" and len(self.concept.strip()) < 10:
            raise ValueError("concept must contain at least 10 characters")
        if self.engine in IMAGE_ENGINES:
            if self.shots != 1:
                raise ValueError("Image jobs produce exactly one image")
            if self.continuation_of or self.precision_mode:
                raise ValueError(
                    "Continuation and precision mode apply to videos only"
                )
        if self.engine == "outfit" and not (
            self.input_image and self.garment_image
        ):
            raise ValueError(
                "Outfit editing needs both a person image and an outfit image"
            )
        if self.continuation_of and self.engine not in {"wan5b", "wan14"}:
            raise ValueError(
                "Continuation is currently available for Wan 5B and Wan 14B"
            )
        if self.precision_mode and self.engine != "wan14":
            raise ValueError("precision_mode is only available for Wan 14B")
        if (
            self.engine in {"wan14", "ltx25", "animate2"}
            and not self.input_image
            and not self.continuation_of
        ):
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
            if self.shots != 1:
                raise ValueError(
                    "LTX-2.5 jobs are limited to one shot so provenance "
                    "information is preserved"
                )
        return self


def row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    if result.get("plan_json"):
        result["plan"] = json.loads(result.pop("plan_json"))
    else:
        result.pop("plan_json", None)
        result["plan"] = None
    output_path = result.get("output_path")
    result["output_url"] = (
        f"/outputs/{quote(output_path, safe='/')}"
        if isinstance(output_path, str)
        else None
    )
    is_image = result["engine"] in IMAGE_ENGINES
    result["output_type"] = "image" if is_image else "video"
    generated = generated_input_name(result["id"])
    result["generated_input"] = (
        generated
        if is_image
        and result["status"] == "succeeded"
        and (INPUT_ROOT / generated).is_file()
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
    media_types = {".mp4": "video/mp4", ".png": "image/png"}
    if (
        not path.is_relative_to(OUTPUT_ROOT.resolve())
        or not path.is_file()
        or path.suffix.lower() not in media_types
    ):
        raise HTTPException(status_code=404, detail="Output not found")
    return FileResponse(
        path, media_type=media_types[path.suffix.lower()], filename=path.name
    )


@app.get("/health")
def health() -> dict[str, Any]:
    result: dict[str, Any] = {"service": "ok"}
    for name, url in (
        ("ollama", f"{OLLAMA_URL}/api/version"),
        ("comfyui", f"{COMFY_URL}/system_stats"),
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
    is_image = request.engine in IMAGE_ENGINES
    is_outfit = request.engine == "outfit"
    input_image = (
        "" if request.engine == "zimage" else request.input_image or ""
    )
    garment_image = (request.garment_image or "") if is_outfit else ""
    quality_mode = 0 if is_image else int(request.quality_mode)
    precision_mode = int(request.precision_mode)
    image_size = request.image_size if request.engine == "zimage" else ""
    if is_outfit:
        try:
            resolved_input(input_image, "Person image")
            resolved_input(garment_image, "Outfit image")
        except (ValueError, FileNotFoundError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
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
                    continuation_of, image_size, garment_image, created_at,
                    updated_at
                ) VALUES (
                    ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?
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
                    continuation_of,
                    image_size,
                    garment_image,
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
            request_json(f"{COMFY_URL}/interrupt", {}, timeout=30)
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
        generated_input_name(job_id),
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
        active_image_user = db.execute(
            """
            SELECT id FROM jobs
            WHERE (input_image = ? OR garment_image = ?)
              AND status NOT IN ('succeeded', 'failed', 'cancelled')
            LIMIT 1
            """,
            (generated_input_name(job_id), generated_input_name(job_id)),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if active_image_user is not None:
        raise HTTPException(
            status_code=409,
            detail="Wait for or cancel the video that uses this image "
            "before deleting it",
        )
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
