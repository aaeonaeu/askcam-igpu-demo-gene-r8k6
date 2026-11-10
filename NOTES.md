# askcam: working notes

Host: AAEON GENE-R8K6, Ryzen 5 PRO 8640U, Radeon 760M (gfx1103), 2x8 GB DDR5-4800, Ubuntu 26.04, Docker 29.9.3 / Compose v5.7.

## Step 1: VLM server on the iGPU (done 2026-11-10)

**What exists**
- `compose.yml` with service `vlm`: `ghcr.io/ggml-org/llama.cpp:server-vulkan` (llama.cpp 0.9.0-dev, build 12211), `/dev/dri` + `group_add` video/render, models mounted read-only from `./models`, port 8080, healthcheck on `/health`, `restart: unless-stopped`.
- Settings are all in `.env` (template: `.env.example`). Group IDs are host-specific (video 44, render 990 here; check with `getent group video render`).
- `scripts/fetch-models.sh [qwen|gemma|all]` downloads GGUFs with curl (llama.cpp `-hf` does not work here). Gemma-3-4B is only downloaded on request and has not been tested yet.
- `scripts/test-vlm.py IMAGE [QUESTION] [--runs N]` sends an image to the API and prints answer, prompt/gen speed and wall time.

**Model:** Qwen2.5-VL-3B-Instruct Q4_K_M (1.8 GB) + mmproj Q8_0 (0.8 GB), full GPU offload (`-ngl 99`), flash attention on, ctx 8192 across 2 slots (4096 per slot), `--image-max-tokens 512`.

**Measured, one 1080-ish photo (`~/frigate-test/media/bus.jpg`), nothing else running on the iGPU:**

| image-max-tokens | prompt tokens | prompt eval (image encode + decode) | gen speed | wall for ~40-token answer |
|---|---|---|---|---|
| 1024 | 1004 | ~5.0 s | 34 t/s | ~6.2 s |
| **512 (default)** | **526** | **~2.5 s** | **34 t/s** | **~3.2–3.9 s** |
| 256 | 266 | ~1.3 s | 34.6 t/s | ~2.8 s |

- Split at 1024 tokens: text-only prompt of 970 tokens takes ~1.7 s (about 520-590 t/s), so the **image encoder costs about 3.2 s per 1000 image tokens** (about 3.3 s of the 5.0 s). Encoding dominates; the LLM part is cheaper.
- Time to first token is the prompt-eval time (about 2.5 s at 512), which meets the plan's "~3 s to first token" target only at 512 or lower.
- mmproj f16 was slower than Q8_0 (3.07 s vs 2.53 s at 512), so Q8_0 stays.
- llama.cpp warns that Qwen-VL "requires at minimum 1024 image tokens" for grounding (bounding boxes). Descriptions and counting look fine at 512. If step 5/6 wants boxes from the VLM, retest accuracy at 1024.
- Answer quality (512): "The image shows a busy street scene with a blue electric bus in the background, and several people walking on the sidewalk." Correct.

**Memory and load**
- iGPU shared memory (GTT) used: 29 MiB before start, ~3.2 GiB idle with models loaded, ~3.4 GiB while answering (includes the 2 slots' KV cache). Dedicated VRAM 462 MiB. Container RAM ~215 MiB. System RAM used 5.3 GiB of 14.7 GiB (with the earlier test containers stopped).
- GPU busy peaks at 99% during a request (compute bound while encoding), 0% idle.
- Budget note: Frigate's ROCm detector measured ~0.4 GiB GTT in the earlier test, so GTT total stays well under the ~7.5 GiB usable. The constraint for step 4 is GPU time and memory bandwidth, not capacity.

**Web UI:** `http://<host>:8080/` serves the page (HTTP 200, gzip). Plain `curl` without `--compressed` gets a "gzip is not supported" 415, which is expected and not an error. I verified the page loads and the same `/v1/chat/completions` endpoint with an image works; I did not click through the UI in a browser.

**Gotchas**
- `docker compose up` env overrides: `VLM_IMAGE_MAX_TOKENS=256 docker compose up -d --force-recreate vlm` to try values without editing `.env`.
- The server has no API key and allows all CORS origins (llama.cpp prints a warning). Fine for a LAN booth; bind it to localhost or add `--api-key` if exposed.
- `n_ctx_slot` is ctx / slots: 8192 / 2 = 4096 per slot. A 512-token image plus prompt and answer fits comfortably; raise `VLM_CTX` if you use 1024-token images with long prompts.

**Open for later steps**
- Gemma-3-4B alternative not benchmarked (`scripts/fetch-models.sh gemma`, then set the two `VLM_*_FILE` lines in `.env`).
- Behaviour with Frigate's detector running at the same time is measured in step 4.

## Step 2: simulated RTSP cameras (done 2026-11-10)

**What exists**
- Service `cams` in `compose.yml`: `bluenviron/mediamtx:1.21.3-ffmpeg` (pinned; digest sha256:208e9f17...). `cams/mediamtx.yml` defines paths `cam1`..`cam3`; each path runs `ffmpeg -re -stream_loop -1 -i /media/camN.mp4 -c copy -f rtsp ...` via `runOnInit` with `runOnInitRestart: yes`. No transcoding in the loop. RTSP on `rtsp://<host>:8554/cam1|cam2|cam3` (from other containers: `rtsp://cams:8554/camN`). Only RTSP is enabled.
- Healthcheck: ffprobe must read H.264 from all three paths. `restart: unless-stopped`.
- `scripts/fetch-media.sh` downloads the originals from Wikimedia Commons into `media/src/` (backs off on HTTP 429) and encodes `media/cam1..3.mp4` with the ffmpeg inside the mediamtx image (no host ffmpeg needed): H.264 High, 1920x1080, 15 fps, GOP 30 (keyframe every 2 s), no audio. Sources, authors and licenses: `media/SOURCES.md`. Media files are git-ignored.

**Clips**
| cam | scene | source | length | encoded size |
|---|---|---|---|---|
| cam1 | street crossing with shops, people and cars (CC0) | DiagonalCrosswalkYongeDundas | 56.6 s | 41 MB |
| cam2 | warehouse, overhead, hi-vis vests (public domain, USDA) | 20200716-OSEC-LSC-1159-CLIP-B-HighRes | 23.8 s | 13 MB |
| cam3 | rural road junction, cars and vans (CC0) | Gau-Bischofsheim L 415 | 61.3 s | 23 MB |

Chosen after rejecting: a tornado security camera (Fort Pierce), a talking-head safety video, handheld Mazatlan footage, and many CCTV clips of crimes (not suitable for a booth).

**Verified**
- `ffprobe rtsp://localhost:8554/cam1..3`: h264 High 1920x1080 15 fps on all three.
- All three read concurrently for 65 s with `-c copy`: 956-961 frames each (15 fps), no errors. cam2 (23.8 s) looped almost 3 times without timestamp problems. Keyframes every 2.0 s.
- Container load while serving 3 readers: ~7% of one core, 64 MiB RAM.
- Killing one publisher ffmpeg: it restarts by itself within seconds; `docker compose restart cams` returns to healthy.
- Loop points show a visible jump (clips are short). Expected.

**Frigate check (using the earlier ~/frigate-test stack with these exact mp4 files and the cached ROCm detector)**
- With default motion settings only cam3 got detections (70-80/s). **cam1 and cam2 got 0**: Frigate's motion detector stays in "calibrating" until motion is <5% of the frame AND there are at most 4 motion boxes. Busy scenes (crowd, many workers) never satisfy that, so no detection runs at all.
- Fix that worked: per-camera `motion: {threshold: 50, contour_area: 80, improve_contrast: false}` on cam1 and cam2. Then all three cameras detect (people, cars, vans correct, hi-vis workers detected).
- New issue for step 3/4: with that tuning the three cameras produce ~35 detections/s each (~105/s total, one detector at ~9.5 ms is saturated) and **5-7 frames/s per camera are skipped**. Needs tuning in step 3 (lower `detect.fps` to ~5, larger `min_area`/motion mask, YOLO11n, or a second detector instance).
- No events were created yet in this throwaway config: that needs zones/review settings (step 3).

**Gotchas**
- `docker exec ... pkill -f cam2.mp4` kills the shell running it (the pattern matches its own command line). Use `ps` + `kill <pid>`.
- ffprobe prints "co located POCs unavailable" when it joins a stream mid-GOP. Harmless.
- Wikimedia returns HTTP 429 to rapid or anonymous requests; the script sleeps between files and retries.
- cam2 shows identifiable workers. It is public domain, but check it is acceptable for the audience (noted in SOURCES.md).

## Step 3: Frigate on the iGPU (done 2026-11-10, with one open stability issue)

**What exists**
- Service `frigate` in `compose.yml`: `ghcr.io/blakeblackshear/frigate:0.18.2-rocm` (pinned via `FRIGATE_IMAGE`), `/dev/kfd` + `/dev/dri`, video/render groups, `LIBVA_DRIVER_NAME=radeonsi`, **no** `HSA_OVERRIDE_GFX_VERSION`, shm 512 MB, `/tmp/cache` tmpfs 1 GB, `depends_on: cams (healthy)`. UI on `:8971` (auth disabled), unauthenticated API on `127.0.0.1:5000` only.
- `frigate/config/config.yml` (tracked) and `frigate/config/labelmap-coco80.txt` (tracked). Everything else in `frigate/config/` (db, secrets, `model_cache/`) and `frigate/media/` is git-ignored.
- `scripts/export-yolo.sh [s n ...]` (IMGSZ=320 default) builds `frigate/config/model_cache/yolo11<size>-320.onnx` inside a throwaway `python:3.12-slim` container, nothing needed on the host. Takes about 1-2 min.
- `scripts/frigate-stats.sh [samples] [interval]` prints inference ms, detections/s, skipped/s per camera, Frigate CPU, iGPU busy %, GTT memory.

**Config decisions**
- Cameras read `rtsp://cams:8554/camN` (detect + record roles), detect stream 1280x720 at **5 fps**, VA-API decode (`ffmpeg.hwaccel_args: preset-vaapi`): ffmpeg uses only ~3% CPU per camera.
- Detector: ONNX Runtime + MIGraphX, YOLO11n at 320x320 (`yolo-generic`, nchw, float). **Two detector instances** (`onnx0`, `onnx1`).
- Labels: Frigate's built-in coco-80 labelmap folds bus and truck into "car" (and then refuses to track them). `labelmap-coco80.txt` is the standard COCO-80 list, so person, bicycle, car, motorcycle, bus, truck are distinct (a bus was not seen in these clips, trucks/vans appear as "truck"). "forklift" does not exist in COCO.
- Zones (relative coordinates): cam1 `crosswalk`, cam2 `packing_line`, cam3 `junction`. Alerts require the zone and the label list per camera. Gotcha: write `1.0`, not `1.00`: Frigate compares coordinate strings lexicographically and treats `1.00 > 1.0` as "pixel coordinates", then fails with "Coordinates must be relative".
- Motion (cam1, cam2): `threshold: 50, contour_area: 80, improve_contrast: false` (needed or detection never starts, see step 2).
- Retention: only alerts are recorded, for 0.05 days (~72 min), snapshots 0.05 days, `expire_interval: 5`. The first attempt (1 day) wrote ~2.5 GB of recordings in 13 minutes because the looping clips keep every camera in alert state permanently. With the new settings it was 0.79 GB recordings + 0.14 GB clips after 8 minutes; it should level off around 7-8 GB (not yet observed at steady state, check in the step 6 soak test).
- Timezone Europe/Rome (container `TZ` and `ui.timezone`). Auth disabled (documented in the config).
- The first start compiles the model for gfx1103: about 80 s per model (cached in `frigate/config/model_cache/migraphx`, owned by root, so cleaning it needs sudo).

**Measured (3 cameras, 5 fps, VLM stopped)**

| variant | inference | detections/s | skipped/s | iGPU busy | Frigate CPU |
|---|---|---|---|---|---|
| s, 1 detector, 5 fps (start) | 11 ms | ~92 | ~5 | ~89% | ~30% |
| n, 1 detector | 6 ms | ~86 | ~6 | ~80% | ~28% |
| s, 2 detectors | 12 ms | ~135 | 0.2-2 | ~91% | ~55% |
| **n, 2 detectors (chosen)** | **6-7 ms** | **130-150** | **0.0** | **~66-71%** | **~35%** |
| s, 2 detectors, 3 fps | 14 ms | ~76-90 | 0.0 | ~70-79% | ~28% |

- With one detector the bottleneck is the **Python side of the detector process** (about 90% of one core), not the GPU: even YOLO11n still skipped frames. Two instances fix it. Each detector process then uses ~58% of a core; host CPU is ~60-65% idle.
- Detection volume is high because Frigate runs one inference per motion region (about 6 regions per frame in these busy clips), ~45 inferences/s per camera.
- GPU memory (GTT) with Frigate alone: ~0.4-0.6 GiB. Whole stack idle recordings aside, host RAM use is moderate.
- Events: 200 events in ~4 minutes (82 person + 46 person cam2, cars, bicycles, trucks), zones attached correctly, 42 review items (alerts and detections). Zones and boxes drawn correctly in the debug frames.
- Not measured: accuracy of YOLO11n vs YOLO11s (n looks fine on the debug frames; s is available via `scripts/export-yolo.sh s`) and 416px input.

### (SOLVED in step 4 with `amdgpu.cwsr_enable=0`) GPU hangs when Frigate (ROCm) and the VLM (Vulkan) share the iGPU
- Frigate alone ran many restarts and several 10+ minute stretches without a single GPU error.
- With the VLM container running next to Frigate (VLM loaded in memory, Frigate detecting at 60-90% GPU), the GPU **hung and was reset** three times:
  1. Frigate start while the VLM was loaded (first inference after compile), twice in a row;
  2. starting the VLM container while Frigate was running;
  3. the 2nd VLM request while Frigate was detecting (the first request worked: prompt 3.3 s instead of 2.5 s, generation 28 vs 34 t/s). The VLM got HTTP 500.
- Frigate's log: `HW Exception by GPU node-1 ... reason :GPU Hang`, `Fatal Python error: Aborted`, then the watchdog restarts it. Kernel log: `amdgpu: MES failed to respond to msg=REMOVE_QUEUE`, `failed to remove hardware queue from MES`, `MES might be in unrecoverable state, issue a GPU reset`, then `MODE2 reset`, `GPU reset succeeded`, `device wedged, but recovered through reset`.
- Pattern: it fails when the scheduler (MES) has to create/remove/preempt GPU queues while the GPU is saturated by a compute workload and a second client (Vulkan) is active. It is a kernel/firmware scheduling problem, not a Frigate config problem.
- Things to try (step 4, all untested): (a) kernel boot parameter `amdgpu.cwsr_enable=0` (known workaround for MES queue-eviction hangs on gfx1103; needs a GRUB change and reboot, so ask first); (b) `amdgpu.mes=0`; (c) lower detector pressure so the GPU is not saturated (fewer regions, `detect.fps` 3, YOLO11n); (d) run the VLM through ROCm/HIP instead of Vulkan so both use the same KFD queue path; (e) fall back to detection on the CPU (OpenVINO) with only the VLM on the iGPU.
- Until this is solved, **keep the VLM stopped while Frigate runs** (it is stopped now) or the other way round.

**Not done / caveats**
- The VLM is not connected to Frigate yet (step 4), and the demo UI is step 5.
- Detection accuracy vs YOLO11s was not quantified.
- Only 3-8 minute runs with the final config (no GPU errors); long-run stability is for step 6.

**Gotchas**
- `docker compose restart frigate` leaves old log lines: use `docker logs --since <time>` when counting hangs.
- Files Frigate writes (`frigate.db`, recordings, MIGraphX cache) are owned by root; use `sudo rm` to reset them.
- Frigate's watchdog restarts the whole Frigate process on a detector crash, and the `restart: unless-stopped` policy brings it back; after a GPU reset it usually recovers on its own.

## Step 4: Frigate GenAI with the local VLM + shared-iGPU tuning (done 2026-11-10)

### 1. GPU hang fix: kernel parameter `amdgpu.cwsr_enable=0`
- Added via `/etc/default/grub.d/99-askcam-amdgpu.cfg` (+ `update-grub`, reboot). Undo: delete the file, `sudo update-grub`, reboot. Check: `cat /sys/module/amdgpu/parameters/cwsr_enable` must print `0`. **This is a host requirement for the demo** (step 6 README must say so). `amdgpu.mes` is already 0 by default and is unrelated.
- What it does: disables Compute Wave Save/Restore, so the GPU scheduler (MES) no longer preempts compute queues mid-wave. The hang (`MES failed to respond to msg=REMOVE_QUEUE`) happened exactly when queues were removed/preempted under load.
- `scripts/gpu-stress.sh transitions`: VLM start while detecting, 20 VLM requests, Frigate restart with VLM loaded, 5 requests, VLM restart, 3 VLM stop/start cycles: **0 GPU resets, 0 Frigate GPU hangs** (before the fix the same kind of sequence reset the GPU 3 times in ~10 min).
- `scripts/gpu-stress.sh soak 30`: 30 min, a VLM request every ~12 s while detection runs: **151/151 requests OK, 0 resets, 0 hangs**. VLM wall 3.90 s avg (max 4.69), prompt 3.09 s, generation 23.9 t/s; detector skipped 1.13 frames/s avg (5 fps then).
- About 50 further minutes of real GenAI traffic afterwards: still 0 resets this boot.

### 2. GenAI wiring
- `genai.vlm`: provider **`llamacpp`** (Frigate 0.18 has a native llama.cpp provider; it reads `/v1/models` and `/props`), `base_url: http://vlm-gate:8080`, `model: vlm`, `roles: [descriptions]`, `provider_options: {max_tokens: 70, temperature: 0.2}`.
- `objects.genai`: enabled, **`use_snapshot: true`** (one cropped snapshot per object; the default sends up to 10 thumbnails = 10x the image tokens), trigger on `tracked_object_end` only. Review-item GenAI (multi-frame summaries) is **not** enabled: too heavy for this iGPU next to detection.
- Per-camera prompts (one sentence, at most 25 words, "do not invent details"). An earlier prompt that listed example activities ("operating a pallet jack") made the model claim them as facts; listing activities was removed.
- Per-camera label/zone selection for descriptions: cam1 `[bicycle, motorcycle, bus, truck]` in `crosswalk`; cam2 `[person]` in a new small `aisle` zone (top-left walkway); cam3 `[truck, bus, motorcycle, bicycle]` in `junction`. Alerts/zones for review are unchanged.

### 3. vlm-gate (new service, `vlm-gate/default.conf.template`, nginx 1.29-alpine)
- Why: Frigate starts a **new thread/HTTP request for every finished tracked object, with no concurrency limit**. These looping busy clips end ~170 objects per minute (cam1 alone ~70 people/min). Without a limit, ~150 requests piled up in llama.cpp and 95 timed out (504 after 120 s) while the GPU ran at 100%.
- The gate admits **1 request at a time and at most 8/min** (`VLM_GATE_CONCURRENCY`, `VLM_GATE_RATE` in `.env`); others get an immediate 503 (Frigate logs a warning and that object has no description). `/v1/models`, `/props`, `/health` pass through.
- Gotcha: nginx ignores requests whose `limit_*` key is empty; the first version keyed on `$server_name` (empty) and limited nothing. Now a constant key via `map`.
- Interactive questions (step 5 UI) should call `http://vlm:8080` directly, not the gate.

### 4. Tuning, before/after (3 cameras, all running together)

| setting | described (of ended objects) | object end -> description p50 / p90 | detector skipped/s avg (max) | inference | iGPU busy |
|---|---|---|---|---|---|
| GenAI off (step 3, 5 fps) | - | - | 0.0 | 6-7 ms | ~68% |
| 5 fps, all labels, gate 12/min | 6% | 5.4 s / 8.6 s | 1.19 (2.3) | 12.8 ms | 86% |
| 4 fps, label selection, gate 8/min | 4% (25 in 4 min) | 4.8 s / 8.0 s | 0.02 (0.1) | 9.6 ms | 72% |
| **+ cam2 `aisle` zone (chosen)** | **29 in 5 min, all 3 cameras** | **4.5 s / 7.4 s** | **0.02 (0.3)** | **9.7 ms** | **69%** |
| same, gate 10/min | 32 in 5 min | 4.4 s / 7.3 s | 0.08 (1.2) | 13.4 ms | 72% |

- Chosen: detect **4 fps**, gate **8/min, concurrency 1**, YOLO11n, 2 detectors. ~6 descriptions/min reach the UI; detection keeps up (skips ~0).
- 10/min gives barely more (concurrency 1 already caps throughput at ~7/min with ~4.5 s per request) and brings skips back.
- "Described %" is low by design: ~170 objects end per minute in these clips, and only selected labels/zones are eligible; ~25% of eligible requests are admitted.
- Example descriptions: cam3 truck "The truck is a white van with the text "Polar Granit" on its side, and it is driving through a road junction." cam2 "The person is walking in a warehouse walkway, wearing a yellow hi-vis vest." cam1 "The cyclist is riding through a busy city intersection, heading towards an unknown destination."

### 5. VLM numbers with detection running
- Direct (interactive-style) question on a full 720p frame while detection + GenAI run: wall 3.6-4.9 s, time to first token (prompt eval) 3.0-4.2 s, ~15-token answer. Generation drops to ~8-9 t/s when a GenAI request is running in the other slot at the same time, ~21-24 t/s otherwise (34 t/s with the iGPU idle).
- Detector inference rises from 6-7 ms to ~10-13 ms while the VLM runs.
- GTT memory with everything loaded: ~4.0 GiB of ~7.5 GiB.

### Scripts added
- `scripts/gpu-stress.sh transitions|soak [min]`: repeatable shared-iGPU stress test; counts `GPU reset`/`MES failed`/`wedged` in the kernel log and Frigate "GPU Hang" lines. Logs in `logs/` (git-ignored).
- `scripts/genai-latency.py [min]`: coverage and latency of AI descriptions (object end -> description first seen), plus skipped fps and iGPU busy.

### Caveats / not done
- Latency is measured from the object's end time (Frigate only describes an object when it ends), polled every 2 s, so values are +/-2 s.
- Which eligible objects get a description is first-come at the gate, not by importance.
- Review summaries (Frigate "Review" GenAI) are off; semantic search is off.
- Long-run stability with GenAI on was ~1 h in total; the 60-minute soak is step 6.

## Step 5: "Ask the camera" page and live load meters (done)

Service `askcam-ui` (FastAPI + one static page, no build step) on `http://<host>:8000`, `#cam1|#cam2|#cam3` selects a camera. Image `python:3.12-slim`, ~40 MiB RAM, ~2% CPU. Layout is designed for 1920x1080 (dark theme, 20 px base font); below 1100 px width it stacks.

### What it does
- **Live view:** Frigate MJPEG with detection boxes (`/api/<cam>?fps=6&h=540&bbox=1`), proxied through the UI so the browser only talks to :8000. Opening it did not change detection: skipped 0.04/s, detector 7.9 ms (baseline 0.05/s, 8.6 ms).
- **Ask the camera:** `POST /api/ask` grabs a clean (no boxes) 720p frame from `latest.jpg`, sends frame + question to `http://vlm:8080` directly (not through vlm-gate), and relays tokens as server-sent events. A short system prompt asks for at most two sentences, only what is visible. One question at a time (a second one gets HTTP 429 "busy"). 4 preset buttons plus free text. The page shows the frame the model saw, first-word time, tok/s and total time.
- **Events feed:** Frigate `/api/events` of the last 10 minutes, filtered to those with a GenAI description; counter "N described · M objects seen in 10 min".
- **Meters (SSE, 1 Hz, 60 s sparklines):** iGPU compute, detection, language model, video decode, GPU shared memory, CPU, RAM, chip power + temperatures.

### Where each meter comes from
| Meter | Source | Notes |
|---|---|---|
| iGPU compute % | `gpu_busy_percent`, sampled 5 Hz, averaged per second | whole-GPU number, all engines together |
| Detection | Frigate `/api/stats` (every 2 s) | inferences/s = sum of `detection_fps`; bar = detector duty = inferences/s x ms / number of detectors |
| Language model | llama.cpp `/slots` at 5 Hz (share of the second the server was busy) and `/metrics` (tokens_predicted_total delta = tok/s) | state "idle / reading image / answering"; image+prompt tok/s only shown when the counter moves |
| Video decode | stream count x source fps (env `CAM_SOURCE_FPS`, 15) and Frigate `ffmpeg_cpu` | **estimate of work, not a hardware counter**: amdgpu exposes no VCN utilisation here (`gpu_metrics` `average_mm_activity` stays 0 while 3 streams decode) |
| GTT memory | `mem_info_gtt_used/total` | |
| CPU / RAM | `/proc/stat`, `/proc/meminfo` (host values, they are not namespaced) | |
| Power / temp | amdgpu hwmon `power1_average` (whole APU socket, ~27-29 W under demo load), `k10temp` | RAPL `energy_uj` is root-only, so it is not used |

Compose mounts `/sys` read-only at `/host/sys`; the amdgpu card is found by PCI vendor 0x1002 plus the presence of `gpu_busy_percent`, so the card index does not matter. A per-engine split of iGPU compute (detection vs VLM) is not available: DRM fdinfo (`drm-engine-compute`) shows only the Vulkan process, ROCm/KFD queues are not listed, and reading it needs root plus the host PID namespace. The tiles therefore show each engine's own activity next to the total.

### Measured (`scripts/ui-test.py 3`: 9 questions across 3 cameras, everything running)
| | Result |
|---|---|
| First word | p50 3.2 s, range 2.9-3.8 s (frame grab ~40 ms; most of it is image encoding + prompt on the iGPU) |
| Speed | median 23.8 tok/s; a few answers 4-6 tok/s when a Frigate description ran at the same moment |
| Total per answer | 3.4-5.5 s |
| Detection while answering | skipped 0.43/s on average (max 1.8/s) of ~12 fps total, vs 0.05/s idle; detector 13.2 ms (max 15.6) vs 8.6 ms |

Detection never stops, but it is not completely free: frames are skipped in bursts while the VLM reads the image. Roughly 3-4% of frames in the test. The "no skipped frames at all" goal is not met; the step 4 average (0.02/s) holds without questions.

### Known limits
- Browser check done only with a headless Firefox screenshot of a populated page at 1920x1080 (layout, no text overflow after shortening the sub-labels). The Chrome extension was not connected, so an actual click-through in a browser was not done; the same API calls are covered by `scripts/ui-test.py`.
- The UI port is unauthenticated and the page proxies Frigate read-only (live, thumbnails, event list). Fine for a booth LAN, do not expose it.
- Answers are from a 3B model: counts and "without helmet" style judgements are sometimes wrong (e.g. it called a cyclist helmet-less). Presets are intentionally generic.
- Frigate described-percentage is still low by design (step 4), so the feed updates every ~10 s, not every object.

### Step 5b: switch the language model from the UI

The header button **Model: ...** opens a table of models (from `vlm/models.ini`). One click on **Load** unloads the current model and loads the chosen one, with all cameras and detection still running; the table shows, per model, what it costs and what it gives, measured while that model was loaded.

How it works
- `vlm` now runs llama.cpp in **router mode** (`--models-preset /config/models.ini --models-max 1`). Each `[section]` of `vlm/models.ini` is a model with its own settings (context, slots, image tokens). Router API: `GET /models`, `POST /models/load|unload`.
- Two router quirks that shaped the design: (1) every request must name a model that exists, so Frigate's fixed `model: vlm` fails; (2) **any** request naming an unloaded model (even `/slots?model=...&autoload=false`) loads it. So `vlm-gate` now forwards chat requests to `askcam-ui` (`/llm/v1/chat/completions`), which rewrites the model name to the loaded one and returns 503 during a switch; the UI also stops polling the VLM while switching. Duplicate aliases are rejected by the router, so one shared alias was not an option.
- `askcam-ui` reads `models.ini` (mounted read-only) only for display: file sizes, settings, and a fit warning (estimate = file size + 1 GiB vs. GPU shared memory and RAM left; "should fit / tight fit / probably does not fit"). A comment line `; size-gib: N` in a section lets the UI judge the fit before the files are downloaded.
- Changes to `models.ini` need `docker compose restart vlm` (the router reads it at start); the UI shows "new preset: restart vlm" until then. Changes to `vlm-gate/default.conf.template` need `docker compose up -d --force-recreate vlm-gate`.
- If a load fails the previous model is reloaded and the error is shown in the dialog.
- Per-model numbers are kept in askcam-ui memory (reset when that container restarts).

Presets in `vlm/models.ini`: `qwen2.5-vl-3b` (default), `qwen2.5-vl-3b-lite` (same files, 256 image tokens), `gemma-3-4b` (`scripts/fetch-models.sh gemma`, 3.1 GiB) and `qwen2.5-vl-7b` (`scripts/fetch-models.sh qwen7b`, 5.2 GiB, context 4096, 1 slot). Gemma and the 7B are **not downloaded and not tested**; for the 7B the UI estimates ~6.5 GiB GPU / ~13-14 GiB RAM and shows "tight fit".

First measurements (same cameras, GenAI running, 9 and 6 questions):
| Model | GPU memory growth | First word (median) | Speed | Switch time |
|---|---|---|---|---|
| qwen2.5-vl-3b (512 image tokens) | 3.22 GiB | 3.2 s | 23.2 tok/s | 5.5 s (files cached) |
| qwen2.5-vl-3b-lite (256 image tokens) | 3.17 GiB | **1.8 s** | 24.6 tok/s | 4.0 s |

So halving the image tokens cuts first word by ~1.4 s at no memory cost; what it costs in answer detail has not been judged systematically. Detector time and skipped frames stayed in the normal range for both.

### Step 5c: Gemma-3-4B and Qwen2.5-VL-7B compared with the 3B (measured 2026-11-10)

Setup: all 3 cameras, detection (2 detectors) and Frigate GenAI running, 9 questions per model through the UI (`scripts/ui-test.py 3`), plus 12 identical (frame, question) pairs per model (`scripts/model-compare.py`, answers in `logs/compare/`, frames fixed so every model sees the same images). Switch with `scripts/switch-model.sh <preset>`.

| Model | GPU memory | First word | Speed | Image + prompt (same frames) | Detector avg | Skipped frames | Switch time |
|---|---|---|---|---|---|---|---|
| qwen2.5-vl-3b-lite (256 img tok) | 3.2 GiB | **1.8 s** | 24.6 tok/s | 2.1 s | 8.5 ms | 0.02/s | 4 s |
| qwen2.5-vl-3b (default, 512 img tok) | 3.2 GiB | 3.2 s | 23.2 tok/s | 3.5 s | 9.6 ms | 0.07-0.11/s | 5.5 s |
| gemma-3-4b | 4.4 GiB | 6.5 s | 11.3 tok/s (9.6 on same frames) | 6.3 s | 25-28 ms | 0.7-1.1/s | 6-7 s |
| qwen2.5-vl-7b (ctx 4096, 1 slot) | 5.7 GiB | 7.4 s | 12.4 tok/s | 5.1 s | 25-32 ms | 2.2-3.0/s | 10 s |

Both bigger models **fit and run** (no OOM, no GPU reset), but they cost detection: skipped frames reach 2-3/s of ~12 fps with the 7B (about 20%), and the detector slows from ~9 ms to ~25-30 ms while a question or description is being processed. The 7B also runs with one slot, so Frigate descriptions and UI questions queue behind each other. The GPU sits at 90-97% busy.

Quality on the 12 identical pairs, judged against the frames (I looked at them):
- **Safety gear on cam2** (everyone wears a vest): 7B correct ("no one is without"); 3B and 3B-lite wrong ("Yes, at least one person"); Gemma hedged ("cannot determine").
- **Vehicle counts** (cam1 about 6-7 cars, cam3 about 7 incl. distant ones): 3B 5 and 3; lite 10 and 3; 7B "at least 10" and "at least 5"; Gemma "several", never a number. No model counts reliably.
- **Hallucinations:** Gemma invented "candies" and "pedestrians without helmets"; lite called the workers "volunteers" and said "no unusual" with less detail; the 7B was the most careful ("no one visible, so impossible to determine"). 
- **Style:** the 7B and 3B give concise answers; Gemma is wordier and hedges.
Overall: 7B is clearly the most accurate, 3B is the best speed/quality compromise, lite trades detail for ~1.4 s, Gemma is slower than the 3B and not better on this task. Sample is small (12 answers), so treat it as indicative.

Recommendation for the booth: keep **qwen2.5-vl-3b** as the default; use the **7B** only for a "better answers" moment with Frigate descriptions paused or the demo at fewer cameras, accepting ~7 s to first word.

### Memory findings made while doing this (important for step 6)
- **`frigate.embeddings_manager` grows about 5 GiB per hour** (RSS +0.09 GiB/min, linear, also with no GenAI calls in progress) and was OOM-killed three times (15:11 and 16:10 today, and an earlier host-wide OOM at 14:21 killed `llama-server` itself). Its job here is running the GenAI description processor. Root cause not found (Frigate 0.18.2; `FRIGATE_MEMRAY_MODULES=frigate.embeddings` would profile it). Swap was full (4/4 GiB) when it happened, which also explains erratic tok/s in the first Gemma run.
- **Mitigation applied:** Frigate now has `mem_limit: 5g` and `memswap_limit: 5g` in `compose.yml`. A leaking process now dies inside Frigate's cgroup and Frigate's watchdog restarts it (a few seconds without descriptions) instead of the kernel killing the VLM. Not yet verified over a full cycle: step 6's soak must log this process's RSS and the number of restarts.
- **Restart bug fixed:** when that process restarts, Frigate's llama.cpp provider re-validates the model name `vlm` against `/v1/models`; the router does not list `vlm`, so descriptions silently stopped ("llama.cpp provider has not been initialized"). `vlm-gate` now serves `/v1/models` and `/props` through askcam-ui under the name `vlm`; verified by restarting Frigate (log: "llama.cpp model 'vlm' initialized").
- RAM is the tightest resource on this 14.4 GiB machine (GPU memory is carved from it): with the 3B about 9.8 GiB used / 4.9 GiB available after Frigate restart; with Gemma or the 7B it dropped to 1.3-2.7 GiB available and swap was used.
