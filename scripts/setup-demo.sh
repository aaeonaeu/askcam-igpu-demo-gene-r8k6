#!/usr/bin/env bash
# One-shot setup of the askcam demo on a GENE-R8K6-class machine (Ubuntu 26.04, Ryzen APU with Radeon iGPU), offline.
# Lives next to the bundle in the unpacked package:  ./setup.sh [target-dir]   (default target: ~/askcam)
# It is safe to run again: every step checks first.  It uses sudo (you will be asked for your password).
#   1. checks the machine   2. installs Docker + compose from the bundled .deb files if missing
#   3. adds you to docker/render/video   4. sets the kernel parameter amdgpu.cwsr_enable=0 (reboot, then run again)
#   5. loads images + models and starts the demo   6. waits until every container is healthy
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
TARGET=${1:-$HOME/askcam}
BUNDLE=$(ls "$HERE"/askcam-offline-*.tar 2>/dev/null | head -1 || true)
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || die "run as your normal user, not root (the script calls sudo where needed)"
[ -n "$BUNDLE" ] || die "no askcam-offline-*.tar next to this script"
[ ! -f "$BUNDLE.sha256" ] || { say "Checking the bundle checksum"; (cd "$HERE" && sha256sum -c "$(basename "$BUNDLE").sha256"); }

say "1/6 Checking the machine"
. /etc/os-release; echo "OS: $PRETTY_NAME, kernel $(uname -r), $(uname -m)"
[ "$(uname -m)" = x86_64 ] || die "x86_64 required"
[ "${ID:-}" = ubuntu ] || echo "WARNING: tested on Ubuntu 26.04 only"
[ -e /dev/kfd ] || die "/dev/kfd missing: the amdgpu/amdkfd kernel drivers are not loaded"
ls /dev/dri/renderD* >/dev/null 2>&1 || die "no /dev/dri/renderD*: no GPU render node"
GFX=$(cat /sys/class/kfd/kfd/topology/nodes/*/properties 2>/dev/null | awk '/gfx_target_version/ && $2>0 {print $2; exit}')
echo "GPU gfx_target_version: ${GFX:-unknown} (the bundled detection cache is for 110003 = gfx1103 / Radeon 760M)"
[ "${GFX:-110003}" = 110003 ] || echo "WARNING: different GPU; Frigate will recompile the detection model on first start (slower, should still work)"
FREE_GB=$(df -BG --output=avail "$HOME" | tail -1 | tr -dc 0-9); [ "$FREE_GB" -ge 25 ] || die "need ~25 GB free in $HOME (have ${FREE_GB} GB)"
RAM_GB=$(awk '/MemTotal/{printf "%d", $2/1048576}' /proc/meminfo); [ "$RAM_GB" -ge 14 ] || echo "WARNING: ${RAM_GB} GB RAM; the demo needs 16 GB"

say "2/6 Docker Engine + Compose plugin"
if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
  echo "already installed: $(docker --version)"
else
  ls "$HERE"/debs/*.deb >/dev/null 2>&1 || die "Docker is missing and no debs/ folder is bundled; install Docker from download.docker.com first"
  sudo apt-get install -y --no-download "$HERE"/debs/*.deb 2>&1 | tail -3 || sudo dpkg -i "$HERE"/debs/*.deb
  sudo systemctl enable --now docker
fi

say "3/6 Groups (docker, render, video)"
for g in docker render video; do
  getent group "$g" >/dev/null || { echo "group $g missing"; continue; }
  id -nG "$USER" | tr ' ' '\n' | grep -qx "$g" || { sudo usermod -aG "$g" "$USER"; echo "added $USER to $g (active after the next login; this script uses 'sg' meanwhile)"; }
done

say "4/6 Kernel parameter amdgpu.cwsr_enable=0 (mandatory: without it the GPU hangs)"
CUR=$(cat /sys/module/amdgpu/parameters/cwsr_enable 2>/dev/null || echo ?)
if [ "$CUR" = 0 ]; then
  echo "active"
else
  F=/etc/default/grub.d/99-askcam-amdgpu.cfg
  if ! grep -qs 'amdgpu.cwsr_enable=0' "$F"; then
    echo 'GRUB_CMDLINE_LINUX_DEFAULT="$GRUB_CMDLINE_LINUX_DEFAULT amdgpu.cwsr_enable=0"' | sudo tee "$F" >/dev/null
    sudo update-grub 2>&1 | tail -2
  fi
  echo
  echo "The kernel parameter is configured but not active yet (current value: $CUR)."
  echo ">>> Reboot now:  sudo reboot      then run this script again: $HERE/setup.sh"
  exit 0
fi

say "5/6 Loading images and models, starting the demo into $TARGET"
RUN() { if docker info >/dev/null 2>&1; then "$@"; else sg docker -c "$(printf '%q ' "$@")"; fi; }
tar -xOf "$BUNDLE" askcam/scripts/restore-offline.sh > "$HERE/.restore.sh"
RUN bash "$HERE/.restore.sh" "$BUNDLE" "$TARGET"; rm -f "$HERE/.restore.sh"

say "6/6 Waiting for the containers (first start compiles nothing: the detection cache is bundled)"
cd "$TARGET"
for _ in $(seq 1 60); do
  n=$(RUN docker compose ps --format '{{.Status}}' | grep -c healthy || true)
  [ "$n" -ge 5 ] && break; sleep 5
done
RUN docker compose ps --format 'table {{.Service}}\t{{.Status}}'
[ "$n" -ge 5 ] || die "not all 5 containers are healthy; see: cd $TARGET && docker compose logs"
IP=$(hostname -I | awk '{print $1}')
cat <<MSG

Demo is up.
  Open  http://$IP:8000   (the demo page; add #cam2 to jump to a camera)
  Frigate UI https://$IP:8971
  Stop:  cd $TARGET && docker compose down        Start again:  docker compose up -d
  Booth script: $TARGET/DEMO.md      Details: $TARGET/README.md
MSG
