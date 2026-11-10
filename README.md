# askcam: an AI camera demo that runs entirely on one integrated GPU

Three simulated 1080p cameras → **Frigate** (VA-API video decode + ROCm object detection) → **llama.cpp + Qwen2.5-VL** (Vulkan) writing descriptions and answering typed questions, with a booth page showing live load. Everything runs on the Radeon 760M of a Ryzen 5 PRO 8640U (AAEON GENE-R8K6); nothing leaves the machine. Docker Compose, no cloud.

```
 mediamtx (3 looping clips, RTSP) → Frigate (decode on VCN, YOLO11n via ROCm/MIGraphX, 2 detectors)
                                      │ GenAI: one snapshot per finished object
                                      ▼
                                   vlm-gate (nginx: 1 at a time, 8/min) → askcam-ui /llm → vlm (llama.cpp router, Vulkan)
 browser → askcam-ui :8000  (live view, "ask the camera", AI event feed, load meters, model switcher) ──┘
```

## Requirements
- AMD Ryzen APU with a Radeon iGPU (tested: Ryzen 5 PRO 8640U, Radeon 760M = gfx1103), 16 GB RAM (RAM is tight: the GPU memory comes out of it, see Troubleshooting), ~15 GB free disk.
- Ubuntu 24.04+ with the `amdgpu` and `amdkfd` kernel drivers (tested: Ubuntu 26.04, kernel 7.0); `/dev/kfd` and `/dev/dri/renderD*` must exist.
- **Kernel parameter `amdgpu.cwsr_enable=0` is MANDATORY.** Without it the GPU hangs and resets within minutes when detection (ROCm) and the VLM (Vulkan) run together (`MES failed to respond to msg=REMOVE_QUEUE`, see NOTES.md step 4). Set it once and reboot:
  ```bash
  echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT amdgpu.cwsr_enable=0"' | sudo tee /etc/default/grub.d/99-askcam-amdgpu.cfg
  sudo update-grub && sudo reboot
  ```
  Check after reboot: `cat /sys/module/amdgpu/parameters/cwsr_enable` must print `0`.
- Docker Engine + Compose plugin from **download.docker.com** (not the Ubuntu packages), your user in the `docker`, `render` and `video` groups.

## Quick start (with internet)
```bash
cp .env.example .env
# group IDs differ per machine: put yours into .env
sed -i "s/^VIDEO_GID=.*/VIDEO_GID=$(getent group video | cut -d: -f3)/; s/^RENDER_GID=.*/RENDER_GID=$(getent group render | cut -d: -f3)/" .env
scripts/fetch-models.sh qwen     # 2.6 GB, Qwen2.5-VL-3B (default). Also: gemma | qwen7b
scripts/fetch-media.sh           # three CC0/public-domain clips from Wikimedia Commons (see media/SOURCES.md)
scripts/export-yolo.sh n         # YOLO11n 320 ONNX for Frigate (uses a throw-away python container)
docker compose up -d --build     # or: make up
```
First start: Frigate compiles the detection model for the GPU (MIGraphX, about 1-2 minutes) and caches it in `frigate/config/model_cache`; descriptions begin once the first tracked objects end.

| URL | What |
|---|---|
| http://HOST:8000 | **the demo page** (add `#cam2` to open a camera directly) |
| https://HOST:8971 | Frigate's own UI (self-signed certificate, first-run credentials are printed in `docker logs askcam-frigate`) |
| rtsp://HOST:8554/cam1 | the simulated cameras |
| http://HOST:8080 | llama.cpp router API (`/models`, `/v1/chat/completions`) |

Useful: `make ps | logs | down | test | bundle`. `make test` asks 9 questions through the UI and prints first-word time and skipped frames.

## Offline use
```bash
scripts/bundle-offline.sh [--all-models]      # one tarball: repo, images, models, clips, compiled cache (6.9 GB with the 3B only)
scripts/restore-offline.sh askcam-offline-YYYYMMDD.tar [target-dir]   # on the machine with no internet
```
The target machine still needs the kernel parameter, Docker and the group memberships above.

## Changing the language model
Open the **Model** menu in the page: it lists every preset in `vlm/models.ini` with measured GPU memory, first-word time, speed and the effect on detection, and loads one in 4-10 s. To add a model: put the files in `models/`, add a `[section]` (context, slots, image tokens) to `vlm/models.ini`, then `make restart-vlm`. Measured comparison of the 3B, Gemma-3-4B and 7B: NOTES.md step 5c (the 3B is the best compromise on this machine).

## Configuration
Everything host-specific is in `.env` (see `.env.example`): group IDs, ports, pinned image digests, `VLM_GATE_RATE` (Frigate descriptions per minute, default 8). Frigate: `frigate/config/config.yml`. After changing `vlm-gate/default.conf.template` run `docker compose up -d --force-recreate vlm-gate`; after changing `frigate/config/config.yml` run `docker compose restart frigate`.

## Troubleshooting
- **GPU hang / `MES failed` in `journalctl -k`, Frigate "GPU Hang":** the kernel parameter above is missing.
- **Containers can't open the GPU:** `VIDEO_GID`/`RENDER_GID` in `.env` do not match `getent group video render`.
- **Check VA-API decode:** `docker exec askcam-frigate vainfo` should list H264 decode; Frigate's `ffmpeg_cpu` per camera stays near 3-10 %.
- **Out of memory / swap in use:** CPU and GPU share the same 16 GB. `gpu_busy`, `mem_info_gtt_used` and `free -m` show it; the 3B needs ~3.2 GiB of GPU memory, the 7B ~5.7 GiB and then swaps. Frigate's `frigate.embeddings_manager` (GenAI) slowly leaks memory in this setup; the Frigate container has a 5 GB limit so only that process is restarted (NOTES.md step 5c).
- **Frigate says "calibrating" / no detections with your own clips:** motion detection needs under 5 % of the frame in motion; see the motion tuning in `config.yml`.
- **No AI descriptions:** only a few percent of objects get one by design (rate limit, zones); check `docker logs askcam-frigate | grep llama` and that a model is loaded in the Model menu.
- **NIC runs at 100 Mb/s:** not a driver issue on this unit, it was the cable/switch port.

## Layout
`compose.yml`, `.env.example`, `Makefile` · `askcam-ui/` (FastAPI + one static page) · `vlm/models.ini` · `vlm-gate/` · `cams/` · `frigate/config/config.yml` · `scripts/` (fetch, bundle/restore, tests, soak) · `NOTES.md` (all measurements) · `PLAN.md` · `DEMO.md` (3-minute booth script).
