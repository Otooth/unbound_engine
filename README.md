# DGX Spark Local Video Generation

Pinned, local-first ComfyUI environment for Wan 2.2 on NVIDIA DGX Spark.
ComfyUI is published only on `127.0.0.1:8188`.

## Current verified configuration

- DGX Spark / NVIDIA GB10 / 128 GB unified memory
- ComfyUI 0.45.0
- Wan 2.2 TI2V-5B FP16
- Wan 2.2 I2V-14B FP8 scaled + LightX2V 4-step LoRA
- Wan Animate 2 distilled 14B INT8 ConvRot
- LTX-2.5 distilled Transformer/Text Encoder INT8 ConvRot
- Z-Image Turbo BF16 text-to-image (1024×1024 in about 10 seconds)
- Official 1280×704, 121-frame, 24 fps workflow
- Container build and source revisions pinned in `versions.lock`
- ComfyUI-Manager configured with `network_mode = offline`
- Hugging Face and Transformers offline environment variables enabled

## Build and start

```bash
docker compose build
scripts/start.sh
```

Open <http://127.0.0.1:8188/> locally. For access from another machine, use an
SSH tunnel; do not expose port 8188 publicly.

```bash
ssh -L 8188:127.0.0.1:8188 USER@DGX_SPARK
```

## Models

The expected files and SHA256 values are recorded in `models/MANIFEST.md`.
To download or re-verify them:

```bash
python3 -m venv hf-env
hf-env/bin/pip install huggingface_hub==2.4.0
hf-env/bin/python scripts/download-models.py
hf-env/bin/python scripts/download-models.py wan14
hf-env/bin/python scripts/download-models.py ltx25
hf-env/bin/python scripts/download-models.py animate2
hf-env/bin/python scripts/download-models.py zimage
hf-env/bin/python scripts/download-models.py klein
```

The downloader uses pinned Hugging Face revisions, verifies file size and
SHA256, and disables the unreliable Xet/CAS transfer path.

## Generate the verified test video

```bash
python3 scripts/test-generate.py
```

The API workflow is `workflows/video_wan2_2_5B_ti2v.api.json`. Runtime and
unified-memory measurements are written under `logs/generations/`.

## Verified 14B / LTX-2.5 comparison

The reproducible UI workflows are:

- `workflows/compare_wan14_4step_i2v.json`
- `workflows/compare_ltx2_5_i2v.json`

Both use the same input image, prompt, seed, 640×640 output, 81 frames,
16 fps, and approximately 5 seconds. On this DGX Spark, Wan 14B 4-step took
100.352 seconds with 85.71 GiB peak unified-memory use. LTX-2.5 took
45.686 seconds with 92.77 GiB peak use and also generated stereo audio.
These are single-sample measurements; they do not establish a general image
quality ranking.

## Local automation API

The FastAPI service queues jobs sequentially and stores status, plans, and
events in `data/jobs.sqlite3`. It binds only to `127.0.0.1:8001`.

```bash
scripts/start-api.sh
```

Open <http://127.0.0.1:8001/> for the Japanese web interface. It supports
prompt entry, model and shot selection, image upload, live progress, history,
in-progress cancellation, and finished-video playback. Cancelling a waiting
job removes it from automation processing; cancelling the active job also
interrupts ComfyUI and releases loaded models before the next job. Finished,
failed, or cancelled jobs can be deleted from the history card or job view
(`DELETE /jobs/JOB_ID`). This removes the job record, its progress events,
`output/jobs/JOB_ID/`, and the job's continuation frames and motion guide in
`input/`. Uploaded reference images are kept. Direct API
use remains available at
<http://127.0.0.1:8001/docs>.

Choose the engine by input type:

- `wan5b`: text-to-video. Use this when the scene should be created from the
  prompt alone.
- `wan14`: image-to-video. A matching reference image is required; its people,
  clothing, and background strongly constrain the result.
- `animate2`: character motion transfer. A clean, front-facing, full-body
  image is recommended. Separate front, side, and back files can be registered,
  and the view matching the motion's starting direction can be selected for
  each generation. For a horizontal turnaround sheet, select the front view
  and confirm the crop preview contains exactly one complete character. The UI
  pads it to the model aspect ratio so the body is not center-cropped.
  An optional MP4 supplies motion; when omitted, Wan 5B creates a separate
  neutral human motion guide from the requested action.
- `ltx25`: image-to-video with audio. A matching image and license
  acknowledgement are required.
- `zimage`: text-to-image with Z-Image Turbo (8 steps). Choose
  `"image_size": "square"` (1024×1024, matches Wan 14B and Animate),
  `"landscape"` (1280×720), or `"portrait"` (720×1280) for LTX-2.5.
- `outfit`: image editing with FLUX.2 [klein] 4B. Replaces the clothing of the
  person in `input_image` with the outfit in `garment_image`.

### Generate an image, then animate it

1. Select **Z-Image Turbo — 文章から画像** and generate. Qwen rewrites the
   request as one English still-image prompt that keeps every subject fully in
   frame as a stable first frame (disable prompt enhancement to send it
   verbatim).
2. The PNG is re-written with FFmpeg `-map_metadata -1`, which removes the
   embedded ComfyUI prompt/workflow text chunks, and saved as
   `output/jobs/JOB_ID/image.png`. A copy named `generated-JOB_ID.png` is
   registered in `input/`.
3. Choose **この画像から動画を作る**. The form switches to Wan 14B with that
   image selected as the start image; LTX-2.5 or Wan Animate 2 can be chosen
   instead. Via the API, pass `"input_image": "generated-JOB_ID.png"`.

Deleting an image job also removes `generated-JOB_ID.png`. Deletion is refused
while a queued or running video still uses that image. Download the model with
`hf-env/bin/python scripts/download-models.py zimage` (about 20.7 GB).

Measured on this DGX Spark: a 1024×1024 image job took 22 seconds end to end
(11 s Qwen planning, 10 s generation) with 57.2 GB peak system memory. Using
that image, a 5-second Wan 14B 4-step video took 114 seconds with 56.7 GB peak
and kept the person, clothing, and background from the image.

### Change only the outfit, then follow a motion video

`outfit` edits a person image with FLUX.2 [klein] 4B (distilled FP8, 4 steps,
Apache-2.0). Image 1 is the person and image 2 is the outfit; the fixed English
instruction keeps the face, hair, pose, framing, and background from image 1
and replaces only the clothing. The optional `concept` adds a note (translated
to English by Qwen when prompt enhancement is on). Output size follows the
person image, scaled to about 1 megapixel.

1. Select **FLUX.2 Klein — 人物の服装を画像で差し替え**, choose the person image
   and the outfit image, and generate. The result is saved like a Z-Image job
   (`output/jobs/JOB_ID/image.png`, metadata removed, and
   `input/generated-JOB_ID.png`).
2. Choose **この画像を動作元動画どおりに動かす（Wan Animate 2）**, attach the
   motion MP4, and generate. The prompt is optional when a motion MP4 is
   attached: left empty, Qwen planning is skipped and a fixed prompt asks the
   model to follow the video's movements while keeping the reference image's
   appearance and background. Wan Animate 2 animates the
   whole edited image; the background comes from that image, not from the
   motion video. Partial replacement inside the original video is not
   supported.
3. Wan Animate 2 uses 81 frames of the motion video (3.4 s at 24 fps). To use
   a later part of a longer MP4, play it in the preview and choose
   **今の再生位置から使う**, or pass `"motion_start": 12.5` (seconds) via the
   API. The job re-encodes the MP4 from that position into
   `input/motion-trim-JOB_ID.mp4` (removed with the job) and fails before
   generation if fewer than 81 frames remain.

Via the API:

```json
{"engine": "outfit", "input_image": "person.png",
 "garment_image": "cardigan.png", "concept": ""}
```

Measured on this DGX Spark: replacing a navy T-shirt with a red cardigan on a
576×1024 full-body image took 12 seconds end to end (10 s editing) with
27.5 GB peak system memory; face, hair, trousers, shoes, pose, and background
were kept. Animating that image with a 5.2-second street-dance MP4 in Wan
Animate 2 took 372 seconds (25 s Qwen planning) with 57.2 GB peak and produced
480×832, 24 fps, 81 frames (3.4 s); the cardigan stayed consistent in every
frame.

Use only images of yourself, consenting people, or AI-original characters.
Download the model with
`hf-env/bin/python scripts/download-models.py klein` (about 4.4 GB; the
Qwen3-4B text encoder is shared with Z-Image).

Prompt enhancement is enabled by default and is constrained to preserve
character count, age, build, and reciprocal actions. Disable it to send the
entered wording unchanged. Leave Seed blank for a new composition on every
run; enter a previous Seed only when reproducing a result.

The web UI provides two generation modes:

- **Standard / fast** uses the verified lower-resolution settings and normal
  H.264 output.
- **Quality priority** raises the generation resolution for each engine and
  saves H.264 with CRF 0 and 4:4:4 chroma. This is losslessly compressed rather
  than raw uncompressed video, so it retains generated pixels while remaining
  an MP4. It requires more time, unified memory, and storage.

For Wan 5B and Wan 14B, selecting approximately 10 or 15 seconds generates
consecutive segments. The final frame of each segment becomes the start image
of the next segment before lossless concatenation. Prompt planning also treats
all segments as one continuous take, avoiding the former five-second reset.

Any completed video job (Wan 5B, Wan 14B, LTX-2.5, or Wan Animate 2) can be
extended:

1. Open it from **最近の生成** and choose **この動画の続きを作る**.
2. Enter only the new action that happens after the existing ending and choose
   how many additional seconds to generate.
3. The automation extracts the exact final frame, inherits the engine and
   quality/precision settings, generates the continuation, and concatenates
   the source plus the new clip into one cumulative `extended.mp4`.

An extended result can be extended again. The source job cannot be deleted
while its continuation is queued or running. The API equivalent is
`"continuation_of": "SOURCE_JOB_UUID"` in `POST /jobs`; the engine must match
the source.

Engine-specific behavior:

- **LTX-2.5**: the final frame becomes the next start image, and audio is
  regenerated for the new clip. The license acknowledgement is still required.
  `extended.mp4` keeps the source's embedded ComfyUI metadata because the LTX
  license forbids removing provenance signals; the per-clip originals stay in
  the job folder. Measured: 5 s source + 5 s continuation in 43 s,
  10.125 s / 162 frames with audio, peak about 59GB.
- **Wan Animate 2**: the character images of the source are reused and the
  final frame is passed to `continue_motion`, with the frame offset carried
  over (the duplicated first frame is dropped, so each continuation adds
  80 frames). The prompt is optional. Leave the motion MP4 empty to keep
  tracing the source's motion MP4 from where it stopped (if fewer than 81
  frames remain, the last pose is held), or choose a new MP4 and start time.
  It is converted to the source's frame rate. If the source used a generated
  motion guide, enter the next action instead. Measured: 343 s per
  continuation, 161 frames at 24 fps, peak about 55GB.

Wan 5B, Wan 14B, and LTX-2.5 keep the boundary frame from both clips (one
repeated frame). The source and its continuation must use the same engine.

Prompt planning keeps instructions as ordered steps:

- The request is split into ordered clauses (sentence ends, `→`, `その後`,
  `then`, and similar). Qwen must return exactly one English step per clause,
  so it cannot drop the first action or start from the end result.
- Qwen also returns the visible result after each step and one fixed
  camera-relative body direction. A second pass audits missing items, order,
  state continuity, orientation, and whether hands and carried objects remain
  physically consistent. One rejected plan is regenerated.
- Steps are assigned to segments in order. A later segment receives the
  previous segment's resulting state, not a repetition of completed actions.
- Human-like subjects use a fixed front, side, or back camera view. Positive
  and negative prompts explicitly reject reversed torsos, front/back fusion,
  impossible head turns, and duplicate limbs.
- For image-based engines the start image defines appearance and framing, so
  the planner does not describe them, and the negative prompt adds cuts,
  transitions, sudden zoom, face changes, and anatomical orientation errors.
- The planned steps appear in the job progress as `工程: 1) … → 2) …`.

**Motion precision (Wan 14B only)** sets the official template's switch to
its standard path: 20 steps, CFG 3.5, without the LightX2V 4-step LoRAs. No
extra model is needed. A two-segment 640×640 job took 1,478 seconds versus
roughly 100 seconds per 4-step segment, with 63.4 GB peak system memory.

```bash

curl -X POST http://127.0.0.1:8001/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "concept": "A white dragon warrior slowly raises one hand as the camera circles.",
    "engine": "wan14",
    "shots": 1
  }'

curl http://127.0.0.1:8001/jobs/JOB_ID
curl http://127.0.0.1:8001/jobs/JOB_ID/events
scripts/stop-api.sh
```

When prompt enhancement is enabled, the pipeline asks the installed Qwen model
for structured English shot prompts and unloads Ollama with `keep_alive: 0`.
It then calls ComfyUI `/free`, generates each shot through pinned API
workflows, and finalizes Wan output with FFmpeg metadata removal. LTX jobs
require `"ltx_license_acknowledged": true`, are limited to one shot, and
preserve the original output without remuxing so provenance information is not
removed.

Configuration can be overridden with `OLLAMA_MODEL`, `OLLAMA_URL`,
`COMFY_URL`, and `COMFY_TIMEOUT`. See `docs/troubleshooting.md`.

## Offline acceptance test

Start ComfyUI, then run:

```bash
scripts/test-offline.sh
```

The script disconnects the running container from all Docker networking,
confirms that external HTTPS is unavailable, regenerates the official 5B test,
and reconnects the original network using an exit trap.

## Remove embedded prompt metadata

ComfyUI embeds the workflow and prompt in generated MP4 files. Strip it before
sharing a file:

```bash
scripts/strip-metadata.sh \
  video/input.mp4 \
  video/output-stripped.mp4
```

Both paths are relative to `output/`. The script refuses path traversal and
overwrites, removes metadata without re-encoding, and verifies that `prompt`
and `workflow` tags are absent.

## Stop and collect logs

```bash
scripts/collect-logs.sh
scripts/stop.sh
```

`stop.sh` calls ComfyUI `POST /free` to unload models before stopping the
container.
