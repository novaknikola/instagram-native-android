# -*- coding: utf-8 -*-
# ig_reel_trace.py — durable Reel prove diagnostics (JSONL + UI XML dumps).
#
# Tracking: C:\threads-android\ig_reel_trace.jsonl
# Dumps:    C:\threads-android\dumps\reel_<user>_<ts>_<phase>.xml
#
# Used by ig_loop.do_post when format=reel. Soft-fail everything.
from __future__ import annotations

from farm_root import ROOT
import json
import os
import re
import subprocess
import time
from datetime import datetime

_BASE = os.environ.get("IG_FARM_BASE", ROOT)
TRACE_PATH = os.path.join(_BASE, "ig_reel_trace.jsonl")
DUMP_DIR = os.path.join(_BASE, "dumps")

_ctx = {
    "serial": "",
    "username": "",
    "pkg": "",
    "fmt": "reel",
}


def configure(serial="", username="", pkg="", fmt="reel"):
    _ctx["serial"] = (serial or "").strip()
    _ctx["username"] = (username or "").strip()
    _ctx["pkg"] = (pkg or "").strip()
    _ctx["fmt"] = (fmt or "reel").strip().lower() or "reel"


def _safe(s, n=200):
    s = re.sub(r"\s+", " ", (s or "").replace("\n", " ")).strip()
    return s[:n]


def log(phase, **fields):
    """Append one JSONL event. Also print a short line for Console log."""
    try:
        row = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "serial": _ctx.get("serial") or fields.pop("serial", ""),
            "username": _ctx.get("username") or fields.pop("username", ""),
            "pkg": _ctx.get("pkg") or fields.pop("pkg", ""),
            "fmt": _ctx.get("fmt") or "reel",
            "phase": phase,
        }
        for k, v in fields.items():
            if isinstance(v, str) and k in ("snippet", "tapped", "desc"):
                row[k] = _safe(v, 240)
            elif k == "tiles" and isinstance(v, list):
                row[k] = [_safe(str(x), 80) for x in v[:6]]
            else:
                row[k] = v
        parent = os.path.dirname(TRACE_PATH)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(TRACE_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        bits = ["[reel-trace]", phase]
        for k in ("step", "state", "cta", "result", "ok", "sel_video", "unsel_video",
                  "next_btns", "xml_path"):
            if k in row and row[k] not in ("", None):
                bits.append("%s=%s" % (k, row[k]))
        print(" ".join(str(b) for b in bits))
    except Exception as e:
        print("[reel-trace] log fail: %s" % e)


def dump_ui_xml(serial, phase="stuck", username=""):
    """uiautomator dump → host dumps/reel_*.xml. Returns path or ''."""
    serial = (serial or _ctx.get("serial") or "").strip()
    user = re.sub(r"[^\w.\-]+", "_", (username or _ctx.get("username") or "unknown"))[:40]
    phase = re.sub(r"[^\w.\-]+", "_", (phase or "stuck"))[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        os.makedirs(DUMP_DIR, exist_ok=True)
    except Exception:
        pass
    host = os.path.join(DUMP_DIR, "reel_%s_%s_%s.xml" % (user, ts, phase))
    remote = "/sdcard/ui_reel_dump.xml"
    try:
        if serial:
            cmd_prefix = ["adb", "-s", serial]
        else:
            cmd_prefix = ["adb"]
        subprocess.run(
            cmd_prefix + ["shell", "uiautomator", "dump", remote],
            capture_output=True, text=True, timeout=45, errors="replace",
        )
        r = subprocess.run(
            cmd_prefix + ["pull", remote, host],
            capture_output=True, text=True, timeout=45, errors="replace",
        )
        if os.path.isfile(host) and os.path.getsize(host) > 50:
            # Do not pass phase= — log()'s first arg IS phase (TypeError if both)
            log("xml_dump", dump_phase=phase, xml_path=host, pull_rc=r.returncode)
            print("[reel-trace] saved UI dump %s" % host)
            return host
        # Fallback: cat into file
        cat = subprocess.run(
            cmd_prefix + ["shell", "cat", remote],
            capture_output=True, text=True, timeout=45, errors="replace",
        )
        body = cat.stdout or ""
        if len(body) > 50:
            with open(host, "w", encoding="utf-8") as fh:
                fh.write(body)
            log("xml_dump", dump_phase=phase, xml_path=host, via="cat")
            print("[reel-trace] saved UI dump (cat) %s" % host)
            return host
    except Exception as e:
        print("[reel-trace] dump_ui_xml fail: %s" % e)
        log("xml_dump_fail", dump_phase=phase, error=str(e))
    return ""


def fail(phase, serial="", username="", **fields):
    """Log failure + pull XML. Returns xml path."""
    fields.pop("phase", None)  # never double-pass phase into log()
    xml = dump_ui_xml(serial or _ctx.get("serial"), phase=phase, username=username)
    log(phase, result="FAIL", xml_path=xml, **fields)
    return xml
