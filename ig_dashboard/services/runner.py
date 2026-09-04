# -*- coding: utf-8 -*-
"""Start / watch IG farm subprocess + per-run txt logs. Fail closed; never crash UI."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..util import ensure_dir, read_text, safe_int, iter_csv_dicts

_HANDOVER = Path(__file__).resolve().parents[2]
_START_LOCK = threading.Lock()
_RUN_LOG_RE = re.compile(r"^ig_run_\d{4}-\d{2}-\d{2}_\d{6}(?:_\d+)?\.txt$")


class FarmRunner:
    """Launches run_ig_farm / schedule; each start gets its own dated+timed .txt log."""

    def __init__(self):
        self.state_path = Path(config.RUN_STATE)
        self.legacy_log = Path(config.RUN_LOG)
        self.logs_dir = Path(getattr(config, "LOGS_DIR", config.BASE / "logs"))
        self.cwd = Path(config.BASE)
        if not (self.cwd / "run_ig_farm.py").exists():
            if (_HANDOVER / "run_ig_farm.py").exists():
                self.cwd = _HANDOVER

    @property
    def log_path(self) -> Path:
        """Current run log (from state), else newest archive, else legacy file."""
        st = self._read_state()
        lp = (st.get("log_path") or "").strip()
        if lp:
            p = Path(lp)
            if p.exists():
                return p
        newest = self._newest_run_log()
        if newest:
            return newest
        return self.legacy_log

    def _read_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(read_text(self.state_path, "{}") or "{}")
        except Exception:
            return {}

    def _write_state(self, data: Dict[str, Any]) -> None:
        try:
            ensure_dir(self.state_path.parent)
            self.state_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _newest_run_log(self) -> Optional[Path]:
        try:
            ensure_dir(self.logs_dir)
            files = [
                p
                for p in self.logs_dir.iterdir()
                if p.is_file() and _RUN_LOG_RE.match(p.name)
            ]
            if not files:
                return None
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            return files[0]
        except Exception:
            return None

    def list_run_logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Newest-first list of per-run log files."""
        out: List[Dict[str, Any]] = []
        try:
            ensure_dir(self.logs_dir)
            files = [
                p
                for p in self.logs_dir.iterdir()
                if p.is_file() and _RUN_LOG_RE.match(p.name)
            ]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            current = str(self.log_path.resolve()) if self.log_path.exists() else ""
            for p in files[: max(1, min(limit, 100))]:
                try:
                    st = p.stat()
                    out.append(
                        {
                            "name": p.name,
                            "path": str(p),
                            "size": st.st_size,
                            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(
                                timespec="seconds"
                            ),
                            "current": str(p.resolve()) == current,
                        }
                    )
                except Exception:
                    continue
        except Exception:
            pass
        return out

    def resolve_log_file(self, name: str = "") -> Optional[Path]:
        """Safe resolve: blank = current; else basename under logs/ matching pattern."""
        name = (name or "").strip()
        if not name:
            p = self.log_path
            return p if p.exists() else None
        base = Path(name).name
        if not _RUN_LOG_RE.match(base):
            return None
        p = self.logs_dir / base
        return p if p.is_file() else None

    def _new_run_log_path(self) -> Path:
        ensure_dir(self.logs_dir)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = self.logs_dir / ("ig_run_%s.txt" % stamp)
        # collide same second: add suffix
        n = 1
        while path.exists():
            path = self.logs_dir / ("ig_run_%s_%d.txt" % (stamp, n))
            n += 1
        return path

    def _write_latest_pointer(self, path: Path) -> None:
        """Easy find: logs/LATEST.txt names the active/current run file."""
        try:
            ensure_dir(self.logs_dir)
            (self.logs_dir / "LATEST.txt").write_text(
                "%s\n%s\n" % (path.name, path), encoding="utf-8"
            )
        except Exception:
            pass

    def _open_run_log(self, kind: str, cmd: List[str]) -> Tuple[Optional[Any], Path, str]:
        """Create a fresh per-run .txt and write header. Returns (fh, path, err)."""
        path = self._new_run_log_path()
        try:
            fh = open(path, "a", encoding="utf-8", buffering=1)
        except Exception as e:
            return None, path, "cannot open run log: %s" % e
        try:
            now = datetime.now().isoformat(timespec="seconds")
            fh.write("===== IG RUN START %s =====\n" % now)
            fh.write("kind: %s\n" % kind)
            fh.write("file: %s\n" % path.name)
            fh.write("command: %s\n\n" % " ".join(cmd))
            fh.flush()
        except Exception:
            pass
            self._write_latest_pointer(path)
        # Keep legacy path as a one-line pointer so old habits still find something
        try:
            self.legacy_log.write_text(
                "Current run log:\n%s\nError shots:\n%s\n(See also folder: %s)\n"
                % (path, path.parent / path.stem, self.logs_dir),
                encoding="utf-8",
            )
        except Exception:
            pass
        # Ensure error-shot sibling dir exists before child processes start
        try:
            import ig_error_shots as _es

            _es.ensure_run_shot_dir(path)
        except Exception:
            try:
                (path.parent / path.stem).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        return fh, path, ""

    def _prep_phone_log_dir(self, env: Dict[str, str], log_path: Path) -> str:
        """N phone .txt files next to the conductor run log."""
        phone_dir = log_path.parent / (log_path.stem + "_phones")
        try:
            phone_dir.mkdir(parents=True, exist_ok=True)
            env["IG_PHONE_LOG_DIR"] = str(phone_dir)
        except Exception:
            return ""
        env["IG_RUN_LOG"] = str(log_path)
        env.setdefault("IG_STAGGER", "2")
        return str(phone_dir)

    def is_process_active(self) -> bool:
        """True if run_ig_farm / run_ig_device is in the process list."""
        try:
            if sys.platform.startswith("win"):
                cmd = (
                    "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Where-Object { $_.CommandLine -match 'run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler' } | "
                    "Measure-Object).Count"
                )
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    text=True,
                    timeout=12,
                    errors="replace",
                ).stdout.strip()
                return safe_int(out, 0) > 0
            st = self._read_state()
            pid = st.get("pid")
            if not pid:
                return False
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def status(self) -> Dict[str, Any]:
        try:
            active = self.is_process_active()
            st = self._read_state()
            lp = self.log_path
            shot = (st.get("error_shot_dir") or "").strip()
            if not shot and lp:
                cand = Path(lp).parent / Path(lp).stem
                if cand.is_dir():
                    shot = str(cand)
            return {
                "active": active,
                "label": "RUNNING" if active else "IDLE",
                "started_at": st.get("started_at", ""),
                "command": st.get("command", []),
                "pid": st.get("pid"),
                "log_path": str(lp),
                "log_name": lp.name if lp else "",
                "logs_dir": str(self.logs_dir),
                "error_shot_dir": shot,
                "phone_log_dir": (st.get("phone_log_dir") or ""),
                "recent_logs": self.list_run_logs(12),
            }
        except Exception:
            return {
                "active": False,
                "label": "IDLE",
                "started_at": "",
                "command": [],
                "pid": None,
                "log_path": str(self.log_path),
                "log_name": self.log_path.name,
                "logs_dir": str(self.logs_dir),
                "error_shot_dir": "",
                "recent_logs": [],
            }

    def start(
        self,
        country: str = "us",
        per_device: int = 1,
        model: str = "",
        devices: str = "",
        allow_unproven: bool = False,
        parallel: bool = True,
        limit: int = 0,
        farm_format: str = "reel",
        max_inflight: int = 0,
        e2e: bool = False,
        story_link: str = "",
        highlight: str = "",
    ) -> Tuple[bool, str]:
        if not _START_LOCK.acquire(blocking=False):
            return False, "Another start is already in progress - wait a moment"
        try:
            return self._start_locked(
                country, per_device, model, devices, allow_unproven, parallel,
                limit, farm_format, max_inflight, e2e, story_link, highlight,
            )
        finally:
            try:
                _START_LOCK.release()
            except Exception:
                pass

    def _start_locked(
        self,
        country: str,
        per_device: int,
        model: str,
        devices: str,
        allow_unproven: bool,
        parallel: bool,
        limit: int,
        farm_format: str = "reel",
        max_inflight: int = 0,
        e2e: bool = False,
        story_link: str = "",
        highlight: str = "",
    ) -> Tuple[bool, str]:
        try:
            if self.is_process_active():
                return False, "A farm run is already active"

            try:
                from .proxy import ProxyPoolHealth

                proxy = ProxyPoolHealth().status()
                if not proxy.get("ok"):
                    return False, proxy.get("msg") or (
                        "Proxy pool is not running - start start_proxy_pool.bat first."
                    )
            except Exception as e:
                return False, "Could not verify proxy pool: %s" % e

            country = (country or "us").strip().lower()
            if country not in ("us", "uk", "gb"):
                country = "us"
            per_device = 1
            limit = safe_int(limit, 0, lo=0, hi=500)
            model = (model or "").strip().lower()
            devices = (devices or "").strip()

            script = self.cwd / "run_ig_farm.py"
            if not script.exists():
                return False, "run_ig_farm.py not found in %s" % self.cwd

            if not config.ACCOUNTS_CSV.exists():
                return False, "Instagram_farm_accounts.csv missing - add accounts first"

            cmd = [
                sys.executable,
                "-u",
                str(script),
                "--country",
                country,
                "--per-device",
                str(per_device),
            ]
            if model and model in config.MODELS:
                cmd += ["--model", model]
            if devices:
                clean = [
                    s.strip()
                    for s in devices.replace(" ", "").split(",")
                    if s.strip()
                ]
                if clean:
                    cmd += ["--devices", ",".join(clean)]
            if allow_unproven:
                cmd.append("--allow-unproven")
            fmt = (farm_format or "reel").strip().lower()
            if e2e:
                cmd += ["--e2e"]
            elif fmt in ("feed", "story", "carousel", "reel"):
                cmd += ["--format", fmt]
            story_link = (story_link or "").strip()
            highlight = (highlight or "").strip()
            if story_link:
                cmd += ["--story-link", story_link]
            if highlight:
                cmd += ["--highlight", highlight]
            if parallel:
                cmd.append("--parallel")
            else:
                cmd.append("--serial")
            max_inflight = safe_int(max_inflight, 0, lo=0, hi=20)
            if max_inflight > 0:
                cmd += ["--max-inflight", str(max_inflight)]
            if limit > 0:
                cmd += ["--limit", str(limit)]

            log_fh, log_path, err = self._open_run_log("farm", cmd)
            if log_fh is None:
                return False, err or "cannot open run log"

            env = os.environ.copy()
            shot_dir = log_path.parent / log_path.stem
            try:
                shot_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            env["IG_ERROR_SHOT_DIR"] = str(shot_dir)
            env["IG_RUN_LOG"] = str(log_path)
            phone_dir = self._prep_phone_log_dir(env, log_path)

            kwargs = {
                "cwd": str(self.cwd),
                "stdout": log_fh,
                "stderr": subprocess.STDOUT,
                "env": env,
            }  # type: Dict[str, Any]
            if sys.platform.startswith("win"):
                try:
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore
                except Exception:
                    pass
            else:
                kwargs["start_new_session"] = True

            try:
                proc = subprocess.Popen(cmd, **kwargs)
            except Exception as e:
                try:
                    log_fh.close()
                except Exception:
                    pass
                return False, "failed to launch: %s" % e

            started = datetime.now().isoformat(timespec="seconds")
            shot_dir = log_path.parent / log_path.stem
            self._write_state(
                {
                    "pid": proc.pid,
                    "started_at": started,
                    "command": cmd,
                    "log_path": str(log_path),
                    "log_name": log_path.name,
                    "error_shot_dir": str(shot_dir),
                    "kind": "farm",
                    "phone_log_dir": phone_dir,
                }
            )
            return True, "started pid=%s log=%s shots=%s phones=%s" % (
                proc.pid, log_path.name, shot_dir.name, Path(phone_dir).name if phone_dir else "-"
            )
        except Exception as e:
            return False, "start failed: %s" % e

    def start_schedule(self, dry: bool = False) -> Tuple[bool, str]:
        """Launch run_ig_schedule.py (due Sheet/CSV rows)."""
        if not _START_LOCK.acquire(blocking=False):
            return False, "Another start is already in progress - wait a moment"
        try:
            if self.is_process_active():
                return False, "A farm run is already active"
            try:
                from .proxy import ProxyPoolHealth

                proxy = ProxyPoolHealth().status()
                if not proxy.get("ok"):
                    return False, proxy.get("msg") or (
                        "Proxy pool is not running - start start_proxy_pool.bat first."
                    )
            except Exception as e:
                return False, "Could not verify proxy pool: %s" % e

            script = self.cwd / "run_ig_schedule.py"
            if not script.exists():
                alt = _HANDOVER / "run_ig_schedule.py"
                if alt.exists():
                    script = alt
                else:
                    return False, "run_ig_schedule.py not found in %s" % self.cwd

            cmd = [sys.executable, "-u", str(script)]
            if dry:
                cmd.append("--dry")
            max_jobs = (os.environ.get("IG_SCHEDULE_MAX") or "0").strip()
            cmd.extend(["--max-jobs", max_jobs])

            log_fh, log_path, err = self._open_run_log("schedule", cmd)
            if log_fh is None:
                return False, err or "cannot open run log"

            env = os.environ.copy()
            try:
                import ig_scheduler as sch  # type: ignore

                sid = (sch.schedule_sheet_id() or "").strip()
            except Exception:
                sid = (os.environ.get("IG_SCHEDULE_SHEET") or "").strip() or getattr(
                    config, "SCHEDULE_SHEET", ""
                )
            if not sid:
                path = Path(getattr(config, "SCHEDULE_SHEET_FILE", self.cwd / "ig_schedule_sheet.txt"))
                if path.is_file():
                    try:
                        for ln in path.read_text(encoding="utf-8").splitlines():
                            s = ln.split("#")[0].strip()
                            if s:
                                sid = s
                                break
                    except Exception:
                        pass
            if sid:
                env["IG_SCHEDULE_SHEET"] = sid
            shot_dir = log_path.parent / log_path.stem
            try:
                shot_dir.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            env["IG_ERROR_SHOT_DIR"] = str(shot_dir)
            env["IG_RUN_LOG"] = str(log_path)
            phone_dir = self._prep_phone_log_dir(env, log_path)

            kwargs = {
                "cwd": str(self.cwd),
                "stdout": log_fh,
                "stderr": subprocess.STDOUT,
                "env": env,
            }  # type: Dict[str, Any]
            if sys.platform.startswith("win"):
                try:
                    kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore
                except Exception:
                    pass
            else:
                kwargs["start_new_session"] = True

            try:
                proc = subprocess.Popen(cmd, **kwargs)
            except Exception as e:
                try:
                    log_fh.close()
                except Exception:
                    pass
                return False, "failed to launch schedule: %s" % e

            started = datetime.now().isoformat(timespec="seconds")
            shot_dir = log_path.parent / log_path.stem
            self._write_state(
                {
                    "pid": proc.pid,
                    "started_at": started,
                    "command": cmd,
                    "log_path": str(log_path),
                    "log_name": log_path.name,
                    "error_shot_dir": str(shot_dir),
                    "kind": "schedule",
                    "phone_log_dir": phone_dir,
                }
            )
            return True, "schedule started pid=%s log=%s phones=%d" % (
                proc.pid, log_path.name, len(list(Path(phone_dir).glob("*.txt"))) if phone_dir else 0
            )
        except Exception as e:
            return False, "schedule start failed: %s" % e
        finally:
            try:
                _START_LOCK.release()
            except Exception:
                pass

    def stop(self) -> Tuple[bool, str]:
        """Kill active IG farm / schedule / device processes."""
        killed = 0
        try:
            if sys.platform.startswith("win"):
                ps = (
                    "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    "Where-Object { $_.CommandLine -match "
                    "'run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler' }; "
                    "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                    "-ErrorAction SilentlyContinue }; ($p | Measure-Object).Count } else { 0 }"
                )
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    errors="replace",
                ).stdout.strip()
                killed = safe_int(out.splitlines()[-1] if out else "0", 0)
            else:
                st = self._read_state()
                pid = st.get("pid")
                if pid:
                    try:
                        os.kill(int(pid), 15)
                        killed = 1
                    except Exception:
                        pass
                subprocess.run(
                    ["pkill", "-f", "run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler"],
                    capture_output=True,
                    timeout=10,
                )
            st = self._read_state()
            log_path = Path(st.get("log_path") or "") if st.get("log_path") else self.log_path
            stopped_at = datetime.now().isoformat(timespec="seconds")
            self._write_state(
                {
                    "pid": None,
                    "started_at": st.get("started_at") or "",
                    "command": st.get("command") or [],
                    "log_path": str(log_path) if log_path else st.get("log_path", ""),
                    "log_name": Path(str(log_path)).name if log_path else st.get("log_name", ""),
                    "kind": st.get("kind") or "",
                    "phone_log_dir": st.get("phone_log_dir") or "",
                    "stopped_at": stopped_at,
                }
            )
            try:
                if log_path and Path(log_path).parent.exists():
                    with open(log_path, "a", encoding="utf-8") as fh:
                        fh.write(
                            "\n===== IG RUN STOP %s killed~%s =====\n"
                            % (stopped_at, killed)
                        )
            except Exception:
                pass
            if killed or not self.is_process_active():
                return True, "Stopped farm processes (killed≈%s)" % killed
            return False, "Stop issued but a process may still be active - check Task Manager"
        except Exception as e:
            return False, "stop failed: %s" % e

    def tail_log(self, lines: int = 120) -> str:
        lines = safe_int(lines, 120, lo=20, hi=500)
        path = self.log_path
        if not path.exists():
            return "(no console log yet - start a run to capture output)"
        try:
            text = read_text(path)
            parts = text.splitlines()
            return "\n".join(parts[-lines:])
        except Exception as e:
            return "(log read error: %s)" % e

    def _phone_log_dir(self) -> Optional[Path]:
        st = self._read_state()
        d = (st.get("phone_log_dir") or "").strip()
        if d:
            p = Path(d)
            if p.is_dir():
                return p
        try:
            lp = self.log_path
            if lp:
                cand = lp.parent / (lp.stem + "_phones")
                if cand.is_dir():
                    return cand
        except Exception:
            pass
        return None

    def _phone_log_file(self, serial: str) -> Optional[Path]:
        d = self._phone_log_dir()
        serial = (serial or "").strip()
        if not d or not serial:
            return None
        p = d / ("%s.txt" % serial)
        return p if p.is_file() else None

    def phone_logs_snapshot(self, lines: int = 80) -> Dict[str, Any]:
        """One tail per phone .txt for the live N-monitor grid."""
        lines = safe_int(lines, 80, lo=20, hi=400)
        d = self._phone_log_dir()
        phones: List[Dict[str, Any]] = []
        if d:
            files = sorted(d.glob("*.txt"), key=lambda p: p.name.lower())
            for p in files:
                serial = p.stem
                try:
                    parts = read_text(p).splitlines()
                    tail = parts[-lines:]
                    text = "\n".join(tail) if tail else "(waiting for this phone…)"
                except Exception as e:
                    text = "(read error: %s)" % e
                    parts = []
                phones.append(
                    {
                        "serial": serial,
                        "serial_short": serial[-10:] if len(serial) > 10 else serial,
                        "path": str(p),
                        "lines": len(parts) if parts else 0,
                        "text": text,
                    }
                )
        return {
            "ok": True,
            "dir": str(d) if d else "",
            "count": len(phones),
            "phones": phones,
        }

    def device_log(self, serial: str, lines: int = 250) -> Dict[str, Any]:
        """
        Latest automation log lines for one phone serial.
        Prefers the newest run log that mentions the serial; falls back to
        scanning a few recent archives. Soft-fail.
        """
        serial = (serial or "").strip()
        lines = safe_int(lines, 250, lo=40, hi=2000)
        out: Dict[str, Any] = {
            "ok": False,
            "serial": serial,
            "log_name": "",
            "log_path": "",
            "match_count": 0,
            "text": "",
            "error": "",
        }
        if not serial or len(serial) < 4:
            out["error"] = "bad serial"
            return out
        dedicated = self._phone_log_file(serial)
        if dedicated and dedicated.is_file():
            try:
                parts = read_text(dedicated).splitlines()
                tail = parts[-lines:]
                out.update(
                    {
                        "ok": True,
                        "log_name": dedicated.name,
                        "log_path": str(dedicated),
                        "match_count": len(parts),
                        "text": "\n".join(tail) if tail else "(empty)",
                    }
                )
                return out
            except Exception as e:
                out["error"] = str(e)
        # Candidate files: current pointer first, then newest archives
        candidates: List[Path] = []
        try:
            cur = self.log_path
            if cur and cur.exists():
                candidates.append(cur)
        except Exception:
            pass
        try:
            for row in self.list_run_logs(12):
                p = Path(row.get("path") or "")
                if p.is_file() and p not in candidates:
                    candidates.append(p)
        except Exception:
            pass
        needle = serial
        chosen: Optional[Path] = None
        matched: List[str] = []
        for path in candidates:
            try:
                parts = read_text(path).splitlines()
            except Exception:
                continue
            hits = [ln for ln in parts if needle in ln]
            if hits:
                chosen = path
                matched = hits
                break
        if not chosen:
            out["ok"] = True
            out["text"] = (
                "(no lines for serial %s in recent run logs — "
                "start a farm run that touches this phone)"
                % serial
            )
            return out
        tail = matched[-lines:]
        out.update(
            {
                "ok": True,
                "log_name": chosen.name,
                "log_path": str(chosen),
                "match_count": len(matched),
                "text": "\n".join(tail) if tail else "(empty)",
            }
        )
        return out

    def fleet_board(self) -> Dict[str, Any]:
        """Per-phone status for parallel runs: CSV rows + log phase hints."""
        st = self.status()
        started_at = (st.get("started_at") or "").strip()
        active = bool(st.get("active"))
        log_text = ""
        try:
            if self.log_path.exists():
                log_text = read_text(self.log_path)
        except Exception:
            pass

        planned: Dict[str, Dict[str, Any]] = {}
        for ln in log_text.splitlines():
            m = re.match(
                r"^\s+([0-9a-f]{16,})\s+\(unique=\d+\):\s*(.+)$", ln.strip(), re.I
            )
            if m:
                planned.setdefault(m.group(1), {"accounts": []})
                continue
            m2 = re.match(r"^\s+([0-9a-f]{16,}):\s+\(no unique", ln.strip(), re.I)
            if m2:
                planned.setdefault(m2.group(1), {"accounts": []})

        for ln in log_text.splitlines():
            m = re.search(
                r"\[farm\] launch \d+/\d+ serial=([0-9a-f]{16,})", ln, re.I
            )
            if m:
                planned.setdefault(m.group(1), {"accounts": []})
                planned[m.group(1)]["launched"] = True
            m3 = re.search(
                r"\[sched\] LAUNCH \d+/\d+ serial=([0-9a-f]{16,})", ln, re.I
            )
            if m3:
                planned.setdefault(m3.group(1), {"accounts": []})
                planned[m3.group(1)]["launched"] = True
            m2 = re.search(r"--- serial device ([0-9a-f]{16,})", ln, re.I)
            if m2:
                planned.setdefault(m2.group(1), {"accounts": []})

        # Latest CSV row per serial (prefer rows after run start)
        csv_by_serial: Dict[str, Dict[str, str]] = {}
        try:
            csv_path = config.RESULTS_CSV
            if csv_path.exists():
                rows = list(iter_csv_dicts(csv_path))
                for r in reversed(rows):
                    serial = (r.get("serial") or "").strip()
                    if not serial or serial in csv_by_serial:
                        continue
                    t = (r.get("time") or "").strip()
                    if started_at and t:
                        ts = t.replace("T", " ")[:19]
                        ss = started_at.replace("T", " ")[:19]
                        if ts < ss:
                            continue
                    csv_by_serial[serial] = r
        except Exception:
            pass

        try:
            pdir = self._phone_log_dir()
            if pdir:
                for pf in pdir.glob("*.txt"):
                    planned.setdefault(pf.stem, {"accounts": []})
                    planned[pf.stem]["launched"] = True
        except Exception:
            pass

        # Phase from log tail per serial
        def _phase(serial: str) -> str:
            text = log_text
            pf = self._phone_log_file(serial)
            if pf:
                try:
                    text = read_text(pf)
                except Exception:
                    pass
            hits = text.splitlines()
            if not hits:
                if active and serial in planned:
                    return "queued"
                return "idle"
            tail = hits[-1]
            tl = tail.lower()
            blob = "\n".join(hits[-12:]).lower()
            if "post_done" in blob:
                return "done"
            if any(x in blob for x in ("captcha", "proxy_dead", "login_rejected", "fail")):
                if "post_done" not in blob:
                    return "problem" if "login=" in blob or "[fail]" in blob else "running"
            if "login=" in blob or "format=" in blob or "[login]" in blob or "[post]" in blob:
                return "running"
            if "[farm] launch" in tl or "[sched] launch" in tl:
                return "starting"
            if active and serial in planned:
                return "running"
            return "idle"

        out_rows: List[Dict[str, Any]] = []
        serials = sorted(set(planned.keys()) | set(csv_by_serial.keys()))
        for serial in serials:
            csv_r = csv_by_serial.get(serial) or {}
            clone = csv_r.get("clone") or ""
            if clone.startswith("com.instagram."):
                clone = clone.split(".")[-1]
            phase = _phase(serial)
            login = csv_r.get("login") or ""
            post = csv_r.get("post") or ""
            if post == "POST_DONE":
                phase = "done"
            elif login in ("CAPTCHA", "HUMAN_BLOCKED", "CONTACT_VERIFY"):
                phase = "problem"
            elif login in ("PROXY_DEAD", "LOGIN_META_ERROR", "UNKNOWN_STUCK"):
                phase = "problem"
            out_rows.append({
                "serial": serial,
                "serial_short": serial[-8:] if len(serial) > 8 else serial,
                "username": csv_r.get("username") or "",
                "clone": clone,
                "format": csv_r.get("format") or "feed",
                "login": login,
                "post": post,
                "phase": phase,
                "time": csv_r.get("time") or "",
            })

        run_mode = "PARALLEL" if (
            "run mode: PARALLEL" in log_text or "[sched] PARALLEL phones=" in log_text
        ) else (
            "SERIAL" if "run mode: SERIAL" in log_text else ""
        )
        return {
            "ok": True,
            "active": active,
            "started_at": started_at,
            "run_mode": run_mode,
            "phones": out_rows,
            "planned_count": len(planned),
        }

    def clear_log(self) -> None:
        """Do not wipe archives. Only clears legacy pointer file."""
        try:
            self.legacy_log.write_text("", encoding="utf-8")
        except Exception:
            pass
