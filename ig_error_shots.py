# -*- coding: utf-8 -*-
"""
ig_error_shots.py - Capture ADB screenshot (+ UI XML) on hard automation fails.

Default storage (always, unless IG_ERROR_SHOT_DIR overrides):
  <IG_FARM_BASE>/logs/error_shots/loose/
    index.jsonl
    <serial>_<user>_<RESULT>_<HHMMSS>.png
    <serial>_<user>_<RESULT>_<HHMMSS>.xml

Console / farm runs may set IG_ERROR_SHOT_DIR to a per-run sibling of the log
(e.g. logs/ig_run_2026-08-10_091500/).

Never raises into callers. Never auto-taps. Vision/Grok is offline (ig_diagnose_shots.py).
"""
from __future__ import annotations

from farm_root import ROOT

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Results that deserve a shot (hard / actionable fails). Soft OK / in-progress omitted.
SHOT_RESULTS: Set[str] = {
    "APP_WONT_OPEN",
    "UNKNOWN_STUCK",
    "LOGIN_TIMEOUT",
    "PASSKEY_STUCK",
    "LOGIN_REJECTED",
    "LOGIN_META_ERROR",
    "LOGIN_STUCK",
    "NO_LOGIN_BTN",
    "ACCOUNT_NOT_FOUND",
    "ACCOUNT_SUSPENDED",
    "ACCOUNT_CHALLENGED",
    "CAPTCHA",
    "POST_CAPTCHA",
    "HUMAN_BLOCKED",
    "CONTACT_VERIFY",
    "CHALLENGE",
    "SIGNUP_LOOP",
    "TIP_STUCK",
    "POST_TIMEOUT",
    "POST_BLOCKED",
    "POST_RATE_LIMIT",
    "NO_IMAGE",
    "PROXY_DEAD",
    "ALL_IPS_MASKED",
    "WRONG_APP",
    "NO_ACCOUNT_DRIVE",
    "DEAD",
    "REPLACED_NO_CREDS",
    "ERROR",
}

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_last_key = ""
_last_ts = 0.0
_DEDUP_SECS = 20.0


def _farm_base() -> Path:
    return Path(os.environ.get("IG_FARM_BASE", ROOT))


def default_loose_dir() -> Path:
    """Canonical default for CLI / non-console runs."""
    return _farm_base() / "logs" / "error_shots" / "loose"


def ensure_default_env() -> Path:
    """If IG_ERROR_SHOT_DIR unset, point it at the farm loose dir (default ON)."""
    env = (os.environ.get("IG_ERROR_SHOT_DIR") or "").strip()
    if env:
        p = Path(env)
    else:
        p = default_loose_dir()
        os.environ["IG_ERROR_SHOT_DIR"] = str(p)
    try:
        p.mkdir(parents=True, exist_ok=True)
        (p / "index.jsonl").touch(exist_ok=True)
    except Exception:
        pass
    return p


def shot_dir() -> Path:
    """Active error-shot directory (run-scoped or loose fallback)."""
    return ensure_default_env()


def ensure_run_shot_dir(run_log_path: Path) -> Path:
    """
    Create sibling folder for a per-run log file.
    ig_run_2026-08-10_091500.txt -> logs/ig_run_2026-08-10_091500/
    """
    run_log_path = Path(run_log_path)
    d = run_log_path.parent / run_log_path.stem
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / "index.jsonl").touch(exist_ok=True)
    except Exception:
        pass
    return d


def _safe(s: str, n: int = 40) -> str:
    s = _SAFE_RE.sub("_", (s or "x").strip())[:n]
    return s or "x"


def should_capture(result: str) -> bool:
    return (result or "").strip().upper() in SHOT_RESULTS


def _adb(serial: str, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    cmd = ["adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += list(args)
    return subprocess.run(
        cmd, capture_output=True, timeout=timeout
    )


def _screencap_png(serial: str, timeout: float = 18.0) -> bytes:
    try:
        r = _adb(serial, "exec-out", "screencap", "-p", timeout=timeout)
    except subprocess.TimeoutExpired:
        return b""
    except Exception:
        return b""
    raw = r.stdout or b""
    if not raw.startswith(_PNG_MAGIC) and b"\r\n" in raw[:64]:
        raw = raw.replace(b"\r\n", b"\n")
    if not raw.startswith(_PNG_MAGIC) or len(raw) < 800:
        return b""
    return raw


def _dump_xml(serial: str, timeout: float = 20.0) -> str:
    remote = "/sdcard/ig_err_ui.xml"
    try:
        _adb(serial, "shell", "uiautomator", "dump", remote, timeout=min(15.0, timeout))
        r = _adb(serial, "shell", "cat", remote, timeout=timeout)
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        return text if "<node" in text or "hierarchy" in text.lower() else ""
    except Exception:
        return ""


def _append_index(row: Dict[str, Any]) -> None:
    path = shot_dir() / "index.jsonl"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def capture(
    serial: str,
    result: str,
    username: str = "",
    note: str = "",
    pkg: str = "",
    include_xml: bool = True,
    build: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """
    Take PNG (+ optional XML) for a hard fail. Returns metadata dict.
    Dedupes identical serial+result within a few seconds.
    """
    out: Dict[str, Any] = {
        "ok": False,
        "skipped": False,
        "result": result,
        "serial": serial or "",
        "png": "",
        "xml": "",
        "error": "",
    }
    result = (result or "").strip().upper()
    if not force and not should_capture(result):
        out["skipped"] = True
        out["error"] = "result not in SHOT_RESULTS"
        return out
    if not (serial or "").strip():
        out["error"] = "no serial"
        return out

    global _last_key, _last_ts
    key = "%s|%s" % (serial, result)
    now = time.time()
    if not force and key == _last_key and (now - _last_ts) < _DEDUP_SECS:
        out["skipped"] = True
        out["error"] = "dedup"
        return out

    d = shot_dir()
    ts = datetime.now().strftime("%H%M%S")
    stamp = datetime.now().isoformat(timespec="seconds")
    base = "%s_%s_%s_%s" % (
        _safe(serial, 24),
        _safe(username or "nouser", 24),
        _safe(result, 32),
        ts,
    )
    png_path = d / (base + ".png")
    xml_path = d / (base + ".xml")

    t0 = time.time()
    raw = _screencap_png(serial)
    if not raw:
        out["error"] = "screencap failed/timeout"
        _append_index(
            {
                "ts": stamp,
                "serial": serial,
                "username": username or "",
                "result": result,
                "note": note or "",
                "pkg": pkg or "",
                "build": build or "",
                "png": "",
                "xml": "",
                "ok": False,
                "error": out["error"],
                "ms": int((time.time() - t0) * 1000),
            }
        )
        print("[shot] FAIL screencap %s %s" % (serial, result))
        return out

    try:
        tmp = png_path.with_suffix(".png.tmp")
        tmp.write_bytes(raw)
        tmp.replace(png_path)
    except Exception as e:
        out["error"] = "write png: %s" % e
        print("[shot] FAIL write %s" % e)
        return out

    xml_text = ""
    if include_xml:
        xml_text = _dump_xml(serial)
        if xml_text:
            try:
                xml_path.write_text(xml_text, encoding="utf-8")
            except Exception:
                xml_text = ""

    row = {
        "ts": stamp,
        "serial": serial,
        "username": username or "",
        "result": result,
        "note": (note or "")[:240],
        "pkg": pkg or "",
        "build": build or "",
        "png": str(png_path),
        "png_name": png_path.name,
        "xml": str(xml_path) if xml_text else "",
        "xml_name": xml_path.name if xml_text else "",
        "ok": True,
        "bytes": len(raw),
        "ms": int((time.time() - t0) * 1000),
        "dir": str(d),
    }
    _append_index(row)
    _last_key = key
    _last_ts = now
    out.update(
        {
            "ok": True,
            "png": str(png_path),
            "xml": row["xml"],
            "png_name": png_path.name,
            "dir": str(d),
            "ms": row["ms"],
        }
    )
    print(
        "[shot] %s user=%s result=%s file=%s%s"
        % (
            serial,
            username or "-",
            result,
            png_path.name,
            (" xml=" + xml_path.name) if xml_text else "",
        )
    )
    return out


def list_shots(limit: int = 50, directory: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest-first incidents from index.jsonl (and orphan PNGs)."""
    d = Path(directory) if directory else shot_dir()
    rows: List[Dict[str, Any]] = []
    seen_png: Set[str] = set()
    idx = d / "index.jsonl"
    if idx.exists():
        try:
            lines = idx.read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in reversed(lines):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    row = json.loads(ln)
                except Exception:
                    continue
                if not row.get("png_name") and row.get("png"):
                    row["png_name"] = Path(str(row["png"])).name
                if not row.get("xml_name") and row.get("xml"):
                    row["xml_name"] = Path(str(row["xml"])).name
                if row.get("png_name"):
                    seen_png.add(row["png_name"])
                rows.append(row)
                if len(rows) >= limit:
                    break
        except Exception:
            pass
    # Orphan PNGs (index write failed mid-run)
    if len(rows) < limit and d.is_dir():
        try:
            pngs = sorted(
                [p for p in d.glob("*.png") if p.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in pngs:
                if p.name in seen_png:
                    continue
                stem = p.stem
                parts = stem.split("_")
                result = parts[-2] if len(parts) >= 3 else "UNKNOWN"
                xml = d / (stem + ".xml")
                rows.append(
                    {
                        "ts": datetime.fromtimestamp(p.stat().st_mtime).isoformat(
                            timespec="seconds"
                        ),
                        "serial": parts[0] if parts else "",
                        "username": parts[1] if len(parts) > 1 else "",
                        "result": result,
                        "note": "orphan_png",
                        "png": str(p),
                        "png_name": p.name,
                        "xml": str(xml) if xml.is_file() else "",
                        "xml_name": xml.name if xml.is_file() else "",
                        "ok": True,
                    }
                )
                if len(rows) >= limit:
                    break
        except Exception:
            pass
    return rows


def resolve_shot_file(name: str, directory: Optional[str] = None) -> Optional[Path]:
    """Safe resolve basename under shot dir."""
    name = Path((name or "").strip()).name
    if not name or ".." in name:
        return None
    if not (name.endswith(".png") or name.endswith(".xml") or name.endswith(".jsonl")
            or name.endswith(".md")):
        return None
    d = Path(directory) if directory else shot_dir()
    p = d / name
    return p if p.is_file() else None
