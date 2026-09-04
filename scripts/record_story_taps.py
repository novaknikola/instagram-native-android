# -*- coding: utf-8 -*-
"""Record manual phone taps via `adb getevent` for story-flow automation tuning.

Usage
-----
  # Check phone + touch device (no recording):
  python scripts/record_story_taps.py --probe

  # Record until Ctrl+C (default serial = farm test phone):
  python scripts/record_story_taps.py
  python scripts/record_story_taps.py --serial 988a98454b3949444a30 --label story_link

While recording
---------------
  1. Stop any farm run (`run_ig_farm.py` / `run_ig_device.py`).
  2. Unlock the phone; keep the screen on.
  3. Start this script, then on the **physical phone** walk through the flow:
     + → Story tab → gallery image → sticker tray → Link → URL → CTA → Done → Share → highlight.
  4. Press Ctrl+C when finished.

Output (gitignored under logs/)
-------------------------------
  logs/manual_recordings/<label>_YYYYMMDD_HHMMSS.json
  logs/manual_recordings/<label>_YYYYMMDD_HHMMSS.txt

Prerequisites
-------------
  - `adb devices` shows the phone as `device`
  - USB debugging enabled
  - Touch the real device screen (Xiaowei mirror-only clicks may not appear here)

Notes
-----
  Samsung farm phones often route finger touch through `sec_e-pen` (event0). The script
  auto-detects touch axes and scales raw coordinates to screen pixels.
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "logs" / "manual_recordings"

DEFAULT_SERIAL = "988a98454b3949444a30"
TAP_MOVE_PX = 45  # movement below this → tap; above → swipe


def scale_coord(raw: int, raw_min: int, raw_max: int, screen: int) -> int:
    if raw_max <= raw_min:
        return raw
    v = (raw - raw_min) * (screen - 1) / (raw_max - raw_min)
    return max(0, min(screen - 1, int(round(v))))


@dataclass
class TouchAxes:
    device: str
    name: str
    x_code: str  # ABS_X or ABS_MT_POSITION_X
    y_code: str
    x_min: int = 0
    x_max: int = 1
    y_min: int = 0
    y_max: int = 1
    screen_w: int = 1440
    screen_h: int = 2960


@dataclass
class Recorder:
    serial: str
    label: str
    axes: TouchAxes
    started_at: float = field(default_factory=time.time)
    events: list = field(default_factory=list)
    _raw_lines: list = field(default_factory=list)
    _cur_x: int | None = None
    _cur_y: int | None = None
    _down_x: int | None = None
    _down_y: int | None = None
    _down_t: float | None = None
    _touch_down: bool = False

    def scale_x(self, raw: int) -> int:
        return scale_coord(raw, self.axes.x_min, self.axes.x_max, self.axes.screen_w)

    def scale_y(self, raw: int) -> int:
        return scale_coord(raw, self.axes.y_min, self.axes.y_max, self.axes.screen_h)

    def _emit_touch_end(self):
        if not self._touch_down or self._down_x is None or self._down_y is None:
            self._touch_down = False
            return
        t_ms = int((time.time() - self.started_at) * 1000)
        delay_ms = 0
        if self.events:
            delay_ms = t_ms - self.events[-1]["t_ms"]
        x1, y1 = self._down_x, self._down_y
        x2 = self._cur_x if self._cur_x is not None else x1
        y2 = self._cur_y if self._cur_y is not None else y1
        dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if dist >= TAP_MOVE_PX:
            ev = {
                "t_ms": t_ms,
                "delay_ms": delay_ms,
                "type": "swipe",
                "x1": x1, "y1": y1,
                "x2": x2, "y2": y2,
                "duration_ms": int((time.time() - (self._down_t or time.time())) * 1000),
            }
        else:
            ev = {
                "t_ms": t_ms,
                "delay_ms": delay_ms,
                "type": "tap",
                "x": x1,
                "y": y1,
            }
        self.events.append(ev)
        kind = ev["type"]
        if kind == "tap":
            print("[%6dms] tap @ %d,%d  (+%dms)" % (t_ms, x1, y1, delay_ms))
        else:
            print("[%6dms] swipe (%d,%d)->(%d,%d)  (+%dms)" % (
                t_ms, x1, y1, x2, y2, delay_ms))
        self._touch_down = False
        self._down_x = self._down_y = None
        self._down_t = None

    def feed_line(self, line: str):
        line = line.strip()
        if not line:
            return
        self._raw_lines.append(line)
        # getevent -l: /dev/input/event0: EV_ABS ABS_X 00001234
        m = re.match(
            r"(/dev/input/event\d+):\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?",
            line,
        )
        if not m:
            return
        dev, ev_type, code, val_hex, extra = m.groups()
        if dev != self.axes.device:
            return
        val = int(val_hex, 16)
        code_u = code.upper()

        if code_u in (self.axes.x_code.upper(), "ABS_X", "ABS_MT_POSITION_X", "0035"):
            self._cur_x = self.scale_x(val)
        elif code_u in (self.axes.y_code.upper(), "ABS_Y", "ABS_MT_POSITION_Y", "0036"):
            self._cur_y = self.scale_y(val)
        elif code_u == "ABS_MT_TRACKING_ID" and val == 0xFFFFFFFF:
            self._emit_touch_end()
        elif ev_type == "EV_KEY" and code_u == "BTN_TOUCH":
            state = (extra or "").upper()
            if state == "DOWN" or val == 1:
                if not self._touch_down and self._cur_x is not None and self._cur_y is not None:
                    self._touch_down = True
                    self._down_x = self._cur_x
                    self._down_y = self._cur_y
                    self._down_t = time.time()
            elif state == "UP" or val == 0:
                self._emit_touch_end()
        elif ev_type == "EV_SYN" and code_u == "SYN_REPORT":
            # Some devices report tap on SYN without explicit BTN_TOUCH UP.
            if self._touch_down and self._cur_x is not None:
                pass  # wait for UP / tracking id end

    def save(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base = OUT_DIR / ("%s_%s" % (self.label, stamp))
        ended = datetime.now().isoformat(timespec="seconds")
        payload = {
            "serial": self.serial,
            "label": self.label,
            "started_at": datetime.fromtimestamp(self.started_at).isoformat(timespec="seconds"),
            "ended_at": ended,
            "screen": {"width": self.axes.screen_w, "height": self.axes.screen_h},
            "touch_device": self.axes.device,
            "touch_name": self.axes.name,
            "axes": {
                "x": self.axes.x_code,
                "y": self.axes.y_code,
                "x_max": self.axes.x_max,
                "y_max": self.axes.y_max,
            },
            "event_count": len(self.events),
            "events": self.events,
        }
        jpath = base.with_suffix(".json")
        tpath = base.with_suffix(".txt")
        jpath.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        lines = [
            "Manual touch recording",
            "serial: %s" % self.serial,
            "screen: %dx%d" % (self.axes.screen_w, self.axes.screen_h),
            "device: %s (%s)" % (self.axes.device, self.axes.name),
            "events: %d" % len(self.events),
            "",
        ]
        for i, ev in enumerate(self.events, 1):
            if ev["type"] == "tap":
                lines.append("%2d. +%5dms  tap %d,%d" % (i, ev["delay_ms"], ev["x"], ev["y"]))
            else:
                lines.append("%2d. +%5dms  swipe (%d,%d)->(%d,%d) %dms" % (
                    i, ev["delay_ms"], ev["x1"], ev["y1"], ev["x2"], ev["y2"],
                    ev.get("duration_ms", 0)))
        tpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\nSaved:")
        print(" ", jpath)
        print(" ", tpath)
        return jpath, tpath


def adb(serial: str, *args, timeout=30) -> str:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    return (r.stdout or "") + (r.stderr or "")


def screen_size(serial: str) -> tuple[int, int]:
    out = adb(serial, "shell", "wm", "size")
    m = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"Override size:\s*(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1440, 2960


def _parse_axis_max(line: str) -> int | None:
    m = re.search(r"max\s+(\d+)", line)
    return int(m.group(1)) if m else None


def discover_touch_axes(serial: str) -> TouchAxes | None:
    """Pick the best touch device from `getevent -pl`."""
    out = adb(serial, "shell", "getevent", "-pl", timeout=60)
    sw, sh = screen_size(serial)
    candidates: list[dict] = []
    cur: dict | None = None
    for line in out.splitlines():
        line = line.rstrip()
        if line.startswith("add device"):
            if cur:
                candidates.append(cur)
            cur = {
                "device": line.split()[-1],
                "name": "",
                "has_btn_touch": False,
                "x_code": "",
                "y_code": "",
                "x_max": 0,
                "y_max": 0,
                "score": 0,
            }
            continue
        if cur is None:
            continue
        if "name:" in line:
            cur["name"] = line.split("name:", 1)[-1].strip().strip('"')
        if "BTN_TOUCH" in line:
            cur["has_btn_touch"] = True
            cur["score"] += 10
        if "ABS_MT_POSITION_X" in line:
            cur["x_code"] = "ABS_MT_POSITION_X"
            mx = _parse_axis_max(line)
            if mx:
                cur["x_max"] = mx
            cur["score"] += 30
        elif "ABS_X" in line and "ABS_MT" not in line and not cur["x_code"]:
            cur["x_code"] = "ABS_X"
            mx = _parse_axis_max(line)
            if mx:
                cur["x_max"] = mx
            cur["score"] += 15
        if "ABS_MT_POSITION_Y" in line:
            cur["y_code"] = "ABS_MT_POSITION_Y"
            my = _parse_axis_max(line)
            if my:
                cur["y_max"] = my
            cur["score"] += 30
        elif "ABS_Y" in line and "ABS_MT" not in line and not cur["y_code"]:
            cur["y_code"] = "ABS_Y"
            my = _parse_axis_max(line)
            if my:
                cur["y_max"] = my
            cur["score"] += 15
        nm = cur["name"].lower()
        if "touch" in nm or "ts" in nm or "sec_e-pen" in nm:
            cur["score"] += 20
    if cur:
        candidates.append(cur)

    good = [c for c in candidates if c["x_code"] and c["y_code"] and c["x_max"] > 0]
    if not good:
        return None
    good.sort(key=lambda c: c["score"], reverse=True)
    best = good[0]
    return TouchAxes(
        device=best["device"],
        name=best["name"],
        x_code=best["x_code"],
        y_code=best["y_code"],
        x_max=best["x_max"],
        y_max=best["y_max"],
        screen_w=sw,
        screen_h=sh,
    )


def probe(serial: str) -> int:
    print("ADB serial: %s" % serial)
    devs = adb(serial, "devices")
    if "\tdevice" not in devs or serial not in devs:
        print("FAIL: phone not listed as device in `adb devices`")
        return 2
    sw, sh = screen_size(serial)
    print("Screen: %dx%d" % (sw, sh))
    axes = discover_touch_axes(serial)
    if not axes:
        print("FAIL: no touch axes found via getevent -pl")
        print("Try touching the phone while recording anyway, or check USB debugging.")
        return 1
    print("Touch device: %s (%s)" % (axes.device, axes.name))
    print("Axes: %s max=%d, %s max=%d" % (axes.x_code, axes.x_max, axes.y_code, axes.y_max))
    print("Scaled example raw (%d,%d) -> (%d,%d)" % (
        axes.x_max // 2, axes.y_max // 2,
        scale_coord(axes.x_max // 2, axes.x_min, axes.x_max, axes.screen_w),
        scale_coord(axes.y_max // 2, axes.y_min, axes.y_max, axes.screen_h),
    ))
    # Quick getevent readability test (2s, no touch required)
    print("getevent -l: opening stream...")
    cmd = ["adb", "-s", serial, "shell", "getevent", "-l", axes.device]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            proc.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("getevent stream: OK (tap the screen during recording to capture coords)")
    except Exception as e:
        print("getevent smoke failed: %s" % e)
        return 1
    print("Probe OK — run without --probe to record.")
    return 0


def record(serial: str, label: str) -> int:
    axes = discover_touch_axes(serial)
    if not axes:
        print("No touch device found. Run with --probe for details.")
        return 1
    rec = Recorder(serial=serial, label=label, axes=axes)
    print("Recording on %s (%s) screen %dx%d" % (
        axes.device, axes.name, axes.screen_w, axes.screen_h))
    print("Walk through the story flow on the phone. Ctrl+C to stop.\n")

    cmd = ["adb", "-s", serial, "shell", "getevent", "-l", axes.device]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )

    def _stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _stop)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            rec.feed_line(line)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
        if rec._touch_down:
            rec._emit_touch_end()
        if rec.events:
            rec.save()
        else:
            print("No taps captured. Was the screen touched? Try --probe first.")
            return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="Record manual phone taps via adb getevent")
    ap.add_argument("--serial", default=DEFAULT_SERIAL, help="adb serial (default: test phone)")
    ap.add_argument("--label", default="story", help="output filename prefix")
    ap.add_argument("--probe", action="store_true", help="check adb/getevent only; do not record")
    args = ap.parse_args()
    if args.probe:
        sys.exit(probe(args.serial))
    sys.exit(record(args.serial, args.label))


if __name__ == "__main__":
    main()
