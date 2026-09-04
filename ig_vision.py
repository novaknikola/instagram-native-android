# -*- coding: utf-8 -*-
"""Live phone screenshot → Gemini or Grok vision → tap targets.

Enable: put the API key in vision_key.txt (one line) OR env:
  IG_VISION_PROVIDER=gemini|grok
  GEMINI_API_KEY / GOOGLE_API_KEY
  XAI_API_KEY / GROK_API_KEY
  IG_VISION=0  → force off even if a key exists

Soft-fail: farm keeps XML/coord fallbacks if vision is off or the API errors.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

from farm_root import ROOT

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_KEY_FILES = (
    "vision_key.txt",
    "gemini_key.txt",
    "grok_key.txt",
    "xai_key.txt",
)


def _env(name):
    return (os.environ.get(name) or "").strip()


def provider():
    p = _env("IG_VISION_PROVIDER").lower()
    if p in ("gemini", "google", "grok", "xai"):
        return "gemini" if p in ("gemini", "google") else "grok"
    if _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY"):
        return "gemini"
    if _env("XAI_API_KEY") or _env("GROK_API_KEY"):
        return "grok"
    for name in _KEY_FILES:
        path = Path(ROOT) / name
        if path.is_file():
            if "gemini" in name:
                return "gemini"
            if "grok" in name or "xai" in name:
                return "grok"
    return "gemini"


def _key_from_files():
    for name in _KEY_FILES:
        path = Path(ROOT) / name
        if not path.is_file():
            continue
        try:
            line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        except Exception:
            continue
        if line and not line.startswith("#"):
            return line
    return ""


def api_key():
    return (
        _env("IG_VISION_KEY")
        or _env("GEMINI_API_KEY")
        or _env("GOOGLE_API_KEY")
        or _env("XAI_API_KEY")
        or _env("GROK_API_KEY")
        or _key_from_files()
    )


def enabled():
    flag = _env("IG_VISION").lower()
    if flag in ("0", "false", "off", "no"):
        return False
    return bool(api_key())


def _screencap_png(serial, timeout=18.0):
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except Exception:
        return b""
    raw = r.stdout or b""
    if not raw.startswith(_PNG_MAGIC) and b"\r\n" in raw[:64]:
        raw = raw.replace(b"\r\n", b"\n")
    if not raw.startswith(_PNG_MAGIC) or len(raw) < 800:
        return b""
    return raw


def _png_wh(data):
    if not data.startswith(_PNG_MAGIC) or len(data) < 24:
        return 0, 0
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _save_shot(png, tag=""):
    d = Path(ROOT) / "logs" / "vision"
    try:
        d.mkdir(parents=True, exist_ok=True)
        name = "vis_%s_%s.png" % (time.strftime("%H%M%S"), (tag or "shot")[:24])
        path = d / name
        path.write_bytes(png)
        return str(path)
    except Exception:
        return ""


def _prompt(question, want, width, height):
    names = ", ".join(want) if want else "(any labeled control that matches the question)"
    return (
        "You are helping Instagram Android UI automation on a Samsung phone.\n"
        "Screenshot size: %dx%d pixels. Coordinates MUST be integers in that pixel space "
        "(origin top-left), so ADB `input tap x y` hits the same pixel.\n\n"
        "Task: %s\n"
        "Find these targets if visible: %s\n\n"
        "Rules:\n"
        "- STORY tab is immediately to the RIGHT of POST (not POST itself).\n"
        "- Picture sticker is the sticker/overlay/smiley chip (often below Aa, or labeled Overlay).\n"
        "- Link is the Link sticker tile in the sticker tray (may be off-screen to the right).\n"
        "- Do not invent targets that are not on this frame.\n\n"
        "Reply JSON only, no markdown:\n"
        "{\n"
        '  "screen": "feed|create_picker|story_camera|story_gallery|story_editor|'
        'story_text_tool|story_stickers|story_link_form|nux|other",\n'
        '  "summary": "one short sentence of what is on screen",\n'
        '  "elements": [{"name": "...", "x": 0, "y": 0, "visible": true}]\n'
        "}\n"
        "element.name must be one of the requested targets when found.\n"
        % (width, height, question.strip() or "Describe the screen and locate key buttons.",
           names)
    )


def _vision_timeout(default=12.0):
    try:
        return float(_env("IG_VISION_TIMEOUT") or default)
    except Exception:
        return default


def _analyze_timeout(default=12.0):
    """Shorter cap for per-step watch so farm does not stall on slow APIs."""
    try:
        return float(_env("IG_STEP_WATCH_TIMEOUT") or _env("IG_VISION_TIMEOUT") or default)
    except Exception:
        return default


def _http_json(url, body, headers, timeout=None):
    if timeout is None:
        timeout = _vision_timeout()
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_model_json(text):
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        a, b = raw.find("{"), raw.rfind("}")
        if a >= 0 and b > a:
            try:
                return json.loads(raw[a:b + 1])
            except Exception:
                return {}
    return {}


def _call_gemini(png, prompt, key, timeout=None):
    model = _env("GEMINI_MODEL") or "gemini-2.5-flash"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent?key=%s"
        % (model, key)
    )
    body = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/png", "data": b64encode(png).decode("ascii")}},
            ]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    data = _http_json(url, body, {"Content-Type": "application/json"}, timeout=timeout)
    parts = (
        data.get("candidates", [{}])[0]
        .get("content", {})
        .get("parts", [])
    )
    text = "".join(p.get("text") or "" for p in parts)
    return _parse_model_json(text)


def _call_grok(png, prompt, key, timeout=None):
    model = _env("XAI_MODEL") or _env("GROK_MODEL") or "grok-4.6"
    b64 = b64encode(png).decode("ascii")
    body = {
        "model": model,
        "temperature": 0.1,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {
                    "url": "data:image/png;base64,%s" % b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
    }
    data = _http_json(
        "https://api.x.ai/v1/chat/completions",
        body,
        {
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % key,
        },
        timeout=timeout,
    )
    text = (
        data.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    return _parse_model_json(text)


def locate(serial, question, want=None, tag=""):
    """Screenshot + vision. Returns dict with screen/summary/elements, or {}."""
    if not enabled():
        return {}
    want = [w for w in (want or []) if w]
    png = _screencap_png(serial)
    if not png:
        print("[vision] screencap failed")
        return {}
    w, h = _png_wh(png)
    path = _save_shot(png, tag or "locate")
    prompt = _prompt(question, want, w, h)
    key = api_key()
    prov = provider()
    print("[vision] %s %dx%d shot=%s ask=%s" % (
        prov, w, h, os.path.basename(path) if path else "-", question[:80]))
    try:
        if prov == "grok":
            parsed = _call_grok(png, prompt, key)
        else:
            parsed = _call_gemini(png, prompt, key)
    except urllib.error.HTTPError as e:
        err = ""
        try:
            err = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            err = str(e)
        print("[vision] HTTP %s %s" % (e.code, err))
        return {}
    except Exception as e:
        print("[vision] error: %s" % e)
        return {}
    screen = (parsed.get("screen") or "").strip()
    summary = (parsed.get("summary") or "").strip()
    elems = parsed.get("elements") or []
    print("[vision] screen=%s | %s" % (screen or "?", summary[:160]))
    for el in elems:
        print("[vision]   %s @ %s,%s vis=%s" % (
            el.get("name"), el.get("x"), el.get("y"), el.get("visible")))
    parsed["_shot"] = path
    parsed["_wh"] = (w, h)
    return parsed


def _analyze_prompt(phase, goal, context, state, expected, width, height):
    return (
        "You are the real-time brain for Instagram Android farm automation.\n"
        "Screenshot: %dx%d px (ADB tap x,y, origin top-left).\n\n"
        "Run goal: %s\n"
        "Current phase: %s\n"
        "Detected UI state (XML heuristic): %s\n"
        "Last action / note: %s\n"
        "Expected screen: %s\n\n"
        "Analyze this frame like an operator watching the phone:\n"
        "1) What is actually on screen?\n"
        "2) Is automation on track, stuck, or on wrong screen?\n"
        "3) Blockers (dialogs, login, captcha, wrong app, loading)?\n"
        "4) ONE concrete next tap with pixel coordinates if applicable.\n"
        "5) Short solution sentence if stuck.\n\n"
        "Reply JSON only, no markdown:\n"
        "{\n"
        '  "screen": "feed|login|reel_edit|reel_caption|share_sheet|story_editor|'
        'gallery|nux|error_dialog|other",\n'
        '  "summary": "one sentence — what you see",\n'
        '  "status": "ok|stuck|wrong_screen|dialog|loading",\n'
        '  "blockers": ["..."],\n'
        '  "recommended_action": {"label": "Next", "x": 0, "y": 0, "why": "..."},\n'
        '  "solution": "If stuck: exact fix for the operator/automation",\n'
        '  "confidence": 0.0\n'
        "}\n"
        "If no tap needed, recommended_action can be {}.\n"
        % (
            width, height,
            (goal or "Instagram post").strip()[:200],
            (phase or "step").strip()[:120],
            (state or "unknown").strip()[:80],
            (context or "none").strip()[:240],
            (expected or "progress toward post/share").strip()[:120],
        )
    )


def analyze_step(
    serial,
    phase="",
    goal="",
    context="",
    state="",
    expected="",
    tag="",
    force=False,
):
    """Full step analysis: screenshot + vision think + solution. Returns dict."""
    if not api_key():
        return {}
    if not force and not enabled():
        return {}
    png = _screencap_png(serial)
    if not png:
        print("[vision] analyze_step: screencap failed")
        return {}
    w, h = _png_wh(png)
    path = _save_shot(png, tag or phase.replace(" ", "_")[:20] or "step")
    prompt = _analyze_prompt(phase, goal, context, state, expected, w, h)
    key = api_key()
    prov = provider()
    print("[vision-analyze] %s #%s state=%s" % (prov, tag or phase, state or "?"))
    try:
        if prov == "grok":
            parsed = _call_grok(png, prompt, key, timeout=_analyze_timeout())
        else:
            parsed = _call_gemini(png, prompt, key, timeout=_analyze_timeout())
    except Exception as e:
        print("[vision-analyze] error: %s" % e)
        return {}
    summary = (parsed.get("summary") or "").strip()
    solution = (parsed.get("solution") or "").strip()
    status = (parsed.get("status") or "").strip()
    print("[vision-analyze] %s | %s" % (status or "?", summary[:160]))
    if solution:
        print("[vision-analyze] solution: %s" % solution[:200])
    act = parsed.get("recommended_action") or {}
    if isinstance(act, dict) and act.get("label"):
        print("[vision-analyze] next: %s @ %s,%s" % (
            act.get("label"), act.get("x"), act.get("y")))
    parsed["_shot"] = path
    parsed["_wh"] = (w, h)
    return parsed


def find(serial, name, question="", tag=""):
    """First visible element matching name (case-insensitive)."""
    want = [name]
    q = question or ("Locate '%s' and give its tap x,y." % name)
    data = locate(serial, q, want=want, tag=tag or name.replace(" ", "_")[:20])
    name_l = name.strip().lower()
    for el in data.get("elements") or []:
        n = (el.get("name") or "").strip().lower()
        if n != name_l and name_l not in n:
            continue
        if el.get("visible") is False:
            continue
        try:
            x, y = int(el["x"]), int(el["y"])
        except Exception:
            continue
        if x > 0 and y > 0:
            return {"name": el.get("name") or name, "x": x, "y": y,
                    "screen": data.get("screen") or "", "summary": data.get("summary") or ""}
    return None


def main():
    import sys
    serial = ""
    ask = "Describe this Instagram screen. Locate POST, STORY, picture sticker, Link, Done if visible."
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--serial" and i + 1 < len(args):
            serial = args[i + 1]; i += 2; continue
        if args[i] == "--ask" and i + 1 < len(args):
            ask = args[i + 1]; i += 2; continue
        i += 1
    if not serial:
        try:
            out = subprocess.check_output(["adb", "devices"], text=True, timeout=10)
            for ln in out.splitlines()[1:]:
                if ln.endswith("\tdevice"):
                    serial = ln.split("\t")[0]
                    break
        except Exception:
            serial = ""
    if not enabled():
        print("No API key. Put it in vision_key.txt or set GEMINI_API_KEY / XAI_API_KEY.")
        sys.exit(2)
    if not serial:
        print("No adb device.")
        sys.exit(2)
    locate(serial, ask, want=["POST tab", "STORY tab", "picture sticker", "Link", "Done"],
           tag="cli")


if __name__ == "__main__":
    main()
