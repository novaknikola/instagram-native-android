# -*- coding: utf-8 -*-
"""Content lines, account status, warm-up profiles - dashboard control plane."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .. import config

for _p in (Path(config.BASE), Path(__file__).resolve().parents[2]):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)


class EcosystemControl:
    def lines_snapshot(self) -> Dict[str, Any]:
        try:
            import ig_content_line as cl

            rows = cl.list_lines()
            return {"rows": rows, "count": len(rows), "path": cl.LINES_PATH, "error": ""}
        except Exception as e:
            return {"rows": [], "count": 0, "path": "", "error": str(e)}

    def status_snapshot(self) -> Dict[str, Any]:
        try:
            import ig_account_status as aast

            rows = aast.list_statuses(300)
            return {"rows": rows, "count": len(rows), "path": aast.STATUS_PATH, "error": ""}
        except Exception as e:
            return {"rows": [], "count": 0, "path": "", "error": str(e)}

    def warmup_snapshot(self) -> Dict[str, Any]:
        try:
            import ig_warmup as wu

            path = getattr(config, "WARMUP_PROFILES_TXT", wu.DEFAULT_TXT)
            profiles = wu.load_profiles(path)
            return {
                "profiles": profiles,
                "path": str(path),
                "text": "\n".join(profiles),
                "error": "",
            }
        except Exception as e:
            return {"profiles": [], "path": "", "text": "", "error": str(e)}

    def save_warmup_text(self, text: str) -> Tuple[bool, str]:
        try:
            import ig_warmup as wu

            profiles = [
                ln.strip().lstrip("@")
                for ln in (text or "").splitlines()
                if ln.strip() and not ln.strip().startswith("#")
            ]
            path = wu.save_profiles_txt(
                profiles, getattr(config, "WARMUP_PROFILES_TXT", wu.DEFAULT_TXT)
            )
            return True, "saved %d profiles -> %s" % (len(profiles), path)
        except Exception as e:
            return False, str(e)

    def ensure_line(self, username: str, folder_id: str, profiles_csv: str = "") -> Tuple[bool, str]:
        try:
            import ig_content_line as cl

            profiles = [
                p.strip().lstrip("@")
                for p in (profiles_csv or "").split(",")
                if p.strip()
            ]
            lid, msg = cl.ensure_line(username, folder_id, warmup_profiles=profiles or None)
            if not lid:
                return False, msg
            return True, "%s (%s)" % (lid, msg)
        except Exception as e:
            return False, str(e)

    def replace(self, dead: str, new: str = "") -> Tuple[bool, str]:
        try:
            import ig_account_status as aast
            import ig_content_line as cl

            if new:
                ok, msg = cl.replace_account(dead, new)
                if ok:
                    aast.set_status(dead, "replaced", "manual:%s" % new)
                    aast.set_status(new, "active", "manual_inherit:%s" % dead)
                return ok, str(msg)
            ok, msg = aast.auto_replace(dead, reason="dashboard")
            return ok, str(msg)
        except Exception as e:
            return False, str(e)

    def bootstrap_map(self) -> Tuple[bool, str]:
        try:
            import drive_content_ig_account as dca
            import ig_content_line as cl

            n = cl.bootstrap_from_drive_map(dca.load_map())
            return True, "bootstrapped %d lines from Drive map" % n
        except Exception as e:
            return False, str(e)
