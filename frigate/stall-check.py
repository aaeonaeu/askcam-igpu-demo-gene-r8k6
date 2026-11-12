#!/usr/bin/env python3
"""Second half of the Frigate container health check (compose.yml): restart Frigate when detection is stalled.

Seen on 2026-11-11: under memory pressure Frigate's watchdog force-killed a stuck detector process; the replacement
detectors then waited forever on the shared detection queue. Cameras kept delivering frames, none were processed
(process_fps 0.1, skipped_fps = camera fps), the API still answered, so the container stayed "healthy" for 11 hours.

Rule: every camera that delivers frames (camera_fps >= 1, detection enabled) processes fewer than 0.5 fps, for
ASKCAM_STALL_RESTART_S seconds (default 120, 0 = only report unhealthy). Then the s6 supervision tree is halted,
the container exits and Docker's restart policy starts it again (about 45 s without detection).
"""
import json
import os
import sys
import time
import urllib.request

STALL_S = float(os.environ.get("ASKCAM_STALL_RESTART_S", "120"))
MARK = "/dev/shm/.askcam-stall-since"


def clear():
    try:
        os.remove(MARK)
    except OSError:
        pass


try:
    stats = json.load(urllib.request.urlopen("http://127.0.0.1:5000/api/stats", timeout=5))
except Exception:
    sys.exit(0)          # an unreachable API is the first half of the health check, not this one's business

cams = [c for c in stats.get("cameras", {}).values()
        if c.get("detection_enabled", True) and (c.get("camera_fps") or 0) >= 1]
stalled = bool(cams) and all((c.get("process_fps") or 0) < 0.5 for c in cams)
if not stalled or (stats.get("service", {}).get("uptime") or 0) < 60:
    clear()
    sys.exit(0)

now = time.time()
try:
    since = float(open(MARK).read())
except (OSError, ValueError):
    since = now
    with open(MARK, "w") as f:
        f.write(str(now))

age = now - since
if STALL_S and age >= STALL_S:
    clear()
    msg = f"[askcam] detection stalled for {age:.0f} s on all {len(cams)} cameras: restarting Frigate\n"
    try:
        with open("/proc/1/fd/1", "w") as f:
            f.write(msg)
    except OSError:
        pass
    os.execv("/run/s6/basedir/bin/halt", ["halt"])

print(f"detection stalled for {age:.0f} s")
sys.exit(1)
