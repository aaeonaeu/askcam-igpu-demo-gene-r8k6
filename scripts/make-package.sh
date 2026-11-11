#!/usr/bin/env bash
# Build the single compressed deliverable: bundle + Docker .debs + setup.sh  ->  askcam-demo-YYYYMMDD.tar.zst
# Usage: scripts/make-package.sh [--all-models]        (run on a machine that has the demo built and internet for apt-get download)
set -euo pipefail
cd "$(dirname "$0")/.."
D=askcam-demo-$(date +%Y%m%d); OUT=$D.tar.zst
rm -rf "$D"; mkdir -p "$D/debs"
scripts/bundle-offline.sh "$@" "$PWD/$D/askcam-offline-$(date +%Y%m%d).tar"
echo "== Docker .deb files (exact versions installed here)"
( cd "$D/debs" && apt-get download $(dpkg -l | awk '/^ii +(docker-ce|docker-ce-cli|containerd.io|docker-buildx-plugin|docker-compose-plugin)(:| )/{print $2"="$3}') )
cp scripts/setup-demo.sh "$D/setup.sh"; chmod +x "$D/setup.sh"
cat > "$D/README-FIRST.txt" <<'T'
askcam demo package (offline). On the target machine (Ubuntu 26.04, Ryzen APU, 16 GB RAM, 25 GB free):

  tar -I zstd -xf askcam-demo-*.tar.zst && cd askcam-demo-*/
  ./setup.sh                # asks for sudo; if it sets the kernel parameter it tells you to reboot, then run ./setup.sh again

It installs Docker from the bundled .deb files if needed, sets the group memberships and the mandatory kernel
parameter amdgpu.cwsr_enable=0, loads the images/models and starts the demo on http://<machine-ip>:8000.
Licences of the bundled models/images: see askcam/THIRD_PARTY.md inside the bundle (the default 3B model and YOLO11 are
non-commercial/AGPL: lab and trade-show demo use only, do not redistribute this package).
T
( cd "$D" && sha256sum setup.sh debs/*.deb > SHA256SUMS )
echo "== compressing (zstd, all cores)"
tar -I 'zstd -T0 -6' -cf "$OUT" "$D"; rm -rf "$D"
sha256sum "$OUT" | tee "$OUT.sha256"; ls -lh "$OUT"
