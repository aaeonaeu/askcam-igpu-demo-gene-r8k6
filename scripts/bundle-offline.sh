#!/usr/bin/env bash
# Build one tarball that restores the whole demo on a machine WITHOUT internet:
# repo files (git HEAD), all docker images (docker save), model files, demo clips, YOLO ONNX + compiled MIGraphX cache.
# Usage: scripts/bundle-offline.sh [--all-models] [output.tar]     (default: default 3B model only)
set -euo pipefail
cd "$(dirname "$0")/.."
ALL=0; [ "${1:-}" = "--all-models" ] && { ALL=1; shift; }
OUT=${1:-askcam-offline-$(date +%Y%m%d).tar}
W=$(mktemp -d "$PWD/.bundle.XXXXXX"); trap 'rm -rf "$W"' EXIT
mkdir -p "$W/askcam"
echo "== repo (git HEAD)"; git archive HEAD | tar -x -C "$W/askcam"
set -a; . ./.env; set +a
echo "== images"
docker compose build askcam-ui >/dev/null
IMAGES=$(docker compose config --images | sort -u)
echo "$IMAGES"; docker save -o "$W/images.tar" $IMAGES
echo "== models / media / frigate model + cache"
mkdir -p "$W/payload/models" "$W/payload/media" "$W/payload/frigate-config"
if [ $ALL = 1 ]; then cp -v models/*.gguf "$W/payload/models/"
else cp -v models/Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf models/mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf "$W/payload/models/"; fi
cp -v media/*.mp4 "$W/payload/media/"
# the cache is written by root inside the Frigate container: copy it with a throw-away container (no sudo needed)
docker run --rm -v "$PWD/frigate/config/model_cache:/src:ro" -v "$W/payload/frigate-config:/dst" \
  --entrypoint sh "${NGINX_IMAGE:-nginx:1.29-alpine}" -c "cp -a /src /dst/model_cache && chown -R $(id -u):$(id -g) /dst"
cp .env "$W/payload/env.used"   # reference only: GIDs of the build host
echo "== tar"; tar -C "$W" -cf "$OUT" askcam images.tar payload
sha256sum "$OUT" | tee "$OUT.sha256"; ls -lh "$OUT"
