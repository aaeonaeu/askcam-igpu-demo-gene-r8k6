#!/usr/bin/env bash
# Export YOLO11 detection models to ONNX for Frigate's ONNX detector.
# Runs in a throwaway python container (CPU torch), so the host needs nothing but Docker.
# Usage: scripts/export-yolo.sh [MODEL_SIZE ...]   e.g. scripts/export-yolo.sh s n   (default: s)
#        IMGSZ=320 (default) or 416/640
# Output: frigate/config/model_cache/yolo11<size>-<imgsz>.onnx
set -euo pipefail
cd "$(dirname "$0")/.."
SIZES=("${@:-s}")
IMGSZ=${IMGSZ:-320}
OUT=frigate/config/model_cache
mkdir -p "$OUT" .cache/yolo-export
for s in "${SIZES[@]}"; do
  dst="$OUT/yolo11${s}-${IMGSZ}.onnx"
  [ -s "$dst" ] && { echo "ok (cached): $dst"; continue; }
  echo "exporting yolo11${s} at ${IMGSZ}px ..."
  docker run --rm --user "$(id -u):$(id -g)" -e HOME=/work -e YOLO_CONFIG_DIR=/work/.cfg -e PIP_CACHE_DIR=/work/.pip \
    -v "$PWD/.cache/yolo-export:/work" -v "$PWD/$OUT:/out" -w /work python:3.12-slim bash -ec "
      pip install --user -q --extra-index-url https://download.pytorch.org/whl/cpu torch torchvision ultralytics onnx onnxslim 2>&1 | tail -2
      # the slim image has no GUI libs: swap opencv for the headless build
      pip uninstall -y -q opencv-python >/dev/null 2>&1 || true
      pip install --user -q --force-reinstall --no-deps opencv-python-headless 2>&1 | tail -1
      export PATH=\$PATH:/work/.local/bin
      python - <<PY
from ultralytics import YOLO
p = YOLO('yolo11${s}.pt').export(format='onnx', imgsz=${IMGSZ}, opset=17, simplify=True, dynamic=False)
import shutil; shutil.copy(p, '/out/yolo11${s}-${IMGSZ}.onnx')
PY"
  ls -lh "$dst"
done
