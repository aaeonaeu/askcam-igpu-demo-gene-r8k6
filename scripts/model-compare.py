#!/usr/bin/env python3
"""Ask the currently loaded VLM the same questions about the same frames and save the answers.
Run it once per model (switch with scripts/switch-model.sh), then compare the files in logs/compare/.

usage: model-compare.py [--frigate http://localhost:5000] [--router http://localhost:8080]
Frames are grabbed once from Frigate and reused (logs/compare/frames/camN.jpg), so every model sees the same images."""
import argparse, base64, json, os, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--frigate", default="http://localhost:5000")
ap.add_argument("--router", default="http://localhost:8080")
a = ap.parse_args()
SYSTEM = ("You are the vision assistant of a camera system. You are given one still frame from a camera. "
          "Answer the question in at most two short sentences, in plain English, using only what is clearly visible. "
          "If something cannot be seen or judged from the frame, say so. Do not guess or invent details.")
QS = ["What is happening?", "Is anyone without safety gear?", "Count the vehicles", "Anything unusual?"]
os.makedirs("logs/compare/frames", exist_ok=True)
for cam in ("cam1", "cam2", "cam3"):
    p = f"logs/compare/frames/{cam}.jpg"
    if not os.path.exists(p):
        open(p, "wb").write(urllib.request.urlopen(f"{a.frigate}/api/{cam}/latest.jpg?h=720").read())
model = next(m["id"] for m in json.load(urllib.request.urlopen(a.router + "/models"))["data"] if m["status"]["value"] == "loaded")
out = {"model": model, "answers": []}
for cam in ("cam1", "cam2", "cam3"):
    b64 = base64.b64encode(open(f"logs/compare/frames/{cam}.jpg", "rb").read()).decode()
    for q in QS:
        body = json.dumps({"model": model, "max_tokens": 120, "temperature": 0.2, "cache_prompt": False, "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                                         {"type": "text", "text": q}]}]}).encode()
        t = time.time()
        r = json.load(urllib.request.urlopen(urllib.request.Request(a.router + "/v1/chat/completions", body,
                                                                    {"Content-Type": "application/json"}), timeout=180))
        text = r["choices"][0]["message"]["content"].strip()
        out["answers"].append({"cam": cam, "q": q, "a": text, "wall_s": round(time.time() - t, 1),
                               "prompt_ms": round(r["timings"]["prompt_ms"]), "tok_s": round(r["timings"]["predicted_per_second"], 1)})
        print(f"{cam} | {q} -> {text}")
json.dump(out, open(f"logs/compare/{model}.json", "w"), indent=1)
