# -*- coding: utf-8 -*-
"""Per-step screenshot + vision analysis — real-time watch & think.

Every farm step saves a PNG and (when an API key exists) asks vision:
what is on screen, is automation stuck, what to tap next.

Enable (default ON):
  IG_STEP_WATCH=1          screenshots + analysis (default)
  IG_STEP_WATCH=0          off

Vision (step analysis uses API even if IG_VISION=0 unless IG_STEP_WATCH_VISION=0):
  vision_key.txt / GEMINI_API_KEY / grok_key.txt

Output:
  logs/step_watch/<YYYYMMDD_HHMMSS>_<serial8>/
    001_login_LOGIN_SCREEN.png
    steps.jsonl
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from farm_root import ROOT

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_ctx = {
    "serial": "",
    "username": "",
    "pkg": "",
    "goal": "",
    "format": "",
}
_run_dir: Path | None = None
_counter = 0
_last_action = ""


def enabled() -> bool:
    return os.environ.get("IG_STEP_WATCH", "1").strip().lower() not in (
        "0", "false", "off", "no",
    )


def _vision_on() -> bool:
    if os.environ.get("IG_STEP_WATCH_VISION", "1").strip().lower() in (
        "0", "false", "off", "no",
    ):
        return False
    try:
        import ig_vision as vis
        return bool(vis.api_key())
    except Exception:
        return False


def configure(
    serial: str = "",
    username: str = "",
    pkg: str = "",
    goal: str = "",
    fmt: str = "",
) -> str:
    """Start a new step-watch run folder. Returns path or ''."""
    global _run_dir, _counter, _last_action
    if not enabled():
        return ""
    _counter = 0
    _last_action = ""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    s8 = re.sub(r"[^\w]", "", (serial or "nodevice"))[-8:] or "nodevice"
    _run_dir = Path(ROOT) / "logs" / "step_watch" / ("%s_%s" % (ts, s8))
    try:
        _run_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print("[step-watch] mkdir fail: %s" % e)
        _run_dir = None
        return ""
    _ctx.update(
        serial=(serial or "").strip(),
        username=(username or "").strip(),
        pkg=(pkg or "").strip(),
        goal=(goal or "Instagram farm automation").strip(),
        format=(fmt or "").strip().lower(),
    )
    manifest = {
        "started": datetime.now().isoformat(timespec="seconds"),
        "serial": _ctx["serial"],
        "username": _ctx["username"],
        "pkg": _ctx["pkg"],
        "goal": _ctx["goal"],
        "format": _ctx["format"],
        "vision": _vision_on(),
    }
    try:
        (_run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8",
        )
    except Exception:
        pass
    print("[step-watch] ON → %s" % _run_dir)
    return str(_run_dir)


def run_dir() -> str:
    return str(_run_dir) if _run_dir else ""


def _safe_label(*parts: str) -> str:
    raw = "_".join(p for p in parts if p).strip("_") or "step"
    raw = re.sub(r"[^\w.\-]+", "_", raw)[:72]
    return raw.strip("_") or "step"


def _screencap(serial: str) -> bytes:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += ["exec-out", "screencap", "-p"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=18)
    except Exception:
        return b""
    raw = r.stdout or b""
    if not raw.startswith(_PNG_MAGIC) and b"\r\n" in raw[:64]:
        raw = raw.replace(b"\r\n", b"\n")
    if not raw.startswith(_PNG_MAGIC) or len(raw) < 800:
        return b""
    return raw


def _append_jsonl(row: dict) -> None:
    if _run_dir is None:
        return
    try:
        with open(_run_dir / "steps.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _print_think(row: dict) -> None:
    n = row.get("n", "?")
    phase = row.get("phase", "")
    state = row.get("state", "")
    summary = (row.get("summary") or "")[:140]
    solution = (row.get("solution") or "")[:200]
    action = row.get("recommended_action") or {}
    act_label = action.get("label") or ""
    act_xy = ""
    if action.get("x") and action.get("y"):
        act_xy = " @ %s,%s" % (action["x"], action["y"])
    status = row.get("status") or ""
    png = row.get("png") or ""
    bits = ["[step-watch]", "#%s" % n, phase]
    if state:
        bits.append("state=%s" % state)
    if status:
        bits.append("status=%s" % status)
    print(" ".join(bits))
    if summary:
        print("[step-watch]   see: %s" % summary)
    if solution:
        print("[step-watch]   fix: %s" % solution)
    if act_label:
        print("[step-watch]   tap: %s%s — %s" % (
            act_label, act_xy, (action.get("why") or "")[:100]))
    if png:
        print("[step-watch]   shot: %s" % os.path.basename(png))


def _analyze_mode():
    """all | states | fail | off — default states (no vision on every tap)."""
    m = os.environ.get("IG_STEP_WATCH_ANALYZE", "states").strip().lower()
    if m in ("0", "off", "false", "no"):
        return "off"
    return m if m in ("all", "states", "fail") else "states"


def _tap_shots_on():
    return os.environ.get("IG_STEP_WATCH_TAPS", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _want_analyze(phase: str) -> bool:
    mode = _analyze_mode()
    if mode == "off":
        return False
    if mode == "all":
        return True
    if mode == "fail":
        return phase.startswith("fail_")
    # states: login/post/publish only — not per-tap
    p = (phase or "").lower()
    if p.startswith("tap_"):
        return False
    # Not publish_* — Grok/screencap on every caption round sat 14–18s staring.
    return p in ("login", "post_start", "warmup") or p.startswith("warmup_")


def step(
    phase: str,
    state: str = "",
    note: str = "",
    expected: str = "",
    last_action: str = "",
) -> dict:
    """Capture screenshot, analyze, log, print think-line. Returns analysis dict."""
    global _counter, _last_action
    if not enabled():
        return {}
    if _run_dir is None:
        configure(serial=_ctx.get("serial", ""), username=_ctx.get("username", ""))

    if last_action:
        _last_action = last_action
    _counter += 1
    n = _counter
    serial = _ctx.get("serial") or ""
    label = _safe_label(phase, state or "")
    png_path = _run_dir / ("%03d_%s.png" % (n, label))

    want_shot = _want_analyze(phase) or _tap_shots_on() or not (phase or "").startswith("tap_")
    t0 = time.time()
    png = b""
    if want_shot:
        for _try in range(3):
            png = _screencap(serial)
            if png:
                break
            time.sleep(0.6)
        if png:
            try:
                png_path.write_bytes(png)
            except Exception as e:
                print("[step-watch] save fail: %s" % e)
                png_path = Path("")

    analysis: dict = {}
    if png and _vision_on() and _want_analyze(phase):
        try:
            import ig_vision as vis
            analysis = vis.analyze_step(
                serial,
                phase=phase,
                goal=_ctx.get("goal") or "",
                context=note or _last_action or "",
                state=state or "",
                expected=expected or "",
                tag=label,
                force=True,
            ) or {}
        except Exception as e:
            print("[step-watch] vision error: %s" % e)

    if not analysis and png:
        analysis = {
            "screen": state or phase,
            "summary": note or ("UI state %s" % state if state else phase),
            "status": "no_vision",
            "solution": (
                "Screenshot saved. Add GEMINI_API_KEY or vision_key.txt "
                "and keep IG_STEP_WATCH_VISION=1 for AI analysis."
            ),
            "recommended_action": {},
            "blockers": [],
        }

    action = analysis.get("recommended_action") or {}
    if isinstance(action, dict):
        rec = {
            "label": action.get("label") or "",
            "x": action.get("x"),
            "y": action.get("y"),
            "why": action.get("why") or "",
        }
    else:
        rec = {}

    row = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "n": n,
        "phase": phase,
        "state": state or "",
        "note": note or "",
        "expected": expected or "",
        "last_action": _last_action,
        "serial": serial,
        "username": _ctx.get("username") or "",
        "pkg": _ctx.get("pkg") or "",
        "format": _ctx.get("format") or "",
        "png": str(png_path) if png_path else "",
        "screen": analysis.get("screen") or "",
        "summary": analysis.get("summary") or "",
        "status": analysis.get("status") or ("ok" if png else "no_shot"),
        "blockers": analysis.get("blockers") or [],
        "solution": analysis.get("solution") or "",
        "recommended_action": rec,
        "confidence": analysis.get("confidence"),
        "ms": int((time.time() - t0) * 1000),
    }
    _append_jsonl(row)
    _print_think(row)
    return analysis


def note_action(action: str) -> None:
    """Record last tap/type for next step's context."""
    global _last_action
    if action:
        _last_action = action.strip()[:240]
