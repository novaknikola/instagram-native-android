# -*- coding: utf-8 -*-
"""ADB device + IG clone inventory. Soft-fail per phone; short TTL cache to keep Console light."""
from __future__ import annotations

from farm_root import ROOT
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

SHARED_SUFFIXES = {"android", "androie", "androif"}
CLONE_CAP = 1


def _even_plan(haves, unused, cap=CLONE_CAP):
    """Same rule as ig_clone_even.even_plan (dashboard must run without extra copies)."""
    n = len(haves or [])
    try:
        cap = max(1, int(cap))
        left = max(0, int(unused or 0))
    except (TypeError, ValueError):
        cap, left = CLONE_CAP, 0
    targets = [max(0, int(h or 0)) for h in (haves or [])]
    while left > 0 and n:
        elig = [i for i in range(n) if targets[i] < cap]
        if not elig:
            break
        i = min(elig, key=lambda j: (targets[j], j))
        targets[i] += 1
        left -= 1
    adds = [max(0, t - h) for t, h in zip(targets, [max(0, int(x or 0)) for x in (haves or [])])]
    lo = min(targets) if targets else 0
    hi = max(targets) if targets else 0
    return {
        "targets": targets,
        "adds": adds,
        "target_lo": lo,
        "target_hi": hi,
        "even_each": lo if lo == hi else "%s–%s" % (lo, hi),
        "balanced_after": (hi - lo) <= 1 if targets else True,
    }

# Cache inventory so Overview/Run do not hammer `pm list` on every navigation.
_INV_CACHE: Tuple[float, List["DeviceInfo"], str] = (0.0, [], "")
_INV_TTL = 45.0
_SERIAL_CACHE: Tuple[float, List[str]] = (0.0, [])
_SERIAL_TTL = 8.0


def _sh(*args: str, timeout: float = 8.0) -> str:
    try:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        ).stdout or ""
    except Exception:
        return ""


@dataclass
class DeviceInfo:
    serial: str
    clones: List[str] = field(default_factory=list)
    unique: List[str] = field(default_factory=list)
    shared: List[str] = field(default_factory=list)
    open_ok: List[str] = field(default_factory=list)
    ignored: List[str] = field(default_factory=list)
    error: str = ""

    @property
    def ready(self) -> bool:
        if self.error:
            return False
        if self.ignored and not self.open_ok:
            return False
        return bool(self.open_ok or self.unique)


def _allow_pairs():
    try:
        import ig_clone_allow
        if ig_clone_allow.active():
            return True, ig_clone_allow.pair_set()
    except Exception:
        pass
    return False, set()


class DeviceFleet:
    """Live phone list via adb (Windows farm)."""

    def adb_available(self) -> bool:
        return shutil.which("adb") is not None

    def list_serials(self, force: bool = False) -> List[str]:
        global _SERIAL_CACHE
        now = time.time()
        ts, cached = _SERIAL_CACHE
        if not force and cached and (now - ts) < _SERIAL_TTL:
            return list(cached)
        if not self.adb_available():
            _SERIAL_CACHE = (now, [])
            return []
        out = _sh("adb", "devices", timeout=8.0)
        serials = []
        for ln in (out or "").splitlines()[1:]:
            if "\tdevice" in ln:
                serials.append(ln.split()[0].strip())
        _SERIAL_CACHE = (now, serials)
        return list(serials)

    def clones_for(self, serial: str) -> List[str]:
        import ig_pkg
        out = _sh("adb", "-s", serial, "shell", "pm", "list", "packages", timeout=8.0)
        return ig_pkg.ig_pkgs_from_pm_list(out)

    def inventory(self, force: bool = False) -> List[DeviceInfo]:
        global _INV_CACHE
        now = time.time()
        ts, cached, _err = _INV_CACHE
        if not force and cached and (now - ts) < _INV_TTL:
            return list(cached)
        devices: List[DeviceInfo] = []
        try:
            serials = self.list_serials(force=force)
        except Exception:
            _INV_CACHE = (now, [], "list failed")
            return devices
        raw = []
        for serial in serials:
            try:
                raw.append((serial, self.clones_for(serial), ""))
            except Exception as e:
                raw.append((serial, [], "%s" % e))
        from collections import Counter
        cnt = Counter(p for _s, cs, err in raw if not err for p in cs)
        for serial, clones, err in raw:
            if err:
                devices.append(DeviceInfo(serial=serial, error=err))
                continue
            unique, shared, ignored = [], [], []
            for c in clones:
                suf = c.rsplit(".", 1)[-1]
                if suf in SHARED_SUFFIXES or c == "com.instagram.android" or cnt[c] > 1:
                    shared.append(c)
                else:
                    unique.append(c)
            devices.append(
                DeviceInfo(
                    serial=serial,
                    clones=clones,
                    unique=unique,
                    shared=shared,
                    open_ok=list(unique),
                    ignored=ignored,
                )
            )
        _INV_CACHE = (now, devices, "")
        return list(devices)

    def summary(self, force: bool = False) -> Dict[str, Any]:
        try:
            inv = self.inventory(force=force)
            unique_n = sum(len(d.unique) for d in inv)
            open_ok_n = sum(len(d.open_ok) for d in inv)
            ignored_n = sum(len(d.ignored) for d in inv)
            allow_on, _ = False, set()
            counts = [len(d.open_ok if allow_on else d.unique) for d in inv]
            ready_n = sum(
                1 for d in inv if (d.open_ok if allow_on else d.unique) and not d.error
            )
            return {
                "phones": len(inv),
                "ready": ready_n,
                "unique_clones": unique_n,
                "open_ok_clones": open_ok_n,
                "ignored_clones": ignored_n,
                "allowlist_on": allow_on,
                "unique_lo": min(counts) if counts else 0,
                "unique_hi": max(counts) if counts else 0,
                "devices": inv,
                "adb_ok": self.adb_available(),
                "error": "" if self.adb_available() else "adb not on PATH",
                "cached": (time.time() - _INV_CACHE[0]) < _INV_TTL and not force,
            }
        except Exception as e:
            return {
                "phones": 0,
                "ready": 0,
                "unique_clones": 0,
                "open_ok_clones": 0,
                "ignored_clones": 0,
                "allowlist_on": False,
                "unique_lo": 0,
                "unique_hi": 0,
                "devices": [],
                "adb_ok": False,
                "error": "%s: %s" % (type(e).__name__, e),
                "cached": False,
            }

    def _apk_dirs(self) -> List[str]:
        base = os.environ.get("IG_FARM_BASE", ROOT)
        return [
            os.path.join(base, "nomix_api", "downloaded_ig"),
            os.path.join(base, "nomix_api", "downloaded_clones"),
            r"C:\threads-android\nomix_api\downloaded_ig",
            r"C:\threads-android\nomix_api\downloaded_clones",
        ]

    def count_apks_on_disk(self) -> Dict[str, Any]:
        """Full APK inventory on the farm PC (not installed)."""
        folders = []
        total_n, total_b = 0, 0
        for d in self._apk_dirs():
            n, b = 0, 0
            if os.path.isdir(d):
                try:
                    names = os.listdir(d)
                except OSError:
                    names = []
                for f in names:
                    if not f.lower().endswith(".apk"):
                        continue
                    path = os.path.join(d, f)
                    try:
                        sz = os.path.getsize(path)
                    except OSError:
                        continue
                    if sz < 100_000_000:
                        continue
                    n += 1
                    b += sz
            folders.append({"path": d, "apks": n, "bytes": b})
            total_n += n
            total_b += b
        return {"apks": total_n, "bytes": total_b, "folders": folders}

    def clone_map(self, force: bool = False, cap: int = CLONE_CAP) -> Dict[str, Any]:
        """Per-phone unique counts + even-spread plan for Overview modal."""
        inv = self.inventory(force=force)
        disk = self.count_apks_on_disk()
        haves = [len(d.unique) for d in inv]
        phones = len(inv)
        unique_n = sum(haves)
        apk_n = int(disk.get("apks") or 0)
        # keep-apk: disk APKs include clones already on phones. Extra ≈ disk − on phones.
        unused_est = max(0, apk_n - unique_n)
        plan = _even_plan(haves, unused_est, cap=cap)
        by_serial_target = {}
        for d, t in zip(inv, plan["targets"]):
            by_serial_target[d.serial] = t
        max_bar = max([cap] + haves + list(plan["targets"] or [0])) or 1
        allow_on, _ = _allow_pairs()
        devices = []
        for d in sorted(inv, key=lambda x: x.serial):
            n = len(d.unique)
            n_ok = len(d.open_ok)
            n_ign = len(d.ignored)
            tgt = by_serial_target.get(d.serial, n)
            devices.append({
                "serial": d.serial,
                "short": d.serial[-10:] if len(d.serial) > 10 else d.serial,
                "unique_n": n,
                "open_ok_n": n_ok,
                "ignored_n": n_ign,
                "unique": [c.rsplit(".", 1)[-1] for c in d.unique],
                "open_ok": [c.rsplit(".", 1)[-1] for c in d.open_ok],
                "ignored": [c.rsplit(".", 1)[-1] for c in d.ignored],
                "shared_n": len(d.shared),
                "shared": [c.rsplit(".", 1)[-1] for c in d.shared],
                "ready": bool(d.open_ok) if allow_on else bool(d.ready),
                "error": d.error or "",
                "target": tgt,
                "need": max(0, tgt - n),
                "pct": int(round(100.0 * n / max_bar)),
                "target_pct": int(round(100.0 * tgt / max_bar)),
            })
        have_lo = min(haves) if haves else 0
        have_hi = max(haves) if haves else 0
        return {
            "ok": True,
            "phones": phones,
            "ready_phones": sum(1 for d in inv if (d.open_ok if allow_on else d.unique) and not d.error),
            "unique_on_phones": unique_n,
            "open_ok_on_phones": sum(len(d.open_ok) for d in inv),
            "ignored_on_phones": sum(len(d.ignored) for d in inv),
            "allowlist_on": allow_on,
            "unique_lo": have_lo,
            "unique_hi": have_hi,
            "spread_ok": (have_hi - have_lo) <= 1 if phones else True,
            "apk_on_disk": apk_n,
            "apk_gb": round((disk.get("bytes") or 0) / 1e9, 1),
            "cap": cap,
            "even_each": plan.get("even_each"),
            "target_lo": plan.get("target_lo"),
            "target_hi": plan.get("target_hi"),
            "balanced_after": plan.get("balanced_after"),
            "adds_total": sum(plan.get("adds") or []),
            "devices": devices,
            "error": "" if self.adb_available() else "adb not on PATH",
            "cached": (time.time() - _INV_CACHE[0]) < _INV_TTL and not force,
        }
