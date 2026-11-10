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
