#!/usr/bin/env python3
"""Soak test: run the full stack for N minutes, ask a VLM question through askcam-ui every 30 s, and log health.

usage: scripts/soak.py [minutes=60] [--tag NAME]
Writes (all kept, git-ignored):  logs/step6/soak-<tag>.csv      one row per 30 s sample
                                  logs/step6/soak-<tag>.events.log  questions, restarts, kernel errors
                                  logs/step6/soak-<tag>.summary.md  verdict, written at the end (and every 5 min)
Run detached:  nohup python3 scripts/soak.py 60 > logs/step6/soak.out 2>&1 &
"""
import argparse, csv, glob, json, os, re, statistics as st, subprocess, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("minutes", nargs="?", type=float, default=60)
ap.add_argument("--tag", default=time.strftime("%Y%m%d-%H%M"))
ap.add_argument("--ui", default="http://localhost:8000")
ap.add_argument("--frigate", default="http://localhost:5000")
a = ap.parse_args()
D = "logs/step6"; os.makedirs(D, exist_ok=True)
CSV, EV, SUM = f"{D}/soak-{a.tag}.csv", f"{D}/soak-{a.tag}.events.log", f"{D}/soak-{a.tag}.summary.md"
QS = ["What is happening?", "Is anyone without safety gear?", "Count the vehicles", "Anything unusual?"]
KERNEL_RE = re.compile(r"GPU reset|MES failed|amdgpu.*(timeout|hang|ring .* timeout)|amdkfd.*(error|fail)|kfd.*reset|Out of memory", re.I)
def j(url, t=10): return json.load(urllib.request.urlopen(url, timeout=t))
def sh(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()
def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True); open(EV, "a").write(line + "\n")

def ask(cam, q):
    req = urllib.request.Request(a.ui + "/api/ask", json.dumps({"camera": cam, "question": q}).encode(), {"Content-Type": "application/json"})
    t0, first, done = time.time(), None, {}
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            for line in r:
                line = line.decode().strip()
                if not line.startswith("data: "): continue
                m = json.loads(line[6:])
                if m["type"] == "token" and first is None: first = time.time() - t0
                elif m["type"] == "done": done = m
                elif m["type"] == "error": return None, None, m["msg"]
    except Exception as e:
        return None, None, repr(e)
    return first, done.get("tok_s"), ""

def hw():
    d = {}
    for h in glob.glob("/sys/class/hwmon/hwmon*"):
        try: name = open(h + "/name").read().strip()
        except OSError: continue
        if name in ("k10temp", "amdgpu"):
            d[name + "_c"] = int(open(h + "/temp1_input").read()) / 1000
        if name == "amdgpu" and os.path.exists(h + "/power1_average"):
            d["power_w"] = int(open(h + "/power1_average").read()) / 1e6
    for dev in glob.glob("/sys/class/drm/card*/device"):
        if os.path.exists(dev + "/gpu_busy_percent"):
            d["gpu_busy"] = int(open(dev + "/gpu_busy_percent").read()); d["gtt_gib"] = int(open(dev + "/mem_info_gtt_used").read()) / 2**30
    return d

def meminfo():
    m = {l.split(":")[0]: int(l.split()[1]) for l in open("/proc/meminfo")}
    return (m["MemTotal"] - m["MemAvailable"]) / 2**20, (m["SwapTotal"] - m["SwapFree"]) / 2**20

def emb_rss():
    t = 0
    for p in glob.glob("/proc/[0-9]*/comm"):
        try:
            if open(p).read().startswith("frigate.embeddi"):
                t += int([l for l in open(os.path.dirname(p) + "/status") if l.startswith("VmRSS")][0].split()[1]) / 2**20
        except (OSError, IndexError): pass
    return t

def restarts():
    out = sh("docker inspect -f '{{.Name}} {{.RestartCount}} {{.State.StartedAt}} {{.State.OOMKilled}}' $(docker compose ps -q)")
    return {l.split()[0]: tuple(l.split()[1:]) for l in out.splitlines()}

def frigate_log_events(since):
    out = sh(f"docker logs --since {since} askcam-frigate 2>&1 | grep -E 'exited with code|Restarted|GPU Hang|has not been initialized|Unable to|ERROR' | grep -v go2rtc_stream_proxy | tail -20")
    return out.splitlines()

t_start = time.time(); T0ISO = time.strftime("%Y-%m-%dT%H:%M:%S")
rs0 = restarts(); log(f"soak start, {a.minutes} min, tag {a.tag}; containers: {rs0}")
cols = ["t_min", "cam", "q_ok", "ttft_s", "tok_s", "detector_ms", "skipped_fps", "det_fps", "gpu_busy", "gtt_gib", "ram_used_gib", "swap_gib",
        "emb_rss_gib", "k10temp_c", "amdgpu_c", "power_w", "descriptions", "kernel_errs"]
w = csv.writer(open(CSV, "w")); w.writerow(cols)
rows, errs_total, last_sum, i, seen_ev = [], 0, 0, 0, set()
while time.time() - t_start < a.minutes * 60:
    tick = time.time()
    cam = ["cam1", "cam2", "cam3"][i % 3]; q = QS[i % 4]; i += 1
    ttft, tps, err = ask(cam, q)
    if err: log(f"QUESTION FAILED {cam}: {err}")
    try:
        s = j(a.frigate + "/api/stats")
        dets = [d["inference_speed"] for d in s["detectors"].values()]
        det_ms = sum(dets) / len(dets); sk = sum(c["skipped_fps"] for c in s["cameras"].values()); dfps = sum(c["detection_fps"] for c in s["cameras"].values())
    except Exception as e:
        log(f"FRIGATE STATS FAILED {e!r}"); det_ms = sk = dfps = None
    try: desc = sum(m["stats"]["genai"] for m in j(a.ui + "/api/models")["models"])
    except Exception: desc = None
    kern = [l for l in sh(f"journalctl -k --since '{T0ISO}' --no-pager 2>/dev/null").splitlines() if KERNEL_RE.search(l)]
    for l in kern:
        if l not in seen_ev: seen_ev.add(l); log("KERNEL: " + l[:220])
    for l in frigate_log_events("90s"):
        if l not in seen_ev: seen_ev.add(l); log("FRIGATE: " + l[:220])
    h = hw(); ram, swap = meminfo()
    row = [round((time.time() - t_start) / 60, 1), cam, int(not err), ttft and round(ttft, 2), tps, det_ms and round(det_ms, 1), sk and round(sk, 2),
           dfps and round(dfps, 1), h.get("gpu_busy"), h.get("gtt_gib") and round(h["gtt_gib"], 2), round(ram, 2), round(swap, 2), round(emb_rss(), 2),
           h.get("k10temp_c"), h.get("amdgpu_c"), h.get("power_w") and round(h["power_w"], 1), desc, len(kern)]
    w.writerow(row); open(CSV, "a").close(); rows.append(dict(zip(cols, row)))
    if time.time() - last_sum > 300 or time.time() - t_start >= a.minutes * 60 - 35:
        last_sum = time.time()
        # summary
        def col(c): return [r[c] for r in rows if r[c] is not None]
        ok = col("q_ok"); tt = col("ttft_s"); rs = restarts()
        changed = {k: (rs0.get(k), v) for k, v in rs.items() if rs0.get(k) != v}
        emb = col("emb_rss_gib"); desc_l = col("descriptions")
        lines = [f"# Soak {a.tag} ({round((time.time()-t_start)/60)} of {a.minutes:g} min so far)", "",
                 f"- questions: {sum(ok)}/{len(ok)} ok; first word p50 {st.median(tt):.1f} s, p95 {sorted(tt)[int(.95*(len(tt)-1))]:.1f} s, max {max(tt):.1f} s" if tt else "- no answers",
                 f"- detector: avg {st.mean(col('detector_ms')):.1f} ms (max {max(col('detector_ms')):.1f}); skipped avg {st.mean(col('skipped_fps')):.2f}/s (max {max(col('skipped_fps')):.1f})",
                 f"- iGPU busy avg {st.mean(col('gpu_busy')):.0f} %; GTT {min(col('gtt_gib')):.1f}-{max(col('gtt_gib')):.1f} GiB; power avg {st.mean(col('power_w')):.0f} W",
                 f"- temperature max: CPU {max(col('k10temp_c')):.0f} C, GPU {max(col('amdgpu_c')):.0f} C (throttling: see CSV; rising ttft with high temp would show it)",
                 f"- RAM used {min(col('ram_used_gib')):.1f}-{max(col('ram_used_gib')):.1f} GiB, swap max {max(col('swap_gib')):.1f} GiB; frigate.embeddings RSS now {emb[-1]:.2f} GiB, max {max(emb):.2f}",
                 f"- Frigate descriptions produced (counter): {desc_l[0] if desc_l else '?'} -> {desc_l[-1] if desc_l else '?'}",
                 f"- kernel errors matching GPU/amdkfd/OOM patterns since start: {len(set(kern))}",
                 f"- container changes (restart count / start time / OOM): {changed or 'none'}",
                 f"- first-vs-last-10-min first word: {st.median(tt[:20]):.1f} s -> {st.median(tt[-20:]):.1f} s" if len(tt) >= 40 else ""]
        open(SUM, "w").write("\n".join(lines) + "\n")
    time.sleep(max(0, 30 - (time.time() - tick)))
log("soak finished"); open(SUM, "a").write("\nFINISHED\n")
