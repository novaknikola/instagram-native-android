# -*- coding: utf-8 -*-
"""Detect whether start_proxy_pool / proxy_pool.py is actually up."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config


class ProxyPoolHealth:
    """PC relay must be running before farm Start posting (device_ports + listeners)."""

    def __init__(self, state_dir: Optional[Path] = None):
        self.state_dir = Path(state_dir or config.PROXY_STATE_DIR)
        self.ports_file = self.state_dir / "device_ports.json"

    def _process_running(self) -> bool:
        if sys.platform.startswith("win"):
            cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'proxy_pool\\.py' } | "
                "Measure-Object).Count"
            )
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", cmd],
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout.strip()
                return int(out or "0") > 0
            except Exception:
                return False
        # Non-Windows preview: look for python … proxy_pool
        try:
            out = subprocess.run(
                ["pgrep", "-fl", "proxy_pool"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            return bool(out.strip())
        except Exception:
            return False

    def _load_ports(self) -> Dict[str, int]:
        mapping = {}
        src = self.ports_file
        try:
            import proxy_pool
            raw, path = proxy_pool.load_device_ports()
            if path:
                src = Path(path)
                self.ports_file = src
            for k, v in (raw or {}).items():
                try:
                    mapping[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            if mapping:
                return mapping
        except Exception:
            pass
        if not src.exists():
            return {}
        try:
            raw = json.loads(src.read_text(encoding="utf-8"))
            out = {}
            for k, v in (raw or {}).items():
                try:
                    out[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            return out
        except Exception:
            return {}

    @staticmethod
    def _port_listening(port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.35)
        try:
            sock.connect(("127.0.0.1", int(port)))
            return True
        except Exception:
            return False
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        try:
            return self._status_inner()
        except Exception as e:
            return {
                "ok": False,
                "label": "PROXY ?",
                "msg": "Proxy health check failed: %s" % e,
                "detail": "check error",
                "process": False,
                "mapped": 0,
                "listening": 0,
                "ports_file": str(self.ports_file),
            }

    def _status_inner(self) -> Dict[str, Any]:
        proc = self._process_running()
        mapping = self._load_ports()
        listening = []  # type: List[int]
        for port in mapping.values():
            if self._port_listening(port):
                listening.append(port)

        # Live listeners are the strongest signal (stale JSON alone is not enough).
        ok = bool(listening) or (proc and bool(mapping))
        # Process up with 0 phones still counts as pool daemon alive.
        if proc and not mapping:
            ok = True

        if ok:
            msg = ""
            if listening:
                detail = "%d relay port(s) listening · %d phone(s) mapped" % (
                    len(listening),
                    len(mapping),
                )
            elif proc:
                detail = "proxy_pool process up · %d phone(s) mapped" % len(mapping)
            else:
                detail = "relay ports responding"
            label = "PROXY UP"
        else:
            label = "PROXY DOWN"
            if not proc and not mapping:
                msg = (
                    "Proxy pool is not running. Double-click start_proxy_pool.bat "
                    "and leave that window open, then refresh this page."
                )
                detail = "no proxy_pool process · no device_ports.json"
            elif not proc and mapping and not listening:
                msg = (
                    "device_ports.json is stale - proxy pool listeners are not up. "
                    "Start start_proxy_pool.bat (keep the window open)."
                )
                detail = "stale port map · no listeners"
            else:
                msg = (
                    "Proxy relay is not ready for phones. Run start_proxy_pool.bat "
                    "before starting a farm batch."
                )
                detail = "proxy relay not ready"

        return {
            "ok": ok,
            "label": label,
            "msg": msg,
            "detail": detail,
            "process": proc,
            "mapped": len(mapping),
            "listening": len(listening),
            "ports_file": str(self.ports_file),
        }
