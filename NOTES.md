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

### OPEN ISSUE: GPU hangs when Frigate (ROCm) and the VLM (Vulkan) share the iGPU
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
