#!/usr/bin/env python3
"""Measure Frigate GenAI description latency and coverage.
Polls the Frigate API every 2 s for N minutes. For every tracked object that ENDS during the window
it records when its AI description first appears (latency = first seen - object end time).
Also samples detector skipped fps and iGPU busy. Usage: scripts/genai-latency.py [minutes]"""
import json, sys, time, urllib.request, statistics as st

API = "http://localhost:5000/api"
MIN = float(sys.argv[1]) if len(sys.argv) > 1 else 3
get = lambda p: json.load(urllib.request.urlopen(API + p, timeout=10))
t_start = time.time(); end = t_start + MIN * 60
ended, described, skips, busy, infs = {}, {}, [], [], []
while time.time() < end:
    now = time.time()
    try:
        for e in get(f"/events?limit=300&after={int(t_start) - 120}"):
            if e["end_time"] and e["end_time"] >= t_start:
                ended.setdefault(e["id"], e)
                if (e.get("data") or {}).get("description") and e["id"] not in described:
                    described[e["id"]] = (now - e["end_time"], e)
        s = get("/stats")
        skips.append(sum(c["skipped_fps"] for c in s["cameras"].values()))
        infs.append(st.mean(d["inference_speed"] for d in s["detectors"].values()))
        busy.append(int(open("/sys/class/drm/card1/device/gpu_busy_percent").read()))
    except Exception as ex:
        print("poll error:", ex)
    time.sleep(max(0, 2 - (time.time() - now)))

lat = sorted(v[0] for v in described.values())
print(f"window {MIN:.0f} min | objects ended {len(ended)} | described {len(described)} ({100*len(described)/max(1,len(ended)):.0f}%)")
if lat:
    print(f"latency object end -> description: p50 {st.median(lat):.1f}s  p90 {lat[int(len(lat)*.9)]:.1f}s  max {lat[-1]:.1f}s")
by = {}
for e in ended.values(): by.setdefault((e["camera"], e["label"]), [0, 0])[0] += 1
for _, e in described.values(): by[(e["camera"], e["label"])][1] += 1
print("per camera/label (ended, described):", {f"{k[0]}/{k[1]}": v for k, v in sorted(by.items())})
print(f"detector: skipped/s avg {st.mean(skips):.2f} max {max(skips):.1f} | inference avg {st.mean(infs):.1f} ms | iGPU busy avg {st.mean(busy):.0f}%")
for lt, e in list(described.values())[:3]:
    print(f"  e.g. {e['camera']} {e['label']} (+{lt:.1f}s): {e['data']['description'][:200]}")
