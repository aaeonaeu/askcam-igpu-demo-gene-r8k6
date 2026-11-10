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
