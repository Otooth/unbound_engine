# Troubleshooting

## Docker socket permission denied

Symptom:

```text
permission denied while trying to connect to the docker API
```

Confirm membership and the socket group:

```bash
id
stat -c '%A %U %G %n' /var/run/docker.sock
```

After adding the user to `docker`, start a new login shell or use
`sg docker -c '<command>'` for the current shell. Do not loosen the Docker
socket permissions.

## ComfyUI is not reachable

Check the loopback-only publication:

```bash
docker compose ps
docker compose port comfyui 8188
curl -f http://127.0.0.1:8188/
```

The expected mapping starts with `127.0.0.1:`. Docker internal networks cannot
publish a host port on this setup; offline acceptance instead disconnects the
running container temporarily with `scripts/test-offline.sh`.

## Hugging Face gated model returns 401 or 403

Authenticate with the browser device flow:

```bash
hf-env/bin/hf auth login
hf-env/bin/hf auth whoami
```

For LTX-2.5, also use **Agree and Access** on the official repository page.
The local token is not copied into the container.

## Large downloads disconnect or time out

`scripts/download-models.py` disables Xet/CAS and uses the Hub's resumable
HTTPS path. Run the same profile again:

```bash
hf-env/bin/python scripts/download-models.py ltx25
hf-env/bin/python scripts/download-models.py ltx25-hq
hf-env/bin/python scripts/download-models.py ltx25-dfr
```

The downloader resumes with authenticated HTTP Range requests. A connection or
DNS failure keeps all verified partial bytes; if a server ignores Range, the
script validates the returned response before replacing anything. Every
completed file is checked against its pinned byte size and SHA256. Do not move
a partial file into `models/` manually.

## ComfyUI reports `Value not in list`

The API validates combo values even for some disabled branches. The official
LTX workflow references an optional Prompt Enhancer model while enhancement is
off. `scripts/prepare-comparison-workflows.py` replaces only that unused combo
value with an installed model; Prompt Enhancer remains disabled.

After adding or removing models, restart ComfyUI so `/object_info` reflects the
current model lists.

## Requested LTX dimensions are ignored

The official LTX UI workflow connects a separate `ResolutionSelector` to the
subgraph width and height inputs. Those links override the displayed subgraph
widget values. The comparison workflow sets the selector to square, 0.4 MP,
multiple 32, producing 640×640.

## Native LTX profile is unavailable

Check the worker and its fixed assets:

```bash
curl http://127.0.0.1:8191/health
docker compose --profile ltx logs ltx
```

`profiles_ready` reports `ti2_hq`, `dfr`, and `ti2_chunked` separately. Re-run
the matching verification profile if one is false:

```bash
hf-env/bin/python scripts/download-models.py ltx25-hq
hf-env/bin/python scripts/download-models.py ltx25-dfr
```

DFR's detailing LoRA is a separately gated Hugging Face repository. A 403
after authenticating means its **Agree and Access** step has not been accepted
for the same account. The fixed worker also needs `gcc` and `libc6-dev` because
PyTorch/Triton compiles a small CUDA launcher on first use; rebuild
`unbound-ltx-worker:1.6.2` if that compiler is missing.

DFR in `ltx-pipelines` 1.6.2 does not accept a negative prompt. The automation
passes corrections through its positive constraints for that profile.

## UI workflow fails when posted to `/prompt`

ComfyUI UI JSON and API JSON are different formats. Use the checked-in
`*.api.json` files for `/prompt`. The API files were produced by loading the
pinned UI workflow in the pinned frontend and calling its graph-to-prompt
conversion.

## Unified-memory pressure or OOM

Run Ollama and ComfyUI sequentially:

```bash
curl http://127.0.0.1:11434/api/generate \
  -d '{"model":"MODEL_NAME","keep_alive":0}'
curl -X POST http://127.0.0.1:8188/free \
  -H 'Content-Type: application/json' \
  -d '{"unload_models":true,"free_memory":true}'
curl -X POST http://127.0.0.1:8191/release \
  -H 'Content-Type: application/json' -d '{}'
free -g
```

The automation service performs all three unload operations between planning,
ComfyUI, and native LTX generation. A stopped Ollama process is not required;
`/api/ps` should show no loaded model. `/health` on port 8191 should show no
`active_job_id`.

The **quality priority** button uses higher generation resolution and lossless
H.264 output. If it runs out of unified memory, wait for the current job to
finish, confirm Ollama has no loaded model, and retry in **standard / fast**
mode. Lossless output prevents additional encoding damage but cannot restore
detail absent from a low-resolution or blurred reference image.

## Automation job fails

Inspect the job and ordered events:

```bash
curl http://127.0.0.1:8001/jobs/JOB_ID
curl http://127.0.0.1:8001/jobs/JOB_ID/events
cat logs/automation-api.log
```

Jobs interrupted by an API service restart are marked failed rather than
silently resumed. Submit a new job after correcting the cause.

To stop a queued or running job, use **Cancel generation** in the web UI or:

```bash
curl -X POST http://127.0.0.1:8001/jobs/JOB_ID/cancel
```

The job becomes `cancelled`. Only the matching active job sends ComfyUI an
interrupt; cancelling a waiting job does not stop a different running job.
Partial output is not shown as a completed video.

## A job says `quality_retry` or `品質不合格`

The first high-confidence failure triggers exactly one corrective regeneration
with a new seed. The correction includes the observed anatomy, orientation,
identity, instruction, freeze, or boundary problem. If the second attempt also
fails, the job ends as failed and neither attempt is exposed through
`/outputs`.

Inspect:

```bash
curl http://127.0.0.1:8001/jobs/JOB_ID
curl http://127.0.0.1:8001/jobs/JOB_ID/events
cat output/jobs/JOB_ID/quality-report.json
```

Do not bypass a front/back-fusion or identity failure by increasing resolution
or lowering compression. Those settings do not improve model understanding.
Use a clean start image matching the requested orientation, shorten the action,
or use a driving video with Wan Animate 2.

## A history entry cannot be deleted

`DELETE /jobs/JOB_ID` returns `409` while the job is queued or running. Cancel
it first, then delete it. Deletion is permanent and also removes the job's
output videos. Uploaded reference images in `input/` are not deleted because
other jobs may use them; remove those manually if needed.

## The generated people or setting do not match the prompt

Check the job's `engine` and `input_image`. Wan 14B and LTX-2.5 workflows are
image-to-video: they animate the people, clothing, composition, and background
already present in the reference image. A text prompt cannot reliably replace
a cyberpunk portrait with two different people.

Use **Wan 2.2 5B — text to video** when creating a scene from words alone.
Use Wan 14B or LTX only after selecting or uploading an image that already
contains the intended subjects. The web UI no longer preselects a reference
image.

Prompt enhancement is enabled by default with constraints that preserve ages,
character counts, and reciprocal actions. Disable it when the exact original
wording must reach the video model unchanged.

## An instructed action is skipped or the video starts at the result

Open the job progress and read the `工程:` line. If an action is missing
there, the instruction did not reach the video model; separate actions with
`。`, `その後`, `→`, or `then` so each becomes its own fixed step. A
`plan_rejected` event means the regenerated plan still failed the missing-step,
order, state, orientation, or physical-consistency audit. The job now stops
before loading a video model; it no longer continues with a warning. Rewrite
the request so the initial state, visible action, and end state are explicit.

If the steps are correct but the video is not, the limit is the model:

- Wan 14B and LTX animate the start image and resist large changes to what
  is already in it. Prefer a start image that shows the state before the
  first action.
- The 4-step LoRA reduces adherence for complex sequences. Wan 14B now uses
  the 20-step `quality` profile by default; choose `fast` only when the
  speed/quality trade-off is intentional.
- Wan receives one start frame per segment, but the automation also carries a
  terminal frame window, tail video, and structured direction/speed/pose
  state into the next prompt. This reduces resets but cannot preserve the
  model's hidden latent state.
- Wan Animate 2 with a recorded motion video remains the most precise option
  for choreography.

Quality priority only changes resolution and compression, not adherence.

## A person's front and back are fused or the torso is reversed

This can happen when a text-to-video prompt leaves the body direction
ambiguous, or when a later segment repeats completed actions. Current prompt
enhancement now:

- selects one fixed front, left-profile, right-profile, or back view;
- keeps head, shoulders, torso, hips, knees, and feet in one direction;
- carries the visible end state into the next segment without repeating old
  action text; and
- adds front/back fusion, reversed torso, impossible head turns, and duplicate
  limbs to the negative prompt.

Wan Animate 2 cannot combine the registered front, side, and back images in
one generation: the pinned official node consumes only one reference image.
The UI therefore fixes the whole job to the selected reference view, and the
automatic motion guide uses the same direction. Do not upload a driving video
that turns from front to back; split such motion into separate videos and
generate each fixed view separately. A seamless 360-degree turn is not
supported by the current node without risking front/back fusion.

## A 10- or 15-second video resets every five seconds

Older automation jobs generated independent five-second shots and concatenated
them, so each boundary restarted the scene. Current Wan 5B and Wan 14B jobs
store the exact end frame, a terminal frame sequence, a tail MP4, and Qwen
Vision's structured direction/speed/pose state. Wan uses the exact end frame
as its next model input; the rest constrains the prompt and boundary audit.
The prompt planner also keeps subject, setting, lighting, and camera position
continuous.

This removes the hard visual reset, but long generative video can still drift
gradually because Wan does not expose hidden temporal state. Native LTX's
`ti2_chunked` profile instead uses official 97-frame windows, 25 carried
frames, and 25 overlap-blended frames within one run. Wan Animate 2 remains a
single-segment motion-transfer mode.

## A completed video needs more footage

Open a succeeded Wan 5B or Wan 14B job and click **この動画の続きを作る**.
Enter only what happens next; do not repeat the earlier action. The added
duration selector controls the new footage, while the result player shows one
combined video containing the source and continuation.

The source and continuation must use the same engine. Quality priority and
Wan 14B precision mode are inherited automatically so resolution, frame rate,
and codec remain compatible for lossless stream concatenation. A source cannot
be deleted while its continuation is active.

The continuation receives the complete saved terminal bundle: exact decoded
final frame, sampled terminal frames, tail MP4, probe timing, and Qwen Vision's
subject count, orientation, pose, motion direction/speed, held objects, and
camera state. Wan's model input is still one image, not hidden temporal state,
so motion speed and fine identity can drift. A boundary gate must pass before
concatenation. Continue in shorter increments and describe only the immediate
next action. The result can be extended repeatedly.

If the endpoint returns `409`:

- the source may not be completed;
- the requested engine may differ from the source;
- the source output may be missing; or
- an active continuation may still depend on a source being deleted.

LTX-2.5 and Wan Animate 2 continuation are not exposed: LTX provenance output
is not concatenated, and Animate 2 cannot preserve a motion sequence's hidden
pose state from only its final frame.

## A turnaround sheet creates three characters

Do not pass an uncropped front/side/back sheet directly to an image-to-video
model. It treats the three drawings as three people in the first frame.

Select **Wan Animate 2 — animate a turnaround character** in the web UI,
upload the horizontal sheet, and identify whether its front view is on the
left, center, or right. The browser extracts that third before upload. Choose
**do not crop** only when the source already contains a single character.

Check the reference preview before generation. It must show one character
from head to toe, without labels, detail drawings, or another view. Use the
horizontal and crop-width adjustments when equal thirds do not align with the
sheet layout or the head and limbs touch the crop boundary.
The UI places the extracted figure on a 480:832 white canvas; this prevents a
very narrow source from being center-cropped down to its face.

Wan Animate 2 also needs motion. Upload a short MP4 to reproduce its movement,
or leave the motion field empty to let Wan 5B generate a motion guide from the
text first. Only the first 81 frames are used by the current fixed workflow.
Complex hand contact can still deform because the reference preserves
appearance, not exact 3D geometry.

When no motion MP4 is supplied, the automation now makes a separate neutral
full-body human motion guide. Character species, costume, and background are
intentionally omitted from this intermediate video and are applied only in
the final Animate pass. A clean, short, fixed-camera MP4 with the performer
fully visible remains more reliable than an automatically generated guide.

Wan Animate 2 consumes only the first reference image. Three separate
front/side/back files can be registered in the web UI, but they are not
combined by the official node. Select one view that matches every frame of the
requested motion; the selected file is used and its direction is locked for
that job. Motions that turn from front to back are intentionally unsupported
because one reference cannot define both sides reliably.
Strongly non-human proportions, quadrupeds, intricate contact between
characters, and precise hand choreography can therefore remain unstable even
with clean references.

## Integrated offline acceptance fails

Use `scripts/test-service-offline-acceptance.sh` when this machine is managed
from another computer. It keeps Ethernet/Wi-Fi and SSH available but recreates
Automation, Qwen, ComfyUI, and native LTX with Docker `network none`; only
shared Unix sockets connect their local APIs. The script refuses to start if a
generation job is active and restores the normal services through an exit
trap after either success or failure.

After a failure, verify that restoration completed:

```bash
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8188/system_stats
curl http://127.0.0.1:8191/health
curl http://127.0.0.1:11434/api/version
```

`scripts/test-offline-acceptance.sh` is the physical host-disconnection
variant. It deliberately exits with status 2 while Hugging Face is reachable;
use it only with local console access. Do not disconnect the sole management
network merely to run acceptance.

ComfyUI's smaller `scripts/test-offline.sh` disconnects and restores only its
Docker network automatically.

## Output metadata

Wan outputs are finalized with metadata removal before external use. LTX-2.5
outputs are not remuxed or stripped because its Acceptable Use Policy
prohibits removing or interfering with provenance information. See
`docs/licenses.md`.
