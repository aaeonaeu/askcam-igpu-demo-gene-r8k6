#!/usr/bin/env python3
"""Exercise askcam-ui end to end: ask questions on every camera while Frigate+GenAI run,
report first-token time, speed, and Frigate skipped frames / detector speed before vs during.

usage: ui-test.py [rounds] [--ui http://localhost:8000] [--frigate http://localhost:5000]"""
import argparse, json, statistics as st, threading, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("rounds", nargs="?", type=int, default=3)
ap.add_argument("--ui", default="http://localhost:8000")
ap.add_argument("--frigate", default="http://localhost:5000")
a = ap.parse_args()
QS = ["What is happening?", "Is anyone without safety gear?", "Count the vehicles", "Anything unusual?"]
CAMS = [c["id"] for c in json.load(urllib.request.urlopen(a.ui + "/api/config"))["cameras"]]

def stats():
    s = json.load(urllib.request.urlopen(a.frigate + "/api/stats"))
    cams = s["cameras"].values()
    return (sum(c["skipped_fps"] for c in cams), sum(d["inference_speed"] for d in s["detectors"].values()) / len(s["detectors"]))

def ask(cam, q):
    req = urllib.request.Request(a.ui + "/api/ask", json.dumps({"camera": cam, "question": q}).encode(),
                                 {"Content-Type": "application/json"})
    t0, first, text, done = time.time(), None, "", {}
    with urllib.request.urlopen(req) as r:
        for line in r:
            line = line.decode().strip()
            if not line.startswith("data: "): continue
            m = json.loads(line[6:])
            if m["type"] == "token":
                if first is None: first = time.time() - t0
                text += m["t"]
            elif m["type"] == "done": done = m
            elif m["type"] == "error": raise RuntimeError(m["msg"])
    return first, done, text

def sampler(box, stop):
    while not stop.is_set():
        box.append(stats()); stop.wait(2)

base = []; stop = threading.Event(); t = threading.Thread(target=sampler, args=(base, stop)); t.start()
time.sleep(20); stop.set(); t.join()
print(f"baseline  : skipped {st.mean(b[0] for b in base):.2f}/s, detector {st.mean(b[1] for b in base):.1f} ms ({len(base)} samples)")

during = []; stop = threading.Event(); t = threading.Thread(target=sampler, args=(during, stop)); t.start()
ttft, toks = [], []
for r in range(a.rounds):
    for cam in CAMS:
        q = QS[(r + CAMS.index(cam)) % len(QS)]
        f, d, text = ask(cam, q)
        ttft.append(f); toks.append(d.get("tok_s", 0))
        print(f"{cam} | first token {f:4.1f} s | {d.get('tok_s', 0):5.1f} tok/s | total {d['total_ms']/1000:4.1f} s | {q} -> {text.strip()[:90]}")
        time.sleep(1)
stop.set(); t.join()
print(f"questions : {len(ttft)}, first token p50 {st.median(ttft):.1f} s, max {max(ttft):.1f} s, speed median {st.median(toks):.1f} tok/s")
print(f"during    : skipped {st.mean(b[0] for b in during):.2f}/s (max {max(b[0] for b in during):.1f}), detector {st.mean(b[1] for b in during):.1f} ms (max {max(b[1] for b in during):.1f})")
