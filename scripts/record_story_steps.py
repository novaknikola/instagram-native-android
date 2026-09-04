# -*- coding: utf-8 -*-
"""Remote story-flow recorder (no physical touch needed).

For farm operators who only control phones via Xiaowei mirror:
  1. Run this script.
  2. In the mirror, do ONE action (tap, type, swipe).
  3. Press Enter here -> saves screenshot + UI XML + optional vision summary.
  4. Repeat until the story+link+highlight flow is done.
  5. Type 'done' + Enter to finish.

Output: logs/manual_recordings/steps_<label>_<timestamp>/
  step_001.png, step_001.xml, steps.json, steps.txt

Usage:
  python scripts/record_story_steps.py
  python scripts/record_story_steps.py --serial 988a98454b3949444a30 --label story_link
  python scripts/record_story_steps.py --vision   # Grok/Gemini describe each screen (needs key)
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "logs" / "manual_recordings"
DEFAULT_SERIAL = "988a98454b3949444a30"


def adb(serial: str, *args, timeout=30) -> bytes | str:
    cmd = ["adb", "-s", serial] + list(args)
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if args and args[0] == "exec-out":
        return r.stdout or b""
    return (r.stdout or b"").decode("utf-8", errors="replace")


def screencap(serial: str) -> bytes:
    return adb(serial, "exec-out", "screencap", "-p", timeout=20) or b""


def uia_dump(serial: str) -> str:
    adb(serial, "shell", "uiautomator", "dump", "/sdcard/ui_step.xml", timeout=25)
    return adb(serial, "shell", "cat", "/sdcard/ui_step.xml", timeout=15) or ""


def _labels_from_xml(xml: str, limit=24) -> list[str]:
    labels = []
    for m in re.finditer(r'(?:text|content-desc)="([^"]{1,80})"', xml):
        t = m.group(1).strip()
        if t and t not in labels:
            labels.append(t)
        if len(labels) >= limit:
            break
    return labels


def _vision_summary(serial: str, png: bytes, step_n: int) -> str:
    try:
        sys.path.insert(0, str(ROOT))
        import ig_vision as vis
        if not vis.enabled():
            return ""
        # Write temp not needed - locate uses screencap internally; save png and pass via locate
        # ig_vision.locate does its own screencap - use find with custom: save png first
        d = ROOT / "logs" / "vision"
        d.mkdir(parents=True, exist_ok=True)
        p = d / ("step_%03d.png" % step_n)
        p.write_bytes(png)
        data = vis.locate(
            serial,
            "Describe this Instagram screen in one short sentence. "
            "List visible buttons: POST, STORY, Link, Done, Share, Your story if present.",
            want=["POST tab", "STORY tab", "Link", "Done", "Share"],
            tag="step_%03d" % step_n,
        )
        return (data.get("summary") or "").strip()
    except Exception as e:
        return "vision_error: %s" % e


def main():
    ap = argparse.ArgumentParser(description="Record story flow by screenshot after each mirror action")
    ap.add_argument("--serial", default=DEFAULT_SERIAL)
    ap.add_argument("--label", default="story_steps")
    ap.add_argument("--vision", action="store_true", help="call Grok/Gemini on each step")
    args = ap.parse_args()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / ("%s_%s" % (args.label, stamp))
    out_dir.mkdir(parents=True, exist_ok=True)

    steps: list[dict] = []
    print("Remote step recorder — serial=%s" % args.serial)
    print("Output: %s" % out_dir)
    print("")
    print("In Xiaowei mirror: do ONE action, then press Enter here.")
    print("Type 'done' when finished.\n")

    n = 0
    while True:
        try:
            line = input("Step %d ready? [Enter=capture / done=finish / note:text]: " % (n + 1)).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nStopping.")
            break
        if line.lower() in ("done", "d", "quit", "q"):
            break
        note = ""
        if line.lower().startswith("note:"):
            note = line[5:].strip()

        n += 1
        png = screencap(args.serial)
        xml = uia_dump(args.serial)
        labels = _labels_from_xml(xml)

        png_path = out_dir / ("step_%03d.png" % n)
        xml_path = out_dir / ("step_%03d.xml" % n)
        if png.startswith(b"\x89PNG"):
            png_path.write_bytes(png)
        xml_path.write_text(xml, encoding="utf-8", errors="replace")

        summary = ""
        if args.vision and png.startswith(b"\x89PNG"):
            summary = _vision_summary(args.serial, png, n)
            if summary:
                print("  vision: %s" % summary[:120])

        step = {
            "n": n,
            "note": note,
            "png": png_path.name,
            "xml": xml_path.name,
            "labels": labels,
            "vision": summary,
        }
        steps.append(step)
        print("  saved step_%03d (%d labels) %s" % (n, len(labels), note or ""))

    meta = {
        "serial": args.serial,
        "label": args.label,
        "step_count": len(steps),
        "steps": steps,
    }
    jpath = out_dir / "steps.json"
    tpath = out_dir / "steps.txt"
    jpath.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    lines = ["Remote story step recording", "steps: %d" % len(steps), ""]
    for s in steps:
        lines.append("step %d%s" % (s["n"], (" — " + s["note"]) if s.get("note") else ""))
        if s.get("vision"):
            lines.append("  vision: %s" % s["vision"])
        for lb in (s.get("labels") or [])[:12]:
            lines.append("  - %s" % lb)
        lines.append("")
    tpath.write_text("\n".join(lines), encoding="utf-8")
    print("\nSaved %d steps -> %s" % (len(steps), out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
