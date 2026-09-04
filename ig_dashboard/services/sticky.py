# -*- coding: utf-8 -*-
"""Sticky IP ledger + shared Drive health - soft-fail."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from .. import config

for _p in (Path(config.BASE), Path(__file__).resolve().parents[2]):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

FORMATS = ("feed", "story", "carousel", "reel")


class StickyDriveHealth:
    """Read ig_sticky_ip.json + shared Drive folder (+ optional overrides)."""

    def sticky_snapshot(self) -> Dict[str, Any]:
        path = Path(config.STICKY_IP_JSON)
        empty = {
            "path": str(path),
            "count": 0,
            "rows": [],
            "error": "",
        }
        try:
            if not path.exists():
                empty["error"] = "ig_sticky_ip.json not created yet (binds on first login)"
                return empty
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
            if not isinstance(data, dict):
                empty["error"] = "sticky file is not a JSON object"
                return empty
            rows = []
            for key, row in sorted(data.items(), key=lambda x: x[0].lower()):
                if not isinstance(row, dict):
                    continue
                if str(key).startswith("_"):
                    continue
                serial, _, pkg = str(key).partition("|")
                # Legacy username-keyed rows (pre clone sticky) still show as username.
                if "|" not in str(key):
                    serial, pkg = "", ""
                    label_user = str(key)
                else:
                    label_user = str(row.get("username") or "")
                rows.append(
                    {
                        "username": label_user or str(key),
                        "serial": serial,
                        "clone": pkg.rsplit(".", 1)[-1] if pkg else "",
                        "session": str(row.get("session") or ""),
                        "country": str(row.get("country") or ""),
                        "last_exit": str(row.get("last_exit") or ""),
                        "updated": str(row.get("updated") or ""),
                    }
                )
            return {
                "path": str(path),
                "count": len(rows),
                "rows": rows[:200],
                "error": "",
            }
        except Exception as e:
            empty["error"] = "%s: %s" % (type(e).__name__, e)
            return empty

    def shared_drive_snapshot(self, peek: bool = False) -> Dict[str, Any]:
        """One Drive folder for all accounts."""
        out: Dict[str, Any] = {
            "folder_id": "",
            "path": str(config.SHARED_DRIVE_FILE),
            "ok": False,
            "subs": {},
            "error": "",
            "hint": "Put folder ID in ig_shared_drive_folder.txt (or set IG_SHARED_DRIVE_FOLDER).",
        }
        try:
            import drive_content_ig_account as dca

            fid = dca.folder_for("", serial="") or dca.phones_root_id()
            if not fid:
                mapping = dca.load_phone_map()
                fid = next(iter(mapping.values()), "") if mapping else ""
            out["folder_id"] = fid
            out["ok"] = bool(dca.has_phone_drive() or fid)
            if not out["ok"]:
                out["error"] = "No phone Drive folders — set Content Bay map or parent folder."
                return out
            if peek:
                try:
                    import drive_content_ig as dc

                    drive, _sheets = dc.services()
                    for fmt in FORMATS:
                        sub = dca._find_subfolder(drive, fid, fmt)
                        out["subs"][fmt] = "ok" if sub else "missing"
                except Exception as e:
                    out["error"] = "Drive peek: %s" % e
            return out
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out

    def drive_map_snapshot(self, peek_folders: bool = False) -> Dict[str, Any]:
        """Optional per-user overrides only (rarely used)."""
        path = Path(config.ACCOUNT_DRIVE_MAP)
        out = {
            "path": str(path),
            "count": 0,
            "rows": [],
            "error": "",
            "shared": self.shared_drive_snapshot(peek=peek_folders),
        }
        try:
            mapping = {}
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8") or "{}")
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        if k and v and not str(k).startswith("_"):
                            mapping[str(k).strip()] = str(v).strip()

            rows: List[Dict[str, Any]] = []
            if peek_folders and mapping:
                try:
                    import drive_content_ig as dc
                    import drive_content_ig_account as dca

                    drive, sheets = dc.services()
                    for user, fid in sorted(mapping.items(), key=lambda x: x[0].lower()):
                        row = {
                            "username": user,
                            "folder_id": fid,
                            "subs": {},
                            "error": "",
                        }
                        try:
                            for fmt in FORMATS:
                                sub = dca._find_subfolder(drive, fid, fmt)
                                row["subs"][fmt] = "ok" if sub else "missing"
                        except Exception as e:
                            row["error"] = str(e)
                        rows.append(row)
                except Exception as e:
                    out["error"] = "Drive peek: %s" % e
                    rows = [
                        {
                            "username": u,
                            "folder_id": fid,
                            "subs": {},
                            "error": "",
                        }
                        for u, fid in sorted(mapping.items())
                    ]
            else:
                rows = [
                    {
                        "username": u,
                        "folder_id": fid,
                        "subs": {},
                        "error": "",
                    }
                    for u, fid in sorted(mapping.items())
                ]

            out["count"] = len(rows)
            out["rows"] = rows[:200]
            if not rows and not out["shared"].get("ok"):
                out["error"] = out["shared"].get("error") or out["error"]
            return out
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out
