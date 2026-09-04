# -*- coding: utf-8 -*-
"""Account ↔ clone ↔ sticky IP join for the Console Binds page."""
from __future__ import annotations

from typing import Any, Dict, List

from .. import config


class BindLedger:
    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "rows": [],
            "count": 0,
            "farm_ok": 0,
            "skipped": 0,
            "unbound": 0,
            "allowlist_on": False,
            "error": "",
        }
        try:
            import ig_account_clone as ac
            import ig_clone_allow as al
            import ig_sticky_ip as sticky
        except Exception as e:
            out["error"] = "%s: %s" % (type(e).__name__, e)
            return out

        allow_on = False
        try:
            allow_on = al.active()
        except Exception:
            allow_on = False
        out["allowlist_on"] = allow_on

        binds = {}
        try:
            binds = ac.all_binds() or {}
        except Exception as e:
            out["error"] = "clone binds: %s" % e
            binds = {}

        from ..util import iter_csv_dicts

        pool = []
        try:
            for row in iter_csv_dicts(config.ACCOUNTS_CSV):
                u = (row.get("username") or "").strip()
                if u:
                    pool.append(
                        {
                            "username": u,
                            "model": (row.get("model") or "").strip().lower(),
                        }
                    )
        except Exception:
            pool = []

        by_user = {r["username"].strip().lower(): r for r in pool}
        seen = set()
        rows: List[Dict[str, Any]] = []

        def _row(username, model, rec):
            pkg = ac.full_pkg((rec or {}).get("clone") or "")
            serial = ((rec or {}).get("serial") or "").strip()
            farm_ok = bool(rec and serial)
            st = sticky.get(serial, pkg) if (serial and pkg) else {}
            if not st:
                st = {}
            status = "UNBOUND"
            if rec:
                status = "FARM_OK" if farm_ok else "NO_PHONE"
            return {
                "username": username,
                "model": model,
                "serial": serial,
                "clone": pkg.rsplit(".", 1)[-1] if pkg else "",
                "pkg": pkg,
                "session": (st.get("session") or "") if isinstance(st, dict) else "",
                "country": (st.get("country") or "") if isinstance(st, dict) else "",
                "last_exit": (st.get("last_exit") or "") if isinstance(st, dict) else "",
                "status": status,
                "farm_ok": farm_ok and bool(rec),
            }

        for rec in binds.values():
            if not isinstance(rec, dict):
                continue
            u = (rec.get("username") or "").strip()
            if not u:
                continue
            k = u.lower()
            seen.add(k)
            model = (by_user.get(k) or {}).get("model") or ""
            rows.append(_row(u, model, rec))

        for r in pool:
            k = r["username"].lower()
            if k in seen:
                continue
            rows.append(_row(r["username"], r["model"], None))

        rows.sort(key=lambda x: (x["status"] != "NO_PHONE", x["username"].lower()))
        out["rows"] = rows
        out["count"] = len(rows)
        out["farm_ok"] = sum(1 for x in rows if x["status"] == "FARM_OK")
        out["skipped"] = sum(1 for x in rows if x["status"] == "NO_PHONE")
        out["unbound"] = sum(1 for x in rows if x["status"] == "UNBOUND")
        return out
