# 3-minute booth script

**Before the show:** `docker compose up -d`, open http://HOST:8000 full screen (1080p), pick *Crosswalk*. Check the **Model** menu says Qwen2.5 VL 3b. Let it run 5 minutes so the feed has descriptions.

## 0:00 Hook (20 s)
"Everything you see runs on **one chip**: the Radeon 760M graphics inside this Ryzen 5 PRO 8640U. No cloud, no extra card. The cameras are simulated, but the pipeline is the real one."
Point at the footer: *100% local · ROCm + Vulkan · no cloud*.

## 0:20 Three engines, one chip (40 s)
Point at the live view and the meters.
- **Video decode** on the chip's video engine: three 1080p streams, 45 frames/s decoded, ffmpeg uses about a quarter of one CPU core.
- **Detection** with YOLO11 on ROCm: about 100 detections/s at ~10 ms each, boxes on the live view.
- **Language model** (Qwen2.5-VL 3B) on Vulkan: reads the picture and answers.
"The CPU is mostly free for **your** application" (CPU tile: about 30 %).

## 1:00 Ask the camera (60 s)
Click *Warehouse*, then **"Is anyone without safety gear?"** → first word in about 3 s, then the answer streams at ~24 words/s. Show the frame the model saw. Then type a question yourself ("What colour is the vest on the left?").
Talking point: "It's a 3 billion parameter model, so it is not perfect: it can miscount. That's the trade-off for running 100% local on an integrated GPU." (On the warehouse clip the 7B model gets the safety-gear question right; see below.)
While it answers, point at the meters: GPU busy rises, detection keeps running.

## 2:00 Events described by the AI (30 s)
Look at the feed on the right: "Every few seconds Frigate sends one cropped picture of a finished object (a cyclist, a truck) to the same model and stores a sentence. Only a few percent of objects are described by design: rate limited so detection is never starved."

## 2:30 Switch the model (30 s, optional)
Open **Model**: "Same chip, different brain. The 7B model answers better but needs 5.7 GB and drops detection frames; the 3B is the sweet spot." Load *3B lite* to show first word falling from 3.2 s to 1.8 s.

## Numbers to quote (NOTES.md, 3B default, everything running)
| | |
|---|---|
| First word of an answer | about 3 s (1.8 s with 256 image tokens) |
| Answer speed | about 24 tokens/s |
| Detection | 3 cameras, ~100 detections/s, ~10 ms each, <0.1 frames/s skipped idle, ~0.4/s while answering |
| Frigate descriptions | ~6 per minute, 4.5 s median after the object leaves |
| GPU memory | 3.2 GiB for the model, ~4 GiB in total |
| Chip power | about 28 W for the whole demo |
| Model switch | 4-10 s |

## If something goes wrong
- No live image: `docker compose restart frigate` (60-90 s).
- Answers "model is not ready": a model switch is still running, wait 10 s.
- A notice at the top of the page says the model was reloaded or replaced: a safeguard acted (no model was loaded, or memory was running out with a bigger model). Click it to dismiss. Do not leave the 7B loaded for hours.
- Detection pauses for about a minute: Frigate was restarted because detection had stalled; it recovers by itself.
- Descriptions missing for a while: the language model is busy with questions (Frigate's requests are rate-limited), or Frigate restarted its description process; both recover by themselves.
