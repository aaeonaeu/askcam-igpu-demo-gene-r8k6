# "Ask your camera" demo: build plan

Target: AAEON GENE-R8K6 (Ryzen 5 PRO 8640U, Radeon 760M iGPU gfx1103, 2x8 GB DDR5-4800, Ubuntu 26.04, kernel 7.0).
Everything runs under Docker Compose in `~/askcam`. The NPU is out of scope.

**Architecture: all AI on the iGPU.** Video decode runs on the VCN engine through VA-API. Object detection runs through ROCm and MIGraphX inside Frigate. The VLM runs through Vulkan in llama.cpp. The CPU handles tracking, rules and the UI.

## Already done (prep)
- **Docker:** Engine 29.9.3 and Compose v5.7.0 from download.docker.com. User `aaeon` is in the `docker`, `render` and `video` groups (video GID 44, render GID 990). Memlock is unlimited. The system has been rebooted, so these are active.
- **Vulkan in containers:** `ghcr.io/ggml-org/llama.cpp:server-vulkan` (already pulled) sees `Vulkan0: AMD Radeon 760M Graphics (RADV PHOENIX)`, with about 7.5 GiB usable. On the host, Gemma-3-1B Q4_K_M did pp512 1610 t/s and tg128 66 t/s.
- **ROCm on gfx1103, native, no `HSA_OVERRIDE_GFX_VERSION`:**
  - TheRock nightly (`~/rocm-test/venv`, ROCm 10.3, torch 2.15): YOLO11s fp16 takes about 10 ms.
  - Frigate 0.18.2 `ghcr.io/blakeblackshear/frigate:stable-rocm` (bundles ROCm 7.2.3, ONNX Runtime with MIGraphX): YOLO11s ONNX at 320 runs at **8–12 ms per inference**, about 80 detections/s across 3 cameras. It ran 10 minutes with no errors, using about 70% of the iGPU and 23% of the CPU. The first start compiles the model in about 82 s, then it's cached.
- **Working Frigate test setup in `~/frigate-test`:** `compose.yml`, `config/config.yml`, `config/model_cache/yolo11s-320.onnx`, `media/test3.mp4`, `wait_stats.sh`.
- **Frigate gotchas found:**
  - Set `detect.enabled: true` explicitly.
  - Don't mount anything read-only at `/media`, because Frigate writes to `/media/frigate`.
  - Clips need small, localized motion (under 5% of the frame), or the motion detector never finishes calibrating.
  - The COCO labelmap gives "bus/truck not supported" warnings, and a bus is labelled `car`.
- **Host gotchas found:**
  - `/tmp` is a tmpfs with a per-user limit, so set `TMPDIR` to a folder on disk for big pip installs.
  - The Debian llama.cpp `-hf` download is broken; use curl.
- The sudo password on this lab unit is the username.

## How to use this plan
- One step per prompt. Before each step, set the model and effort listed for it in the app.
- Each prompt is self-contained. Every step reads and updates `~/askcam/NOTES.md`, so you can clear the conversation between steps and save tokens.
- Run the steps in order.

## Version control (applies to every step)
- `~/askcam` is a git repository (set up in step 1). Every step ends with a commit, `askcam: step N - <summary>`, made only after the step's "Done when" checks pass.
- **What goes in git:** compose files, `.env.example`, Frigate config, prompts, UI code, scripts (`fetch-models.sh`, `fetch-media.sh`, bundle/restore), `media/SOURCES.md`, README, NOTES.md, DEMO.md, PLAN.md.
- **What stays out (`.gitignore`):** `.env` (host-specific GIDs and secrets), `models/` (GGUF and ONNX, re-downloaded or re-exported by scripts), `media/*.mp4` (fetched by script), Frigate runtime data (`frigate/db`, recordings, clips, `model_cache/`), and offline bundles (`*.tar*`). Large binaries are recreated by scripts instead of committed, so the repo stays small. Git LFS is an option later if you'd rather version the ONNX model.
- **Remote:** pushed in step 6 to a remote you provide (GitHub/GitLab/Gitea URL plus credentials or SSH key). Nothing is pushed before that, and pushing only happens when you explicitly ask.
- **Git identity:** set it per repo (`git config user.name/user.email`), not globally. Step 1 asks you for the name and email to use.

| # | Step | Model | Effort | Why |
|---|------|-------|--------|-----|
| 1 | VLM server on the iGPU (compose skeleton) | Sonnet 5.5 | medium | Standard setup; some model/flag choices |
| 2 | Simulated RTSP cameras | Sonnet 5.5 | low | Simple, well-documented |
| 3 | Frigate on the iGPU: VA-API decode + ROCm/MIGraphX detection + zones | Sonnet 5.5 | medium | Working config already proven in `~/frigate-test`; mostly porting and polishing |
| 4 | Frigate GenAI → local VLM, plus tuning detection and the VLM together on the iGPU | Opus 5.5 | medium | Detection and VLM compete for GPU time and memory bandwidth, which needs measurement and tuning |
| 5 | "Ask the camera" web page + live load meters | Sonnet 5.5 | high | Custom code and UI quality |
| 6 | Packaging, offline bundle, soak test, demo script | Sonnet 5.5 | medium | Docs and scripts; long test run in the background |

---

## Step 1: VLM server on the iGPU
**Model: Sonnet 5.5 · Effort: medium**

```
Project dir: ~/askcam (read PLAN.md first; create NOTES.md if missing and log decisions/results there at the end).
First: initialize ~/askcam as a git repo (branch main) following the "Version control" section of PLAN.md: create the .gitignore described there and a .env.example, ask me for the git user.name/user.email to set locally (repo-only config), and commit PLAN.md as the initial commit.
Goal: a docker compose service "vlm" running llama.cpp server on the AMD Radeon 760M via Vulkan, serving a small vision-language model with an OpenAI-compatible API.
Constraints/facts:
- Image ghcr.io/ggml-org/llama.cpp:server-vulkan is already pulled; it needs /dev/dri plus group_add for video (44) and render (990). The iGPU has about 7.5 GiB usable shared memory and will also run Frigate's ROCm detector (about 0.4 GB GPU memory), and total RAM is 14 GiB, so keep the VLM at or below about 4 GB.
- Pick Qwen2.5-VL-3B-Instruct GGUF (Q4_K_M plus its mmproj) as the default and Gemma-3-4B-it as the alternative. Download with scripts/fetch-models.sh (curl from Hugging Face resolve URLs, into ./models, skip if present). Don't rely on llama.cpp's -hf download.
- Settings: full GPU offload, context about 8k, parallel slots 2, port 8080, healthcheck, restart unless-stopped. Put tunables in .env.
Done when: `docker compose up -d vlm` works, the built-in web UI at :8080 answers a question about an uploaded image, and NOTES.md records, for one test image (use the API with curl and ~/frigate-test/media/bus.jpg): image-encode time, prompt t/s, generation t/s, and memory used (/sys/class/drm/card1/device/mem_info_gtt_used). Then commit (see "Version control" in PLAN.md).
```

## Step 2: Simulated RTSP cameras
**Model: Sonnet 5.5 · Effort: low**

```
Project dir: ~/askcam (read PLAN.md and NOTES.md first; append results to NOTES.md).
Goal: add a "cams" service (bluenviron/mediamtx) that publishes 3 looping RTSP streams (cam1..cam3) from local video files in ./media, so the demo runs without real cameras. Use an ffmpeg sidecar (or mediamtx runOnInit) with -re -stream_loop -1 and -c copy (no CPU transcoding in the loop).
Videos: find 3 short 1080p clips with freely redistributable licenses (e.g., Pexels/Pixabay or other CC0/public-domain sources): a street or parking lot with cars and people, a warehouse or industrial scene, and a shop/entrance with people. They must be FIXED-camera footage (no panning/zooming) with people/vehicles moving through only part of the frame, because Frigate's motion detector stays in "calibrating" (and never runs detection) when more than ~5% of the frame changes continuously. Add scripts/fetch-media.sh to download them and record source URL plus license in media/SOURCES.md. Re-encode once to H.264 1080p, GOP of about 2 s, if needed (fine as a one-off).
Done when: ffprobe rtsp://localhost:8554/cam1..3 shows valid H.264 streams that loop indefinitely, and the services restart cleanly. Then commit (see "Version control" in PLAN.md).
```

## Step 3: Frigate on the iGPU (decode + detection + zones)
**Model: Sonnet 5.5 · Effort: medium**

```
Project dir: ~/askcam (read PLAN.md and NOTES.md first; append results/decisions to NOTES.md).
Goal: add a Frigate service to the askcam compose stack, consuming the 3 RTSP streams from the "cams" service, with everything AI on the iGPU.
Start from the proven working setup in ~/frigate-test (compose.yml, config/config.yml, config/model_cache/yolo11s-320.onnx). Key facts already verified there:
- Image ghcr.io/blakeblackshear/frigate:stable-rocm (0.18.2) with devices /dev/kfd and /dev/dri, group_add 44 and 990, LIBVA_DRIVER_NAME=radeonsi, NO HSA_OVERRIDE_GFX_VERSION (gfx1103 works natively).
- Detector: `detectors: onnx: {type: onnx}` with model_type yolo-generic, 320x320, nchw, float, labelmap /labelmap/coco-80.txt. Measured 8-12 ms/inference. First start compiles MIGraphX for ~82 s; persist /config/model_cache so it's cached.
- hwaccel_args: preset-vaapi (decode on the VCN engine).
- Camera clips from step 2 (rtsp://cams:8554/cam1..3, 15 fps, 1080p). With default motion settings Frigate stays in "calibrating" on busy scenes (needs <5% motion and <=4 motion boxes) and never detects: cam1 and cam2 got 0 detections. Per-camera `motion: {threshold: 50, contour_area: 80, improve_contrast: false}` fixed it (tune further if needed). With that, 3 cameras generate ~35 detections/s each, saturate the single detector (~9.5 ms) and skip 5-7 fps per camera: reduce detect fps (~5), use min_area/masks, try YOLO11n or a second detector instance, until skipped_fps is ~0.
- Events/review items need zones and `review`/`objects` settings: the throwaway config produced 0 events.
- `detect.enabled: true` must be set explicitly. Don't mount anything read-only at /media (Frigate writes /media/frigate).
To do:
- Copy the ONNX model into the askcam tree (or export it with ~/rocm-test/venv: `YOLO('yolo11s.pt').export(format='onnx', imgsz=320, opset=17, simplify=True)` with TMPDIR on disk).
- Fix labels: track person, car, bicycle, motorcycle, plus truck/bus if they can be supported (investigate the "not supported by the current model" warning and the bus showing as "car"; use or create a labelmap that keeps them distinct).
- One meaningful zone per camera (e.g., "entrance", "loading_bay"), recording limited to events with short retention (disk is 256 GB), shm_size sized for 3x1080p, timezone Europe/Rome, auth decision documented (disabled for a booth is acceptable, note it).
- Optionally compare YOLO11n vs YOLO11s and 320 vs 416 input for accuracy vs GPU load; pick the default.
Done when: the Frigate UI shows the 3 cameras with live boxes and zones, events appear in Review, and NOTES.md records inference ms, detections/s, total CPU %, GPU busy % (/sys/class/drm/card1/device/gpu_busy_percent) and GPU memory with all 3 streams running. Then commit (see "Version control" in PLAN.md).
```

## Step 4: Frigate GenAI with the local VLM, plus shared-iGPU tuning
**Model: Opus 5.5 · Effort: medium**

```
Project dir: ~/askcam (read PLAN.md and NOTES.md first; append results to NOTES.md).
Goal: enable Frigate's GenAI feature so tracked objects / review items get a natural-language description from our local "vlm" service (llama.cpp server, OpenAI-compatible at http://vlm:8080/v1). Use the openai provider with base_url pointed at the vlm service (check the current Frigate 0.18 docs/config schema inside the image for exact keys; set a dummy api key if required).
- Write short, factual prompts per object type in English for a factory/retail security demo (who/what, where/zone, action, anything unusual), under ~60 words.
- Only events should trigger the VLM (no continuous calls).
- IMPORTANT: detection (ROCm/MIGraphX) and the VLM (Vulkan) now share the same iGPU and memory bandwidth. Measure with both active: detector inference ms and skipped fps before/during VLM requests, VLM time-to-first-token and t/s with detection running, GPU busy %, GTT memory. Tune until detection never skips frames and the VLM answers within a few seconds (options: detect fps, YOLO11n vs s, 320 input, VLM image size/max tokens, parallel slots 1 vs 2, GenAI request concurrency).
Done when: new events show AI descriptions within a few seconds, detection skipped_fps stays ~0 during VLM calls, and NOTES.md records end-to-end latency (event to description) and the chosen tuning with before/after numbers. Then commit (see "Version control" in PLAN.md).
```

## Step 5: "Ask the camera" page and live load meters
**Model: Sonnet 5.5 · Effort: high**

```
Project dir: ~/askcam (read PLAN.md and NOTES.md first; append results to NOTES.md).
Goal: a small "askcam-ui" container (Python FastAPI plus a single static page, no heavy frontend build) on :8000 that is the booth demo screen:
- Camera picker plus live view (MJPEG or snapshots from the Frigate API, with detection boxes).
- A question box: grabs the current frame from Frigate, sends it with the question to the vlm service, and streams the answer token by token. Include 4 preset question buttons (e.g., "What is happening?", "Is anyone without safety gear?", "Count the vehicles", "Anything unusual?").
- A live feed of the latest Frigate events with their AI descriptions (Frigate API, or MQTT if enabled).
- Load meters updated every second, framed as "one chip, three engines":
  - iGPU compute busy % (/sys/class/drm/card1/device/gpu_busy_percent), split if possible into detection (Frigate detector stats) vs VLM activity
  - video decode (Frigate's amd-vaapi GPU stats)
  - GPU shared memory used (mem_info_gtt_used)
  - CPU %, RAM, and package power if readable
  - footer: "100% local · Ryzen 5 PRO 8640U · Radeon 760M · ROCm + Vulkan · no cloud"
  Mount the needed /sys paths read-only.
- Clean, high-contrast design readable from 2 m away at 1080p, with a dark theme.
Done when: the page works end-to-end with all 3 cameras, answers start streaming within ~3 s, detection keeps running without skipped frames while a question is answered, and the meters visibly react. Then commit (see "Version control" in PLAN.md).
```

## Step 6: Packaging, offline bundle, soak test and demo script
**Model: Sonnet 5.5 · Effort: medium**

```
Project dir: ~/askcam (read PLAN.md and NOTES.md first).
Goal: make the demo easy to save, share and replicate.
1. README.md: requirements (Ubuntu 24.04+ / kernel with amdgpu and amdkfd, Docker + compose from download.docker.com, user in docker/render/video groups, /dev/kfd and /dev/dri present), quick start (fetch-models, fetch-media, docker compose up -d), URLs, first-start note (MIGraphX compile ~1-2 min, cached in model_cache), troubleshooting (group IDs differ per host so they go in .env, VA-API check, GTT memory, Frigate "calibrating" with bad test clips).
2. Pin image versions/digests in compose; all settings in .env with a .env.example; healthchecks and depends_on conditions; `make up/down/logs/bundle` or equivalent scripts.
3. scripts/bundle-offline.sh: docker save all images plus models plus media plus config (including the precompiled MIGraphX cache) into one tarball, and scripts/restore-offline.sh to load and start on a machine with no internet. Test restore by removing images locally and restoring.
4. Soak test: run the full stack for 60 minutes in the background with a script firing a VLM question every 30 s; log detector inference ms and skipped fps, VLM latency, memory, temperatures (sensors / hwmon), GPU busy, kernel amdgpu/kfd errors (journalctl -k) and container restarts; report any leaks, throttling or GPU errors in NOTES.md.
5. DEMO.md: a 3-minute booth script with talking points ("all AI on the integrated GPU: video decode on VCN, detection via ROCm, VLM via Vulkan; CPU free for the application") and the measured numbers from NOTES.md.
6. Git: check that `git status` is clean and no large or host-specific files are tracked (`git ls-files | xargs du -ch | tail -1`, no .env/models/media/*.mp4/model_cache). Verify replication from git alone: `git clone ~/askcam /tmp-on-disk/askcam-clone`, then cp .env.example .env, fetch scripts, `docker compose up -d` work there (use a different compose project name and ports, or stop the main stack first). Then ask me for the remote URL, add it as `origin`, tag the release (e.g. v1.0-demo), and push main plus tags only after I confirm.
Done when: a fresh clone plus `docker compose up -d` following the README works, the offline bundle restores, the soak test passes, and the repo is pushed to the remote.
```
