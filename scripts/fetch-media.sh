#!/usr/bin/env bash
# Download the demo clips (see media/SOURCES.md) and re-encode them to media/cam1..3.mp4.
# ffmpeg runs in the mediamtx image already used by the stack, so no host ffmpeg is needed.
set -euo pipefail
cd "$(dirname "$0")/.."
. ./.env 2>/dev/null || true
IMAGE=${MEDIAMTX_IMAGE:-bluenviron/mediamtx:1.21.3-ffmpeg}
UA="askcam-lab-demo/1.0 (+https://aaeon.eu) curl"
mkdir -p media/src

fetch() {  # fetch <Commons file name> <local name>
  local name=$1 out=media/src/$2
  [ -s "$out" ] && { echo "ok (cached): $out"; return; }
  local url="https://commons.wikimedia.org/wiki/Special:FilePath/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$name")"
  echo "downloading: $name"
  for i in 1 2 3 4 5 6; do
    if curl -fsSL -A "$UA" --retry 2 -o "$out.part" "$url"; then mv "$out.part" "$out"; return; fi
    echo "  retry $i (rate limited?), waiting $((i*20))s"; sleep $((i*20))
  done
  echo "failed: $name" >&2; exit 1
}

encode() {  # encode <src> <dst>
  [ -s "media/$2" ] && { echo "ok (cached): media/$2"; return; }
  echo "encoding: $2"
  docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/media:/media" --entrypoint ffmpeg "$IMAGE" \
    -hide_banner -loglevel error -y -err_detect ignore_err -i "/media/src/$1" \
    -vf "fps=15,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,format=yuv420p" \
    -an -c:v libx264 -preset slow -crf 23 -g 30 -keyint_min 30 -sc_threshold 0 -movflags +faststart "/media/$2"
}

fetch "DiagonalCrosswalkYongeDundas.webm"                        cam1.webm; sleep 5
fetch "20200716-OSEC-LSC-1159-CLIP-B-HighRes.webm"               cam2.webm; sleep 5
fetch "Gau-Bischofsheim - Eine Minute an der L 415.webm"         cam3.webm
encode cam1.webm cam1.mp4
encode cam2.webm cam2.mp4
encode cam3.webm cam3.mp4
ls -lh media/*.mp4
