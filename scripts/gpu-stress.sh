#!/usr/bin/env bash
# Repeatable shared-iGPU stress test: Frigate (ROCm detector) + VLM (llama.cpp Vulkan).
# Usage: scripts/gpu-stress.sh transitions        # start/stop/restart each service under load + 20 VLM requests
#        scripts/gpu-stress.sh soak [minutes]     # mixed load: VLM request every ~10 s while detection runs (default 30)
# Counts GPU resets / MES failures in the kernel log since the test started. Log: logs/gpu-stress-<mode>-<time>.log
set -uo pipefail
cd "$(dirname "$0")/.."
MODE=${1:-transitions}; MIN=${2:-30}

LOG=logs/gpu-stress-$MODE-$(date +%Y%m%d-%H%M%S).log
T0=$(date '+%Y-%m-%d %H:%M:%S')
exec > >(tee "$LOG") 2>&1

frame() {  # grab a current camera frame from Frigate as the VLM test image
  curl -s -m 5 -o logs/stress-frame.jpg "localhost:5000/api/cam$((RANDOM % 3 + 1))/latest.jpg?h=720" && [ -s logs/stress-frame.jpg ]
}
resets() { journalctl -k --since "$T0" --no-pager | grep -ciE 'GPU reset begin|MES failed|device wedged'; }
hangs()  { docker logs --since "$(date -d "$T0" +%Y-%m-%dT%H:%M:%S)" askcam-frigate 2>&1 | grep -c 'GPU Hang'; }
wait_healthy() { local i; for i in $(seq 1 60); do [ "$(docker inspect -f '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ] && return 0; sleep 3; done; return 1; }
vlm_req() {  # one request; prints "ok <wall s> <ttft ms> <gen t/s>" or "FAIL <code>"
  frame || { echo "FAIL noframe"; return; }
  python3 -I - <<'PY'
import base64, json, time, urllib.request
img = base64.b64encode(open("logs/stress-frame.jpg", "rb").read()).decode()
body = json.dumps({"model": "vlm", "max_tokens": 60, "temperature": 0.2, "cache_prompt": False,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + img}},
        {"type": "text", "text": "Describe what the people and vehicles are doing, in one sentence."}]}]}).encode()
t = time.perf_counter()
try:
    r = json.load(urllib.request.urlopen(urllib.request.Request("http://localhost:8080/v1/chat/completions", body,
        {"Content-Type": "application/json"}), timeout=120))
    tm = r["timings"]
    print("ok %.2f %.0f %.1f" % (time.perf_counter() - t, tm["prompt_ms"], tm["predicted_per_second"]))
except Exception as e:
    print("FAIL", type(e).__name__, str(e)[:60])
PY
}
det() { curl -s -m 5 localhost:5000/api/stats | python3 -c 'import sys,json
try:
  d=json.load(sys.stdin); c=d["cameras"].values()
  print("inf %.1f ms det %.0f/s skip %.1f/s" % (sum(v["inference_speed"] for v in d["detectors"].values())/len(d["detectors"]), sum(x["detection_fps"] for x in c), sum(x["skipped_fps"] for x in c)))
except Exception: print("frigate stats unavailable")'; }
status() { echo "[$(date +%T)] resets=$(resets) frigate_hangs=$(hangs) | $(det) | gpu $(cat /sys/class/drm/card1/device/gpu_busy_percent)% | $*"; }

echo "== gpu-stress $MODE  start $T0  cwsr_enable=$(cat /sys/module/amdgpu/parameters/cwsr_enable)  kernel $(uname -r)"
docker compose up -d cams frigate >/dev/null 2>&1; wait_healthy askcam-frigate || echo "frigate not healthy"

if [ "$MODE" = transitions ]; then
  status "baseline: frigate only"
  echo "-- 1. start VLM while Frigate detects";   docker compose up -d vlm >/dev/null 2>&1; wait_healthy askcam-vlm; sleep 10; status "vlm up"
  echo "-- 2. 20 VLM requests while Frigate detects"
  for i in $(seq 1 20); do echo "   req $i: $(vlm_req)"; done; status "after 20 requests"
  echo "-- 3. restart Frigate while VLM is loaded";  docker compose restart frigate >/dev/null 2>&1; wait_healthy askcam-frigate; sleep 30; status "frigate restarted"
  echo "-- 4. 5 VLM requests";  for i in $(seq 1 5); do echo "   req $i: $(vlm_req)"; done
  echo "-- 5. restart VLM while Frigate detects";   docker compose restart vlm >/dev/null 2>&1; wait_healthy askcam-vlm; sleep 10; status "vlm restarted"
  echo "-- 6. stop/start VLM 3x";  for i in 1 2 3; do docker compose stop vlm >/dev/null 2>&1; sleep 5; docker compose up -d vlm >/dev/null 2>&1; wait_healthy askcam-vlm; echo "   cycle $i: $(vlm_req)"; done
  status "end"
else
  docker compose up -d vlm >/dev/null 2>&1; wait_healthy askcam-vlm
  end=$((SECONDS + MIN * 60)); n=0; fails=0
  while [ $SECONDS -lt $end ]; do
    n=$((n+1)); r=$(vlm_req); [[ $r == FAIL* ]] && fails=$((fails+1))
    [ $((n % 6)) -eq 1 ] && status "req $n: $r" || echo "   req $n: $r"
    sleep 8
  done
  status "soak done: $n requests, $fails failed"
fi
echo "== RESULT: gpu_resets=$(resets) frigate_gpu_hangs=$(hangs)  log=$LOG"
