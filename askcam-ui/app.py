"""askcam-ui: booth demo page for the askcam stack.

 - live view (Frigate MJPEG with detection boxes), proxied so the browser only talks to this service
 - "ask the camera": current frame + question -> VLM (direct, not through vlm-gate), tokens streamed as SSE
 - feed of Frigate events that got an AI description
 - load meters (1 Hz SSE): iGPU busy, detection, VLM, decode, GTT memory, CPU, RAM, power
"""
import asyncio
import base64
import configparser
import glob
import json
import os
import re
import statistics
import time
from collections import deque

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

FRIGATE = os.environ.get("FRIGATE_URL", "http://frigate:5000")
VLM = os.environ.get("VLM_URL", "http://vlm:8080")
MODELS_INI = os.environ.get("MODELS_INI", "/config/models.ini")
MODELS_DIR = os.environ.get("MODELS_DIR", "/models")
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
# Safeguards (guard_sampler). A value of 0 switches that safeguard off.
DEFAULT_MODEL = os.environ.get("DEFAULT_MODEL", "")              # empty: the load-on-startup preset of models.ini
GUARD_NO_MODEL_S = float(os.environ.get("GUARD_NO_MODEL_S", "60"))           # reload the default after this long with no model
GUARD_MIN_HEADROOM_GIB = float(os.environ.get("GUARD_MIN_HEADROOM_GIB", "1.5"))  # free RAM + free swap below this ...
GUARD_LOW_MEM_S = float(os.environ.get("GUARD_LOW_MEM_S", "20"))             # ... for this long: back to the default model

SYSTEM_PROMPT = (
    "You are the vision assistant of a camera system. You are given one still frame from a camera. "
    "Answer the question in at most two short sentences, in plain English, using only what is clearly visible. "
    "If something cannot be seen or judged from the frame, say so. Do not guess or invent details."
)

app = FastAPI(title="askcam-ui")
http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=None))
state: dict = {"t": 0, "model": {"active": None, "switching": None}}
ask_lock = asyncio.Lock()


def active_model():
    return state["model"].get("active")


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
        act = active_model()
        if state["model"].get("switching"):
            act = None
        try:
            if not act:
                raise RuntimeError("no model loaded")
            r = await http.get(f"{VLM}/slots", params={"model": act}, timeout=1.0)
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
        if gpu and act:
            mstats(act)["gpu_busy"].append(sum(gpu) / len(gpu))
        gpu = []
        used, total = read(f"{GPU_DEV}/mem_info_gtt_used"), read(f"{GPU_DEV}/mem_info_gtt_total")
        if used and total:
            s["gtt"] = {"used_gib": int(used) / 2**30, "total_gib": int(total) / 2**30, "pct": 100 * int(used) / int(total)}
        # VLM: activity share over the last second + generation speed from llama.cpp counters
        vlm = {"active": round(sum(vlm_act) / len(vlm_act)) if vlm_act else 0}
        vlm_act = []
        try:
            if not act:
                raise RuntimeError("no model loaded")
            txt = (await http.get(f"{VLM}/metrics", params={"model": act}, timeout=1.0)).text
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
            act = active_model()
            if act and not state["model"].get("switching"):
                ms_ = mstats(act)
                ms_["det_ms"].append(avg_ms)
                ms_["skipped"].append(state["detect"]["skipped_fps"])
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
    asyncio.create_task(model_sampler())
    asyncio.create_task(guard_sampler())


@app.get("/api/meters/stream")
async def meters_stream():
    async def gen():
        while True:
            yield f"data: {json.dumps(state)}\n\n"
            await asyncio.sleep(1.0)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- models (llama.cpp router)
_mstats: dict = {}


def mstats(name):
    """Per-model measurements collected while the model is loaded (in memory, reset when askcam-ui restarts)."""
    return _mstats.setdefault(name, {"questions": [], "det_ms": deque(maxlen=900), "skipped": deque(maxlen=900),
                                     "gpu_busy": deque(maxlen=900), "genai": 0, "load_s": None, "gtt_gib": None})


def load_presets():
    """models.ini (llama.cpp preset file) -> {name: info}; also checks that the GGUF files are present."""
    cp = configparser.ConfigParser(interpolation=None, allow_no_value=True)
    cp.optionxform = str
    try:
        cp.read(MODELS_INI)
    except configparser.Error:
        return {}
    base = dict(cp["*"]) if cp.has_section("*") else {}
    hints, cur = {}, None                      # "; size-gib: N" comments (router ignores them)
    for line in open(MODELS_INI):
        m = re.match(r"\[(.+)\]", line.strip())
        if m:
            cur = m.group(1)
        m = re.match(r";\s*size-gib:\s*([\d.]+)", line.strip())
        if m and cur:
            hints[cur] = float(m.group(1))
    out = {}
    for name in cp.sections():
        if name == "*":
            continue
        c = {**base, **dict(cp[name])}
        size, missing = 0, []
        for key in ("model", "mmproj"):
            path = c.get(key)
            if not path:
                continue
            local = os.path.join(MODELS_DIR, os.path.basename(path))
            if os.path.exists(local):
                size += os.path.getsize(local)
            else:
                missing.append(os.path.basename(path))
        if not size and name in hints:
            size = hints[name] * 2**30
        out[name] = {
            "ctx": int(c.get("ctx-size", 4096)), "parallel": int(c.get("parallel", 1)),
            "img_tokens": int(c.get("image-max-tokens", 0)) or None,
            "startup": str(c.get("load-on-startup", "")).lower() in ("true", "1", "on", "yes"),
            "size_gib": size / 2**30, "missing": missing, "hint": bool(missing and name in hints),
            # GPU memory estimate = weights + projector + KV cache / compute buffers (~1 GiB measured for the 3B)
            "est_gib": size / 2**30 + 1.0,
        }
    return out


async def router_status():
    r = await http.get(f"{VLM}/models", timeout=3.0)
    return {m["id"]: m for m in r.json().get("data", [])}


async def model_sampler():
    while True:
        try:
            st = await router_status()
            loaded = next((k for k, m in st.items() if m["status"]["value"] == "loaded"), None)
            loading = next((k for k, m in st.items() if m["status"]["value"] == "loading"), None)
            state["model"].update(active=loaded, loading=loading, router_up=True)
        except Exception:
            state["model"].update(active=None, loading=None, router_up=False)
        await asyncio.sleep(1.0)


_switch = {"task": None, "error": None, "started": None}


async def do_switch(name, restore_prev=True):
    prev = active_model()
    state["model"]["switching"] = name
    _switch.update(error=None, started=time.time())
    try:
        st = await router_status()
        for k, m in st.items():
            if k != name and m["status"]["value"] in ("loaded", "loading"):
                await http.post(f"{VLM}/models/unload", json={"model": k}, timeout=60.0)
        for _ in range(60):                       # wait until the old model is really gone (frees its GPU memory)
            st = await router_status()
            if not any(m["status"]["value"] in ("loaded", "loading") for k, m in st.items() if k != name):
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(1.0)
        before = float(read(f"{GPU_DEV}/mem_info_gtt_used", 0))
        t0 = time.time()
        r = await http.post(f"{VLM}/models/load", json={"model": name}, timeout=30.0)
        if r.status_code >= 400:
            raise RuntimeError(f"router refused the load: {r.text[:200]}")
        for _ in range(240):
            st = await router_status()
            v = st.get(name, {}).get("status", {}).get("value")
            if v == "loaded":
                break
            if v in ("unloaded", "failed") and time.time() - t0 > 5:
                raise RuntimeError("the model failed to load (see `docker logs askcam-vlm`)")
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("timed out waiting for the model to load")
        ms_ = mstats(name)
        ms_["load_s"] = round(time.time() - t0, 1)
        await asyncio.sleep(2.0)
        ms_["gtt_gib"] = round((float(read(f"{GPU_DEV}/mem_info_gtt_used", 0)) - before) / 2**30, 2)
        for k in ("det_ms", "skipped", "gpu_busy"):
            ms_[k].clear()                        # measure only while this model is loaded
    except Exception as e:
        _switch["error"] = str(e)
        if restore_prev and prev and prev != name:  # best effort: bring the previous model back
            try:
                await http.post(f"{VLM}/models/load", json={"model": prev}, timeout=30.0)
            except Exception:
                pass
    finally:
        state["model"]["switching"] = None


# ---------------------------------------------------------------- safeguards
def default_model():
    pre = load_presets()
    if DEFAULT_MODEL in pre:
        return DEFAULT_MODEL
    return (next((n for n, p in pre.items() if p["startup"] and not p["missing"]), None)
            or next((n for n, p in pre.items() if not p["missing"]), None))


async def guard_switch(name, why):
    print(f"guard: {why}", flush=True)
    state["model"]["guard"] = {"t": time.time(), "msg": why}
    await do_switch(name, restore_prev=False)
    if _switch["error"]:
        print(f"guard: loading {name} failed: {_switch['error']}", flush=True)
        state["model"]["guard"]["msg"] = why + f" Loading it failed: {_switch['error']}"


async def guard_sampler():
    """Keeps the demo answering without an operator:
    1. no model loaded (its process was killed, or a load failed): load the default model again;
    2. a bigger model is loaded and free RAM + free swap are nearly gone: replace it by the default model before the
       kernel's OOM killer acts (which also stalls Frigate's detectors, see NOTES.md step 8)."""
    await asyncio.sleep(30)                           # the router loads its startup model first
    none_since = low_since = None
    while True:
        await asyncio.sleep(2.0)
        try:
            m = state["model"]
            mi = meminfo()
            headroom = (mi["MemAvailable"] + mi.get("SwapFree", 0)) / 2**30
            m["headroom_gib"] = round(headroom, 1)
            if m.get("switching") or not m.get("router_up"):
                none_since = low_since = None
                continue
            now, act, dflt = time.time(), m.get("active"), default_model()
            if act or m.get("loading") or not dflt or not GUARD_NO_MODEL_S:
                none_since = None
            else:
                none_since = none_since or now
                if now - none_since >= GUARD_NO_MODEL_S:
                    none_since = None
                    await guard_switch(dflt, f"No language model was loaded for {GUARD_NO_MODEL_S:.0f} s, "
                                             f"so {dflt} was loaded again.")
                    continue
            if not GUARD_MIN_HEADROOM_GIB or not act or not dflt or act == dflt or headroom >= GUARD_MIN_HEADROOM_GIB:
                low_since = None
            else:
                low_since = low_since or now
                if now - low_since >= GUARD_LOW_MEM_S:
                    low_since = None
                    await guard_switch(dflt, f"Memory was running out with {act} loaded ({headroom:.1f} GiB of RAM "
                                             f"and swap left), so it was replaced by {dflt}.")
        except Exception as e:
            print(f"guard: {e!r}", flush=True)


def med(v):
    return round(statistics.median(v), 1) if v else None


def avg(v):
    return round(sum(v) / len(v), 2) if v else None


@app.get("/api/models")
async def models():
    pre = load_presets()
    act = active_model()
    sw = state["model"].get("switching")
    ram, gtt = state.get("ram"), state.get("gtt")
    try:
        known = set(await router_status())
    except Exception:
        known = set(pre)
    out = []
    for name, p in pre.items():
        ms_ = mstats(name)
        q = ms_["questions"]
        fits = None
        if ram and gtt and name != act:
            others_gtt = gtt["used_gib"] - (pre[act]["est_gib"] if act in pre else 0)
            others_ram = ram["used_gib"] - (pre[act]["est_gib"] if act in pre else 0)
            need_gtt, need_ram = others_gtt + p["est_gib"], others_ram + p["est_gib"]
            worst = max(need_gtt / gtt["total_gib"], need_ram / ram["total_gib"])
            fits = "no" if worst > 1.0 else ("tight" if worst > 0.9 else "ok")
            p["need_gtt_gib"], p["need_ram_gib"] = round(need_gtt, 1), round(need_ram, 1)
        out.append({
            "id": name, **p, "active": name == act, "restart_needed": name not in known, "loading": name == sw, "fits": fits,
            "stats": {"questions": len(q), "ttft_s": med([x[0] for x in q]), "tok_s": med([x[1] for x in q]),
                      "det_ms": avg(ms_["det_ms"]), "skipped": avg(ms_["skipped"]), "gpu_busy": avg(ms_["gpu_busy"]),
                      "genai": ms_["genai"], "load_s": ms_["load_s"], "gtt_gib": ms_["gtt_gib"]},
        })
    return {"models": out, "active": act, "switching": sw, "error": _switch["error"],
            "switch_elapsed": round(time.time() - _switch["started"]) if sw and _switch["started"] else None,
            "gtt": gtt, "ram": ram}


class Load(BaseModel):
    model: str


@app.post("/api/models/load")
async def models_load(body: Load):
    pre = load_presets()
    if body.model not in pre:
        raise HTTPException(404, "unknown model")
    if pre[body.model]["missing"]:
        raise HTTPException(400, "model files are not downloaded: " + ", ".join(pre[body.model]["missing"]))
    if state["model"].get("switching") or ask_lock.locked():
        return JSONResponse({"error": "busy"}, status_code=409)
    if body.model == active_model():
        return {"ok": True, "already": True}
    _switch["task"] = asyncio.create_task(do_switch(body.model))
    return {"ok": True}


@app.get("/llm/v1/models")
async def llm_models():
    """Frigate validates that its configured model name ("vlm") is in /v1/models when its llama.cpp provider starts
    (and restarts after any crash of its embeddings process). Present the loaded model under that name."""
    lst = (await http.get(f"{VLM}/v1/models", timeout=5.0)).json().get("data", [])
    act = active_model()
    entry = (next((m for m in lst if m["id"] == act), None)
             or next((m for m in lst if m.get("status", {}).get("value") == "loaded"), None)
             or (lst[0] if lst else None))
    if entry is None:
        return JSONResponse({"error": "no models"}, status_code=503)
    return {"object": "list", "data": [{**entry, "id": "vlm", "aliases": []}]}


@app.get("/llm/props")
async def llm_props():
    act = active_model()
    if not act:
        return JSONResponse({"error": "no model loaded"}, status_code=503)
    r = await http.get(f"{VLM}/props", params={"model": act}, timeout=5.0)
    return Response(r.content, status_code=r.status_code, media_type="application/json")


@app.post("/llm/v1/chat/completions")
async def llm_proxy(request: Request):
    """Frigate GenAI (via vlm-gate) calls this with its fixed model name; forward to whichever model is loaded."""
    act = active_model()
    if not act or state["model"].get("switching"):
        return JSONResponse({"error": {"message": "model is loading, try again", "code": 503}}, status_code=503)
    body = await request.json()
    body["model"] = act
    mstats(act)["genai"] += 1
    r = await http.post(f"{VLM}/v1/chat/completions", json=body, timeout=httpx.Timeout(10.0, read=120.0))
    return Response(r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


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
    model = active_model()
    if state["model"].get("switching") or not model:
        return JSONResponse({"error": "model is not ready"}, status_code=503)

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
                "model": model, "stream": True, "max_tokens": MAX_TOKENS, "temperature": 0.2,
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
            if first:
                mstats(model)["questions"].append((first, done.get("tok_s", 0)))
            yield sse(done)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@app.get("/")
async def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
