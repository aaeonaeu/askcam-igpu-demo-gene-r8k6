#!/usr/bin/env bash
# Print Frigate detector/camera stats plus iGPU and CPU load. Usage: scripts/frigate-stats.sh [samples] [interval_s]
N=${1:-1}; T=${2:-10}
for i in $(seq 1 "$N"); do
  curl -s --max-time 5 localhost:5000/api/stats | python3 -c '
import sys, json, time
try:
    d = json.load(sys.stdin)
except Exception:
    print("stats not ready"); sys.exit()
o = list(d["detectors"].values())[0]; c = d["cameras"]
det = sum(v["detection_fps"] for v in c.values()); sk = sum(v["skipped_fps"] for v in c.values())
cpu = d["cpu_usages"].get("frigate.full_system", {}).get("cpu")
cams = " ".join("%s:%.0ffps/det%.0f/skip%.1f" % (k, v["camera_fps"], v["detection_fps"], v["skipped_fps"]) for k, v in c.items())
print(time.strftime("%H:%M:%S"), "inference %5.1f ms | detections/s %5.1f | skipped/s %4.1f | %s | frigate cpu %s%%" % (o["inference_speed"], det, sk, cams, cpu))'
  echo "          iGPU busy $(cat /sys/class/drm/card1/device/gpu_busy_percent)% | GTT $(( $(cat /sys/class/drm/card1/device/mem_info_gtt_used)/1048576 )) MiB | host cpu idle $(vmstat 1 2 | tail -1 | awk '{print $15}')%"
  [ "$i" -lt "$N" ] && sleep "$T"
done
