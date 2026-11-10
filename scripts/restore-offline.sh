#!/usr/bin/env bash
# Restore the demo from a bundle made by bundle-offline.sh, with no internet, and start it.
# Usage: scripts/restore-offline.sh askcam-offline-YYYYMMDD.tar [target-dir]   (default target: ./askcam)
set -euo pipefail
TAR=$(readlink -f "$1"); T=${2:-askcam}
[ -f "$TAR.sha256" ] && (cd "$(dirname "$TAR")" && sha256sum -c "$(basename "$TAR").sha256")
mkdir -p "$T"; T=$(readlink -f "$T")
S=$(mktemp -d "$T/.restore.XXXXXX"); trap 'rm -rf "$S"' EXIT
tar -C "$S" -xf "$TAR"
cp -a "$S/askcam/." "$T/"
docker load -i "$S/images.tar"
cd "$T"
mkdir -p models media frigate/config/model_cache
cp -n "$S"/payload/models/* models/; cp -n "$S"/payload/media/* media/
cp -rn "$S"/payload/frigate-config/model_cache/. frigate/config/model_cache/
if [ ! -f .env ]; then
  cp .env.example .env
  V=$(getent group video | cut -d: -f3); R=$(getent group render | cut -d: -f3)
  sed -i "s/^VIDEO_GID=.*/VIDEO_GID=$V/; s/^RENDER_GID=.*/RENDER_GID=$R/" .env
fi
docker compose up -d --no-build
echo "Started. UI: http://$(hostname -I | awk '{print $1}'):$(grep -oP '^UI_PORT=\K.*' .env || echo 8000)"
