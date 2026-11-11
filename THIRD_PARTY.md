# Third-party components and their licences

The MIT licence in `LICENSE` covers only the files in this repository (compose files, configs, scripts, `askcam-ui`, docs). The one exception is `frigate/patches/`: two source files of Frigate 0.18.2 with a local fix, under Frigate's MIT licence (`frigate/patches/LICENSE.frigate`). The repository contains **no** third-party binaries, container images, models or video clips; `scripts/fetch-*.sh` and `docker compose` download them from their original sources, and each keeps its own licence. Check them before any use beyond an internal demo, and especially before redistributing an offline bundle (`scripts/bundle-offline.sh` packs images, models and clips together).

| Component | Used for | Licence (as checked / to my knowledge) |
|---|---|---|
| Frigate 0.18.2 (image) | detection, recording, GenAI | MIT |
| llama.cpp (image) | VLM server, Vulkan | MIT |
| MediaMTX (image) | simulated RTSP cameras | MIT |
| nginx (image) | `vlm-gate` | BSD-2-Clause |
| Python image, FastAPI, uvicorn, httpx | `askcam-ui` | PSF / MIT / BSD-3 |
| **Qwen2.5-VL-3B-Instruct** (default model) | VLM | **Qwen Research License** per the original model card (non-commercial research use). The `ggml-org` GGUF conversion is tagged Apache-2.0, which does not override the upstream licence. Read https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct before commercial use. |
| Qwen2.5-VL-7B-Instruct (optional) | VLM | Apache-2.0 (model card) |
| Gemma-3-4B-it (optional) | VLM | Gemma Terms of Use (use restrictions apply) |
| **YOLO11n** (Ultralytics, exported by `scripts/export-yolo.sh`) | object detection | **AGPL-3.0** (commercial use needs an Ultralytics enterprise licence) |
| Demo clips (`scripts/fetch-media.sh`) | simulated cameras | CC0 / public domain from Wikimedia Commons, see `media/SOURCES.md` (the warehouse clip shows identifiable workers) |

Practical consequence: the demo is fine as a lab or trade-show demonstration; a product or a public download of the bundle would need a commercially licensed VLM (e.g. the 7B, Apache-2.0) and detector (e.g. another permissively licensed model) in place of the 3B and YOLO11.
