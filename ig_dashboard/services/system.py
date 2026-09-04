# -*- coding: utf-8 -*-
"""Aggregate farm readiness - proxy, adb, files, Drive SA - never raises."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, List

from .. import config
from .devices import DeviceFleet
from .proxy import ProxyPoolHealth


class SystemHealth:
    """Independent readiness snapshot for the console banner / overview."""

    def __init__(self):
        self.proxy = ProxyPoolHealth()
        self.devices = DeviceFleet()

    def snapshot(self) -> Dict[str, Any]:
        checks = []  # type: List[Dict[str, Any]]
        blockers = []  # type: List[str]
        warnings = []  # type: List[str]

        # Proxy
        try:
            px = self.proxy.status()
        except Exception as e:
            px = {
                "ok": False,
                "label": "PROXY ?",
                "msg": "Proxy check failed: %s" % e,
                "detail": "error",
            }
        checks.append(
            {
                "id": "proxy",
                "label": "Proxy pool",
                "ok": bool(px.get("ok")),
                "detail": px.get("detail") or px.get("msg") or "",
            }
        )
        if not px.get("ok"):
            blockers.append(
                px.get("msg")
                or "Proxy pool offline - run start_proxy_pool.bat and leave it open."
            )

        # ADB
        adb_path = shutil.which("adb")
        try:
            serials = self.devices.list_serials()
            adb_ok = bool(adb_path is not None)
            adb_detail = (
                "%d device(s)" % len(serials)
                if adb_ok
                else "adb not found on PATH"
            )
            if adb_ok and not serials:
                warnings.append("No phones connected (adb devices empty).")
                adb_detail = "adb ok · 0 phones"
        except Exception as e:
            adb_ok = False
            serials = []
            adb_detail = str(e)
            warnings.append("ADB check failed: %s" % e)
        checks.append(
            {"id": "adb", "label": "ADB / phones", "ok": adb_ok, "detail": adb_detail}
        )
        if not adb_ok:
            blockers.append("ADB is missing or broken - install platform-tools and reopen the console.")

        # Accounts CSV
        acct = Path(config.ACCOUNTS_CSV)
        acct_ok = acct.exists() and acct.stat().st_size > 0 if acct.exists() else False
        checks.append(
            {
                "id": "accounts",
                "label": "Account pool CSV",
                "ok": acct_ok,
                "detail": str(acct) if acct_ok else "missing: %s" % acct,
            }
        )
        if not acct_ok:
            warnings.append("Instagram_farm_accounts.csv missing or empty - add accounts before posting.")

        # Results CSV (optional - created on first run)
        res = Path(config.RESULTS_CSV)
        checks.append(
            {
                "id": "results",
                "label": "Try history CSV",
                "ok": True,
                "detail": "present" if res.exists() else "will create on first run",
            }
        )

        # Service account for Drive
        sa = Path(config.SERVICE_ACCOUNT)
        sa_ok = sa.exists()
        checks.append(
            {
                "id": "drive_sa",
                "label": "Drive service account",
                "ok": sa_ok,
                "detail": str(sa.name) if sa_ok else "missing service_account.json",
            }
        )
        if not sa_ok:
            warnings.append(
                "service_account.json missing - Content page / posting images will fail until restored."
            )

        # Farm scripts
        farm_py = Path(config.BASE) / "run_ig_farm.py"
        if not farm_py.exists():
            farm_py = Path(__file__).resolve().parents[2] / "run_ig_farm.py"
        scripts_ok = farm_py.exists()
        checks.append(
            {
                "id": "scripts",
                "label": "Farm scripts",
                "ok": scripts_ok,
                "detail": str(farm_py.parent) if scripts_ok else "run_ig_farm.py not found",
            }
        )
        if not scripts_ok:
            blockers.append("run_ig_farm.py not found in this project folder.")

        try:
            inv = self.devices.inventory()
            native_n = sum(1 for d in inv if d.unique and not d.error)
        except Exception:
            native_n = 0
        checks.append(
            {
                "id": "unique_ig",
                "label": "Unique IG clones",
                "ok": native_n > 0 if serials else True,
                "detail": "%d phone(s) with a unique Nomix clone" % native_n,
            }
        )
        if serials and native_n == 0:
            blockers.append(
                "No connected phone has a unique IG clone. Install 1 APK per phone."
            )

        ready = len(blockers) == 0
        return {
            "ready": ready,
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "proxy": px,
            "phone_count": len(serials),
        }
