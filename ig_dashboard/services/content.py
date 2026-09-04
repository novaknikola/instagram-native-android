# -*- coding: utf-8 -*-
"""Per-phone Drive folders (Reels / Posts / Stories). Soft-fail, never raises."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config

for _p in (Path(config.BASE), Path(__file__).resolve().parents[2]):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

FORMATS = ("reel", "feed", "story")


@dataclass
class PhoneContent:
    serial: str
    folder_id: str = ""
    subs: Dict[str, str] = field(default_factory=dict)
    unused: Dict[str, int] = field(default_factory=dict)
    folder_n: Dict[str, int] = field(default_factory=dict)
    error: str = ""


class ContentPool:
    """Read-only peek of per-phone Drive supply."""

    def snapshot(self, include_files: bool = True) -> List[Any]:
        """Back-compat: list of phone rows (not model cards)."""
        return self.phone_snapshot().get("phones") or []

    def phone_snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "phones": [],
            "map_path": str(config.PHONE_DRIVE_MAP),
            "root_path": str(config.PHONES_DRIVE_ROOT),
            "root_id": "",
            "mapped": 0,
            "missing": 0,
            "error": "",
        }
        try:
            import drive_content_ig_account as dca
            from .devices import DeviceFleet
        except Exception as e:
            out["error"] = "import failed: %s" % e
            return out

        out["root_id"] = dca.phones_root_id()
        mapping = dca.load_phone_map()
        serials = []
        try:
            serials = DeviceFleet().list_serials()
        except Exception:
            serials = []
        seen = set(serials)
        for k in mapping:
            if k not in seen and len(k) > 8:
                serials.append(k)
                seen.add(k)

        if not Path(config.SERVICE_ACCOUNT).exists():
            out["error"] = "service_account.json missing"
            phones = [
                PhoneContent(serial=s, folder_id=_lookup(mapping, s), error="no SA")
                for s in serials
            ]
            out["phones"] = phones
            return out

        drive = None
        try:
            import drive_content_ig as dc

            drive, _sheets = dc.services()
        except Exception as e:
            out["error"] = "Google auth: %s" % e

        phones: List[PhoneContent] = []
        for serial in serials:
            row = PhoneContent(serial=serial)
            try:
                fid = dca.folder_for("", serial=serial, drive=drive)
                row.folder_id = fid or ""
                if not fid:
                    row.error = "unmapped"
                    out["missing"] += 1
                    phones.append(row)
                    continue
                out["mapped"] += 1
                if drive is None:
                    phones.append(row)
                    continue
                for fmt in FORMATS:
                    sub, matched = dca._find_format_subfolder(drive, fid, fmt)
                    row.subs[fmt] = matched or "missing"
                    files = dca.list_media(drive, "", fmt, serial=serial) if sub else []
                    used = dc._used_ids(dca._used_path("", fmt, serial=serial))
                    row.folder_n[fmt] = len(files)
                    row.unused[fmt] = len([f for f in files if f["id"] not in used])
            except Exception as e:
                row.error = "%s: %s" % (type(e).__name__, e)
            phones.append(row)
        out["phones"] = phones
        return out

    def reset_ledger(self, model: str = "", serial: str = "") -> Tuple[bool, str]:
        serial = (serial or "").strip()
        if not serial:
            return False, "serial required to reset a phone ledger"
        try:
            import drive_content_ig_account as dca
            import os

            n = 0
            for fmt in FORMATS + ("carousel",):
                path = dca._used_path("", fmt, serial=serial)
                if os.path.isfile(path):
                    os.remove(path)
                    n += 1
            return True, "cleared %d ledger file(s) for %s" % (n, serial[-8:])
        except Exception as e:
            return False, "reset failed: %s" % e

    def save_phone_map_text(self, raw: str) -> Tuple[bool, str]:
        """Accept JSON object or lines 'SERIAL FOLDER_ID' / 'SERIAL,FOLDER_ID'."""
        try:
            import json
            import drive_content_ig_account as dca

            raw = (raw or "").strip()
            mapping = {}
            if raw.startswith("{"):
                data = json.loads(raw)
                if not isinstance(data, dict):
                    return False, "JSON must be an object {serial: folder_id}"
                mapping = data
            else:
                for ln in raw.splitlines():
                    ln = ln.split("#")[0].strip()
                    if not ln:
                        continue
                    if "," in ln:
                        a, b = ln.split(",", 1)
                    elif " " in ln:
                        a, b = ln.split(None, 1)
                    else:
                        continue
                    mapping[a.strip()] = b.strip()
            n = len(dca.save_phone_map(mapping))
            return True, "saved %d phone folder(s)" % n
        except Exception as e:
            return False, "save failed: %s" % e

    def as_dict(self) -> Dict[str, Any]:
        try:
            snap = self.phone_snapshot()
            return {
                "phones": [
                    {
                        "serial": p.serial,
                        "folder_id": p.folder_id,
                        "subs": p.subs,
                        "unused": p.unused,
                        "folder_n": p.folder_n,
                        "error": p.error,
                    }
                    for p in (snap.get("phones") or [])
                    if isinstance(p, PhoneContent)
                ],
                "mapped": snap.get("mapped"),
                "missing": snap.get("missing"),
                "root_id": snap.get("root_id"),
                "error": snap.get("error"),
            }
        except Exception as e:
            return {"phones": [], "error": str(e)}


def _lookup(mapping, serial):
    if serial in mapping:
        return mapping[serial]
    tail = serial[-8:] if len(serial) >= 8 else serial
    return mapping.get(tail, "")
