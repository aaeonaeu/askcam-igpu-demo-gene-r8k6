"""askcam-ui: booth demo page for the askcam stack.

 - live view (Frigate MJPEG with detection boxes), proxied so the browser only talks to this service
 - "ask the camera": current frame + question -> VLM (direct, not through vlm-gate), tokens streamed as SSE
 - feed of Frigate events that got an AI description
 - load meters (1 Hz SSE): iGPU busy, detection, VLM, decode, GTT memory, CPU, RAM, power
"""
import asyncio
import base64
import glob
import json
import os
import re
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

FRIGATE = os.environ.get("FRIGATE_URL", "http://frigate:5000")
VLM = os.environ.get("VLM_URL", "http://vlm:8080")
VLM_MODEL = os.environ.get("VLM_ALIAS", "vlm")
SYS = os.environ.get("HOST_SYS", "/host/sys")
CAMERAS = [c.split(":", 1) for c in os.environ.get(
    "CAMERAS", "cam1:Crosswalk,cam2:Warehouse,cam3:Road junction").split(",")]
SRC_FPS = float(os.environ.get("CAM_SOURCE_FPS", "15"))  # fps of the simulated 1080p streams (decode load)
SRC_DESC = os.environ.get("CAM_SOURCE_DESC", "1080p H.264")
FRAME_HEIGHT = int(os.environ.get("ASK_FRAME_HEIGHT", "720"))
MAX_TOKENS = int(os.environ.get("ASK_MAX_TOKENS", "160"))
LIVE_FPS = int(os.environ.get("LIVE_FPS", "6"))
LIVE_HEIGHT = int(os.environ.get("LIVE_HEIGHT", "540"))
FRIGATE_STATS_EVERY = float(os.environ.get("FRIGATE_STATS_EVERY", "2"))

SYSTEM_PROMPT = (
    "You are the vision assistant of a camera system. You are given one still frame from a camera. "
    "Answer the question in at most two short sentences, in plain English, using only what is clearly visible. "
    "If something cannot be seen or judged from the frame, say so. Do not guess or invent details."
)

app = FastAPI(title="askcam-ui")
http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
state: dict = {"t": 0}
ask_lock = asyncio.Lock()


# ---------------------------------------------------------------- meters
def read(path, default=None):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def find_amdgpu():
    """Return (device dir, hwmon dir) of the first AMD GPU that exposes gpu_busy_percent."""
    for dev in sorted(glob.glob(f"{SYS}/class/drm/card*/device")):
        if read(f"{dev}/vendor") == "0x1002" and os.path.exists(f"{dev}/gpu_busy_percent"):
            hw = next(iter(glob.glob(f"{dev}/hwmon/hwmon*")), None)
            return dev, hw
    return None, None


GPU_DEV, GPU_HWMON = find_amdgpu()
CPU_HWMON = next((h for h in glob.glob(f"{SYS}/class/hwmon/hwmon*") if read(f"{h}/name") == "k10temp"), None)


def cpu_times():
    f = open("/proc/stat").readline().split()[1:]
    v = list(map(int, f))
    return sum(v), v[3] + v[4]  # total, idle+iowait


def meminfo():
    m = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":", 1)
        m[k] = int(v.split()[0]) * 1024
    return m


async def sysfs_sampler():
    """5 Hz: iGPU busy and VLM activity, averaged into 1 s state updates."""
    gpu, vlm_act, n = [], [], 0
    last_tok, last_ptok, last_t = None, None, time.time()
    cpu_prev = cpu_times()
    last_emit = time.time()
    while True:
        await asyncio.sleep(0.2)
        v = read(f"{GPU_DEV}/gpu_busy_percent") if GPU_DEV else None
        if v is not None:
            gpu.append(int(v))
        try:
            r = await http.get(f"{VLM}/slots", timeout=1.0)
            slots = r.json()
            vlm_act.append(100 if any(s.get("is_processing") for s in slots) else 0)
        except Exception:
            pass
        now = time.time()
        if now - last_emit < 1.0:
            continue
        last_emit = now
        s = {}
        # iGPU
        s["gpu"] = {"busy": round(sum(gpu) / len(gpu)) if gpu else None, "peak": max(gpu) if gpu else None}
        gpu = []
        used, total = read(f"{GPU_DEV}/mem_info_gtt_used"), read(f"{GPU_DEV}/mem_info_gtt_total")
        if used and total:
            s["gtt"] = {"used_gib": int(used) / 2**30, "total_gib": int(total) / 2**30, "pct": 100 * int(used) / int(total)}
        # VLM: activity share over the last second + generation speed from llama.cpp counters
        vlm = {"active": round(sum(vlm_act) / len(vlm_act)) if vlm_act else 0}
        vlm_act = []
        try:
            txt = (await http.get(f"{VLM}/metrics", timeout=1.0)).text
            tok = float(re.search(r"^llamacpp:tokens_predicted_total (\S+)", txt, re.M).group(1))
            ptok = float(re.search(r"^llamacpp:prompt_tokens_total (\S+)", txt, re.M).group(1))
            proc = int(float(re.search(r"^llamacpp:requests_processing (\S+)", txt, re.M).group(1)))
            if last_tok is not None and now > last_t:
                vlm["gen_tok_s"] = round((tok - last_tok) / (now - last_t), 1)
                vlm["prompt_tok_s"] = round((ptok - last_ptok) / (now - last_t))
            last_tok, last_ptok, last_t = tok, ptok, now
            vlm["requests"] = proc
        except Exception:
            pass
        s["vlm"] = vlm
        # CPU / RAM
        cpu_now = cpu_times()
        dt, di = cpu_now[0] - cpu_prev[0], cpu_now[1] - cpu_prev[1]
        s["cpu"] = {"pct": round(100 * (1 - di / dt), 1) if dt else 0}
        cpu_prev = cpu_now
        mi = meminfo()
        used = mi["MemTotal"] - mi["MemAvailable"]
        s["ram"] = {"used_gib": used / 2**30, "total_gib": mi["MemTotal"] / 2**30, "pct": 100 * used / mi["MemTotal"]}
        # power / temperature (amdgpu hwmon reports the whole APU socket power in microwatts)
        p = read(f"{GPU_HWMON}/power1_average") or read(f"{GPU_HWMON}/power1_input") if GPU_HWMON else None
        t_gpu = read(f"{GPU_HWMON}/temp1_input") if GPU_HWMON else None
        t_cpu = read(f"{CPU_HWMON}/temp1_input") if CPU_HWMON else None
        s["power_w"] = round(int(p) / 1e6, 1) if p else None
        s["temp_c"] = {"gpu": round(int(t_gpu) / 1000) if t_gpu else None, "cpu": round(int(t_cpu) / 1000) if t_cpu else None}
        state.update(s, t=now)


async def frigate_sampler():
    while True:
        try:
            st = (await http.get(f"{FRIGATE}/api/stats", timeout=5.0)).json()
            cams = st.get("cameras", {})
            dets = st.get("detectors", {})
            fps = sum(c.get("detection_fps", 0) for c in cams.values())
            ms = [d["inference_speed"] for d in dets.values() if d.get("inference_speed")]
            avg_ms = sum(ms) / len(ms) if ms else 0
            state["detect"] = {
                "fps": round(fps, 1),
                "ms": round(avg_ms, 1),
                "detectors": len(dets),
                # share of the detector processes' capacity in use (inferences/s x time per inference)
                "duty": round(min(100.0, 100 * fps * avg_ms / 1000 / max(1, len(dets))), 0),
                "skipped_fps": round(sum(c.get("skipped_fps", 0) for c in cams.values()), 2),
            }
            state["decode"] = {
                "streams": len(cams),
                "fps": round(len(cams) * SRC_FPS),
                "desc": SRC_DESC,
                "ffmpeg_cpu": round(sum(float(c.get("ffmpeg_cpu", 0)) for c in cams.values()), 1),
            }
        except Exception:
            state.pop("detect", None)
            state.pop("decode", None)
        await asyncio.sleep(FRIGATE_STATS_EVERY)


@app.on_event("startup")
async def startup():
    asyncio.create_task(sysfs_sampler())
    asyncio.create_task(frigate_sampler())


@app.get("/api/meters/stream")
async def meters_stream():
    async def gen():
        while True:
            yield f"data: {json.dumps(state)}\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- misc
@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/config")
async def config():
    return {"cameras": [{"id": c[0], "name": c[1]} for c in CAMERAS], "max_tokens": MAX_TOKENS}


@app.get("/api/live/{cam}")
async def live(cam: str):
    """MJPEG with detection boxes, proxied from Frigate."""
    if cam not in dict(CAMERAS):
        raise HTTPException(404)
    req = http.build_request("GET", f"{FRIGATE}/api/{cam}", params={"fps": LIVE_FPS, "h": LIVE_HEIGHT, "bbox": 1})
    r = await http.send(req, stream=True)

    async def gen():
        try:
            async for chunk in r.aiter_raw():
                yield chunk
        finally:
            await r.aclose()
    return StreamingResponse(gen(), media_type=r.headers.get("content-type", "multipart/x-mixed-replace;boundary=frame"),
                             headers={"Cache-Control": "no-store"})


@app.get("/api/thumb/{event_id}")
async def thumb(event_id: str):
    if not re.fullmatch(r"[\w.-]+", event_id):
        raise HTTPException(400)
    r = await http.get(f"{FRIGATE}/api/events/{event_id}/thumbnail.jpg", timeout=5.0)
    if r.status_code != 200:
        raise HTTPException(r.status_code)
    return Response(r.content, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


_feed_cache = {"t": 0, "v": None}


@app.get("/api/feed")
async def feed(window_min: int = 10, limit: int = 8):
    """Latest events that got an AI description + counters for the window."""
    if time.time() - _feed_cache["t"] > 1.0 or _feed_cache["v"] is None:
        after = time.time() - window_min * 60
        r = await http.get(f"{FRIGATE}/api/events", params={"limit": 400, "after": after}, timeout=8.0)
        ev = r.json()
        described = [e for e in ev if (e.get("data") or {}).get("description")]
        out = [{
            "id": e["id"], "camera": e["camera"], "label": e["label"], "zones": e.get("zones", []),
            "start": e["start_time"], "text": e["data"]["description"],
        } for e in described[:limit]]
        _feed_cache.update(t=time.time(), v={"events": out, "objects": len(ev), "described": len(described),
                                             "window_min": window_min})
    return _feed_cache["v"]


# ---------------------------------------------------------------- ask the camera
class Ask(BaseModel):
    camera: str
    question: str = Field(min_length=1, max_length=300)


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n"


@app.post("/api/ask")
async def ask(body: Ask):
    if body.camera not in dict(CAMERAS):
        raise HTTPException(404, "unknown camera")
    if ask_lock.locked():
        return JSONResponse({"error": "busy"}, status_code=429)

    async def gen():
        async with ask_lock:
            t0 = time.perf_counter()
            try:
                r = await http.get(f"{FRIGATE}/api/{body.camera}/latest.jpg", params={"h": FRAME_HEIGHT}, timeout=5.0)
                r.raise_for_status()
            except Exception as e:
                yield sse({"type": "error", "msg": f"could not grab a frame from Frigate ({e.__class__.__name__})"})
                return
            jpg = r.content
            grab_ms = (time.perf_counter() - t0) * 1000
            b64 = base64.b64encode(jpg).decode()
            yield sse({"type": "frame", "b64": b64, "grab_ms": round(grab_ms)})
            payload = {
                "model": VLM_MODEL, "stream": True, "max_tokens": MAX_TOKENS, "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                        {"type": "text", "text": body.question},
                    ]},
                ],
            }
            t1 = time.perf_counter()
            first, n, timings = None, 0, None
            try:
                async with http.stream("POST", f"{VLM}/v1/chat/completions", json=payload,
                                       timeout=httpx.Timeout(10.0, read=60.0)) as resp:
                    if resp.status_code != 200:
                        yield sse({"type": "error", "msg": f"VLM returned HTTP {resp.status_code}"})
                        return
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: ") or line.endswith("[DONE]"):
                            continue
                        j = json.loads(line[6:])
                        timings = j.get("timings", timings)
                        delta = (j.get("choices") or [{}])[0].get("delta", {}).get("content")
                        if delta:
                            if first is None:
                                first = time.perf_counter() - t1
                            n += 1
                            yield sse({"type": "token", "t": delta})
            except httpx.HTTPError as e:
                yield sse({"type": "error", "msg": f"VLM request failed ({e.__class__.__name__})"})
                return
            total = time.perf_counter() - t1
            done = {"type": "done", "grab_ms": round(grab_ms), "ttft_ms": round(first * 1000) if first else None,
                    "total_ms": round(total * 1000), "tokens": n}
            if timings:
                done["tok_s"] = round(timings.get("predicted_per_second", 0), 1)
                done["prompt_ms"] = round(timings.get("prompt_ms", 0))
            yield sse(done)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
