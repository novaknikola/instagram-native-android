# -*- coding: utf-8 -*-
"""Fleet Screens: cached thumbs + queued ADB screencap. Soft-fail; never raises to UI."""
from __future__ import annotations

import io
import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import config
from ..util import ensure_dir

_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{4,64}$")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

try:
    from PIL import Image  # type: ignore

    _HAS_PIL = True
except Exception:
    Image = None  # type: ignore
    _HAS_PIL = False


def safe_serial(raw: str) -> str:
    s = (raw or "").strip()
    if not _SERIAL_RE.match(s):
        return ""
    return s


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _age_band(age_s: Optional[float]) -> str:
    if age_s is None:
        return "missing"
    if age_s <= float(config.SCREEN_FRESH_SECS):
        return "fresh"
    if age_s <= float(config.SCREEN_STALE_SECS):
        return "ok"
    return "stale"


class ScreenService:
    """Disk-backed shots + single worker queue (farm-aware concurrency)."""

    def __init__(self, farm_active_fn: Optional[Callable[[], bool]] = None):
        self.root = Path(config.PHONE_SHOTS_DIR)
        ensure_dir(self.root)
        self._farm_active_fn = farm_active_fn or (lambda: False)
        self._dev_cache: Tuple[float, List[str], str] = (0.0, [], "")
        self._dev_ttl = 8.0
        self._q: "queue.PriorityQueue[Tuple[int, float, str, Dict[str, Any]]]" = (
            queue.PriorityQueue()
        )
        self._seq = 0
        self._lock = threading.Lock()
        self._busy_serials: set = set()
        self._cancel = threading.Event()
        self._job: Dict[str, Any] = {
            "id": "",
            "active": False,
            "mode": "",
            "total": 0,
            "done": 0,
            "ok": 0,
            "fail": 0,
            "results": [],
            "message": "",
        }
        self._listeners: List[queue.Queue] = []
        self._listeners_lock = threading.Lock()
        self._workers_started = False
        self._start_workers()

    def _start_workers(self) -> None:
        if self._workers_started:
            return
        self._workers_started = True
        for i in range(2):
            t = threading.Thread(
                target=self._worker_loop, name="ig-screen-%d" % i, daemon=True
            )
            t.start()

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        msg = {"event": event, "data": payload, "ts": time.time()}
        with self._listeners_lock:
            dead = []
            for q in self._listeners:
                try:
                    q.put_nowait(msg)
                except Exception:
                    dead.append(q)
            for q in dead:
                try:
                    self._listeners.remove(q)
                except ValueError:
                    pass

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._listeners_lock:
            self._listeners.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._listeners_lock:
            try:
                self._listeners.remove(q)
            except ValueError:
                pass

    def pillow_ok(self) -> bool:
        return bool(_HAS_PIL)

    def phone_dir(self, serial: str) -> Path:
        return self.root / serial

    def paths(self, serial: str) -> Dict[str, Path]:
        d = self.phone_dir(serial)
        return {
            "dir": d,
            "full": d / "full.png",
            "thumb": d / "thumb.jpg",
            "meta": d / "meta.json",
        }

    def read_meta(self, serial: str) -> Dict[str, Any]:
        p = self.paths(serial)["meta"]
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _write_meta(self, serial: str, meta: Dict[str, Any]) -> None:
        ensure_dir(self.phone_dir(serial))
        p = self.paths(serial)["meta"]
        tmp = p.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            try:
                tmp.unlink()
            except Exception:
                pass

    def _adb(self, *args: str, timeout: float = 12.0) -> Tuple[int, bytes, str]:
        try:
            r = subprocess.run(
                ["adb", *args],
                capture_output=True,
                timeout=timeout,
            )
            err = (r.stderr or b"").decode("utf-8", errors="replace")
            return r.returncode, r.stdout or b"", err
        except subprocess.TimeoutExpired:
            return 124, b"", "timeout"
        except FileNotFoundError:
            return 127, b"", "adb not on PATH"
        except Exception as e:
            return 1, b"", "%s" % e

    def list_serials(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        ts, cached, err = self._dev_cache
        if not force and cached is not None and (now - ts) < self._dev_ttl:
            return {
                "serials": list(cached),
                "adb_ok": shutil.which("adb") is not None,
                "error": err,
                "cached": True,
            }
        if not shutil.which("adb"):
            self._dev_cache = (now, [], "adb not on PATH")
            return {
                "serials": [],
                "adb_ok": False,
                "error": "adb not on PATH",
                "cached": False,
            }
        code, out, aerr = self._adb("devices", timeout=10.0)
        text = (out or b"").decode("utf-8", errors="replace")
        serials: List[str] = []
        offline: List[str] = []
        if code == 124 or (not text.strip() and aerr == "timeout"):
            err = "adb devices timed out (ADB stuck). Run: adb kill-server && taskkill /F /IM adb.exe && adb start-server"
            self._dev_cache = (now, [], err)
            return {"serials": [], "adb_ok": False, "error": err, "cached": False}
        for ln in text.splitlines()[1:]:
            if "\tdevice" in ln:
                s = safe_serial(ln.split()[0])
                if s:
                    serials.append(s)
            elif "\toffline" in ln or "\tunauthorized" in ln:
                offline.append(ln.split()[0])
        err = ""
        if offline and not serials:
            err = "phones visible but offline/unauthorized: %s" % ", ".join(offline[:8])
        self._dev_cache = (now, serials, err)
        return {
            "serials": serials,
            "adb_ok": True,
            "error": err,
            "offline": offline,
            "cached": False,
        }

    def rescan(self) -> Dict[str, Any]:
        self._dev_cache = (0.0, [], "")
        return self.list_serials(force=True)

    def _phone_row(self, serial: str) -> Dict[str, Any]:
        meta = self.read_meta(serial)
        paths = self.paths(serial)
        has_thumb = paths["thumb"].exists() and paths["thumb"].stat().st_size > 200
        has_full = paths["full"].exists() and paths["full"].stat().st_size > 800
        captured_at = meta.get("captured_at") or ""
        age_s = None
        if captured_at:
            try:
                # accept iso with or without tz
                dt = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
                age_s = max(0.0, time.time() - dt.timestamp())
            except Exception:
                try:
                    age_s = max(0.0, time.time() - paths["thumb"].stat().st_mtime)
                except Exception:
                    age_s = None
        elif has_thumb:
            try:
                age_s = max(0.0, time.time() - paths["thumb"].stat().st_mtime)
            except Exception:
                age_s = None
        band = _age_band(age_s)
        mtime = 0
        try:
            if has_thumb:
                mtime = int(paths["thumb"].stat().st_mtime)
        except Exception:
            mtime = 0
        with self._lock:
            capturing = serial in self._busy_serials
        return {
            "serial": serial,
            "has_thumb": has_thumb,
            "has_full": has_full,
            "captured_at": captured_at,
            "age_s": None if age_s is None else round(age_s),
            "band": band,
            "error": meta.get("error") or "",
            "mtime": mtime,
            "capturing": capturing,
            "duration_ms": meta.get("duration_ms"),
        }

    def snapshot(self, farm_active: Optional[bool] = None) -> Dict[str, Any]:
        listing = self.list_serials()
        serials = listing.get("serials") or []
        phones = [self._phone_row(s) for s in serials]
        counts = {"fresh": 0, "ok": 0, "stale": 0, "missing": 0}
        for p in phones:
            counts[p["band"]] = counts.get(p["band"], 0) + 1
        if farm_active is None:
            try:
                farm_active = bool(self._farm_active_fn())
            except Exception:
                farm_active = False
        with self._lock:
            job = dict(self._job)
        return {
            "adb_ok": listing.get("adb_ok", False),
            "error": listing.get("error") or "",
            "pillow_ok": self.pillow_ok(),
            "farm_active": farm_active,
            "stale_secs": int(config.SCREEN_STALE_SECS),
            "fresh_secs": int(config.SCREEN_FRESH_SECS),
            "phones": phones,
            "counts": counts,
            "job": job,
        }

    def _make_thumb(self, png_bytes: bytes, thumb_path: Path) -> Tuple[bool, str]:
        if not _HAS_PIL or Image is None:
            return False, "Pillow not installed (pip install Pillow)"
        try:
            im = Image.open(io.BytesIO(png_bytes))
            im = im.convert("RGB")
            w = max(120, int(config.SCREEN_THUMB_WIDTH))
            if im.width > w:
                h = int(im.height * (w / float(im.width)))
                try:
                    resample = Image.Resampling.BILINEAR
                except AttributeError:
                    resample = Image.BILINEAR  # type: ignore
                im = im.resize((w, max(1, h)), resample)
            ensure_dir(thumb_path.parent)
            tmp = thumb_path.with_suffix(".tmp.jpg")
            im.save(tmp, format="JPEG", quality=72, optimize=True)
            tmp.replace(thumb_path)
            return True, ""
        except Exception as e:
            return False, "thumb failed: %s" % e

    def _atomic_write(self, path: Path, data: bytes) -> None:
        ensure_dir(path.parent)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def capture_one(self, serial: str, wake: bool = False) -> Dict[str, Any]:
        """Synchronous capture (called from worker). Never raises."""
        serial = safe_serial(serial)
        if not serial:
            return {"ok": False, "serial": "", "error": "invalid serial"}
        t0 = time.time()
        if wake:
            self._adb("-s", serial, "shell", "input", "keyevent", "224", timeout=5.0)
            time.sleep(0.25)
        code, raw, err = self._adb(
            "-s",
            serial,
            "exec-out",
            "screencap",
            "-p",
            timeout=float(config.SCREEN_CAPTURE_TIMEOUT),
        )
        if code == 124:
            return {
                "ok": False,
                "serial": serial,
                "error": "screencap timeout",
                "duration_ms": int((time.time() - t0) * 1000),
            }
        if code != 0 or not raw:
            return {
                "ok": False,
                "serial": serial,
                "error": (err or "screencap failed")[:200],
                "duration_ms": int((time.time() - t0) * 1000),
            }
        # Some Windows adb builds CRLF-corrupt PNG; repair common case
        if not raw.startswith(_PNG_MAGIC) and b"\r\n" in raw[:64]:
            raw = raw.replace(b"\r\n", b"\n")
        if not raw.startswith(_PNG_MAGIC) or len(raw) < 800:
            return {
                "ok": False,
                "serial": serial,
                "error": "invalid PNG from screencap",
                "duration_ms": int((time.time() - t0) * 1000),
            }
        paths = self.paths(serial)
        try:
            self._atomic_write(paths["full"], raw)
        except Exception as e:
            return {
                "ok": False,
                "serial": serial,
                "error": "write full failed: %s" % e,
                "duration_ms": int((time.time() - t0) * 1000),
            }
        tok, terr = self._make_thumb(raw, paths["thumb"])
        meta = {
            "serial": serial,
            "captured_at": _now_iso(),
            "bytes": len(raw),
            "ok": bool(tok),
            "error": "" if tok else terr,
            "duration_ms": int((time.time() - t0) * 1000),
            "farm_was_active": bool(self._farm_active_fn()),
        }
        self._write_meta(serial, meta)
        if not tok:
            return {
                "ok": False,
                "serial": serial,
                "error": terr,
                "duration_ms": meta["duration_ms"],
                "mtime": int(time.time()),
            }
        mtime = int(paths["thumb"].stat().st_mtime)
        return {
            "ok": True,
            "serial": serial,
            "error": "",
            "duration_ms": meta["duration_ms"],
            "mtime": mtime,
            "captured_at": meta["captured_at"],
        }

    def _worker_loop(self) -> None:
        while True:
            try:
                farm_on = bool(self._farm_active_fn())
            except Exception:
                farm_on = False
            # When farm running, only one worker should process (lock around busy set size)
            with self._lock:
                active_n = len(self._busy_serials)
            if farm_on and active_n >= 1:
                time.sleep(0.15)
                continue
            if (not farm_on) and active_n >= 2:
                time.sleep(0.08)
                continue
            try:
                prio, _seq, serial, opts = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            track = bool(opts.get("track_job", True))
            if self._cancel.is_set() and prio >= 5:
                if track:
                    with self._lock:
                        if self._job.get("active"):
                            self._job["done"] = int(self._job.get("done") or 0) + 1
                            self._job["fail"] = int(self._job.get("fail") or 0) + 1
                self._q.task_done()
                continue
            serial = safe_serial(serial)
            if not serial:
                self._q.task_done()
                continue
            with self._lock:
                if serial in self._busy_serials:
                    self._q.task_done()
                    continue
                self._busy_serials.add(serial)
            self._emit("phone.started", {"serial": serial})
            try:
                result = self.capture_one(serial, wake=bool(opts.get("wake")))
            except Exception as e:
                result = {"ok": False, "serial": serial, "error": "%s" % e}
            with self._lock:
                self._busy_serials.discard(serial)
                if track and self._job.get("active"):
                    self._job["done"] = int(self._job.get("done") or 0) + 1
                    if result.get("ok"):
                        self._job["ok"] = int(self._job.get("ok") or 0) + 1
                    else:
                        self._job["fail"] = int(self._job.get("fail") or 0) + 1
                    results = list(self._job.get("results") or [])
                    results.append(result)
                    self._job["results"] = results[-200:]
                    if self._job["done"] >= self._job.get("total", 0):
                        self._job["active"] = False
                        self._job["message"] = "Updated %d · failed %d" % (
                            self._job.get("ok") or 0,
                            self._job.get("fail") or 0,
                        )
                        self._emit("job.done", dict(self._job))
                    else:
                        done = int(self._job["done"])
                        fail = int(self._job.get("fail") or 0)
                        if done >= 4 and fail / float(done) >= 0.3:
                            self._cancel.set()
                            self._job["active"] = False
                            self._job["message"] = (
                                "Paused: ADB struggling (%d fails). Fix devices, then Update stale."
                                % fail
                            )
                            self._emit("job.paused", dict(self._job))
            self._emit("phone.done" if result.get("ok") else "phone.fail", result)
            with self._lock:
                job_active = bool(self._job.get("active"))
            if track and job_active:
                self._emit("job.progress", self.job_status())
            self._q.task_done()

    def enqueue(
        self, serials: List[str], mode: str = "selected", wake: bool = False, priority: int = 5
    ) -> Dict[str, Any]:
        serials = [safe_serial(s) for s in serials]
        serials = [s for s in serials if s]
        # dedupe preserve order
        seen = set()
        uniq = []
        for s in serials:
            if s not in seen:
                seen.add(s)
                uniq.append(s)
        if not uniq:
            return {"ok": False, "error": "no phones to update"}
        with self._lock:
            if self._job.get("active"):
                return {"ok": False, "error": "an update job is already running"}
            self._cancel.clear()
            job_id = uuid.uuid4().hex[:12]
            self._job = {
                "id": job_id,
                "active": True,
                "mode": mode,
                "total": len(uniq),
                "done": 0,
                "ok": 0,
                "fail": 0,
                "results": [],
                "message": "Updating…",
            }
        for s in uniq:
            self._seq += 1
            self._q.put(
                (priority, self._seq, s, {"wake": wake, "track_job": True})
            )
        self._emit("job.started", dict(self._job))
        return {"ok": True, "job_id": self._job["id"], "total": len(uniq)}

    def enqueue_one(self, serial: str, wake: bool = False) -> Dict[str, Any]:
        serial = safe_serial(serial)
        if not serial:
            return {"ok": False, "error": "invalid serial"}
        with self._lock:
            if serial in self._busy_serials:
                return {"ok": False, "error": "already capturing", "serial": serial}
            bulk_on = bool(self._job.get("active")) and self._job.get("mode") != "one"
            if not bulk_on:
                self._cancel.clear()
                self._job = {
                    "id": "one-%s" % serial[-6:],
                    "active": True,
                    "mode": "one",
                    "total": 1,
                    "done": 0,
                    "ok": 0,
                    "fail": 0,
                    "results": [],
                    "message": "Updating %s…" % serial,
                }
                track = True
                self._emit("job.started", dict(self._job))
            else:
                track = False
        self._seq += 1
        self._q.put((1, self._seq, serial, {"wake": wake, "track_job": track}))
        return {"ok": True, "serial": serial, "queued": True}

    def start_mode(self, mode: str, serials: Optional[List[str]] = None, wake: bool = False) -> Dict[str, Any]:
        mode = (mode or "stale").strip().lower()
        snap = self.snapshot()
        phones = snap.get("phones") or []
        if mode == "all":
            targets = [p["serial"] for p in phones]
        elif mode == "selected":
            want = set(safe_serial(s) for s in (serials or []) if safe_serial(s))
            targets = [p["serial"] for p in phones if p["serial"] in want]
        else:  # stale = missing + stale
            targets = [
                p["serial"] for p in phones if p.get("band") in ("missing", "stale")
            ]
        return self.enqueue(targets, mode=mode, wake=wake, priority=5)

    def cancel(self) -> Dict[str, Any]:
        self._cancel.set()
        with self._lock:
            if self._job.get("active"):
                self._job["active"] = False
                self._job["message"] = "Cancelled"
                job = dict(self._job)
            else:
                job = dict(self._job)
        self._emit("job.cancelled", job)
        return {"ok": True, "job": job}

    def job_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._job)
