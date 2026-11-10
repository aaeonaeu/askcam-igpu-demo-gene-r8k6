#!/usr/bin/env python3
"""Send an image + question to the VLM server and print answer and timings.
Usage: scripts/test-vlm.py IMAGE [QUESTION] [--url http://localhost:8080] [--runs N] [--max-tokens N]
"""
import argparse, base64, json, mimetypes, time, urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("image")
ap.add_argument("question", nargs="?", default="Describe what is happening in this image in two sentences.")
ap.add_argument("--url", default="http://localhost:8080")
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--max-tokens", type=int, default=100)
a = ap.parse_args()

def loaded_model(url):  # llama.cpp router: requests must name the loaded model
    try:
        d = json.load(urllib.request.urlopen(url + "/models", timeout=5))["data"]
        return next(m["id"] for m in d if m["status"]["value"] == "loaded")
    except Exception:
        return "vlm"

data = base64.b64encode(open(a.image, "rb").read()).decode()
mime = mimetypes.guess_type(a.image)[0] or "image/jpeg"
body = json.dumps({
    "model": loaded_model(a.url), "max_tokens": a.max_tokens, "temperature": 0.2, "cache_prompt": False,
    "messages": [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}},
        {"type": "text", "text": a.question}]}],
}).encode()

for i in range(a.runs):
    t = time.perf_counter()
    r = json.load(urllib.request.urlopen(urllib.request.Request(
        a.url + "/v1/chat/completions", body, {"Content-Type": "application/json"}), timeout=300))
    wall = time.perf_counter() - t
    tm, u = r["timings"], r["usage"]
    print(f"run {i+1}: wall {wall:5.2f}s | prompt {u['prompt_tokens']} tok in {tm['prompt_ms']:6.0f} ms "
          f"({tm['prompt_per_second']:6.1f} t/s, includes image encode) | gen {u['completion_tokens']} tok "
          f"{tm['predicted_per_second']:5.1f} t/s")
    if i == 0:
        print("answer:", r["choices"][0]["message"]["content"].strip())
