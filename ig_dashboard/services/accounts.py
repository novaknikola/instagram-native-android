# -*- coding: utf-8 -*-
"""Account pool from Instagram_farm_accounts.csv - never raises; atomic writes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..util import atomic_write_csv, iter_csv_dicts, safe_int
from .results import ResultLedger


@dataclass
class AccountRow:
    username: str
    model: str
    login: str
    post: str
    time: str


class AccountPool:
    """Reads Instagram_farm_accounts.csv; never stores passwords in API responses."""

    def __init__(self, ledger: Optional[ResultLedger] = None):
        self.ledger = ledger or ResultLedger()

    def stats(self) -> Dict[str, Any]:
        empty = {
            "total": 0,
            "tried": 0,
            "untried": 0,
            "posted": 0,
            "by_model": {m: 0 for m in config.MODELS},
            "error": "",
        }
        try:
            latest = self.ledger.latest_by_username()
            total = tried = posted = 0
            by_model = {m: 0 for m in config.MODELS}  # type: Dict[str, int]
            for row in iter_csv_dicts(config.ACCOUNTS_CSV):
                total += 1
                m = (row.get("model") or "").strip().lower() or "ig"
                if m not in by_model:
                    m = "ig"
                by_model[m] = by_model.get(m, 0) + 1
                u = (row.get("username") or "").strip()
                if u in latest:
                    tried += 1
                    if latest[u].get("post") == "POST_DONE":
                        posted += 1
            return {
                "total": total,
                "tried": tried,
                "untried": max(0, total - tried),
                "posted": posted,
                "by_model": by_model,
                "error": "" if config.ACCOUNTS_CSV.exists() else "Instagram_farm_accounts.csv missing",
            }
        except Exception as e:
            empty["error"] = "%s: %s" % (type(e).__name__, e)
            return empty

    def list_accounts(
        self,
        model: str = "",
        status: str = "",
        page: int = 1,
        per_page: int = 80,
    ) -> Tuple[List[AccountRow], int]:
        page = safe_int(page, 1, lo=1)
        per_page = safe_int(per_page, 80, lo=10, hi=200)
        try:
            latest = self.ledger.latest_by_username()
            out = []  # type: List[AccountRow]
            for row in iter_csv_dicts(config.ACCOUNTS_CSV):
                u = (row.get("username") or "").strip()
                if not u:
                    continue
                m = (row.get("model") or "").strip().lower()
                res = latest.get(u)
                if res:
                    login, post, t = res["login"], res["post"], res["time"]
                else:
                    login, post, t = "NOT_RUN", "NOT_RUN", ""
                out.append(AccountRow(u, m, login, post, t))
            if model:
                out = [a for a in out if a.model == model]
            if status == "live":
                out = [a for a in out if a.post == "POST_DONE"]
            elif status == "not_run":
                out = [a for a in out if a.login == "NOT_RUN"]
            elif status == "failed":
                out = [
                    a
                    for a in out
                    if a.login != "NOT_RUN" and a.post != "POST_DONE"
                ]
            total = len(out)
            start = (page - 1) * per_page
            return out[start : start + per_page], total
        except Exception:
            return [], 0

    def add_account(
        self, username: str, password: str, tfa_secret: str, model: str
    ) -> Tuple[bool, str]:
        try:
            username = (username or "").strip()
            password = (password or "").strip()
            tfa_secret = (tfa_secret or "").strip()
            model = (model or "").strip().lower()
            if not username or not password or not tfa_secret:
                return False, "username, password, and 2FA secret are required"
            if model not in config.MODELS:
                model = config.DEFAULT_MODEL

            fieldnames = ["username", "password", "tfa_secret", "model"]
            rows = []  # type: List[Dict[str, Any]]
            existing = set()
            for row in iter_csv_dicts(config.ACCOUNTS_CSV):
                # Preserve original columns if present
                if not rows and row:
                    keys = [k for k in row.keys() if k]
                    if keys:
                        # Keep known order, append extras
                        ordered = [k for k in fieldnames if k in keys]
                        for k in keys:
                            if k not in ordered:
                                ordered.append(k)
                        fieldnames = ordered
                u = (row.get("username") or "").strip()
                if u:
                    existing.add(u)
                rows.append(row)

            if username in existing:
                return False, "username already in pool"

            rows.append(
                {
                    "username": username,
                    "password": password,
                    "tfa_secret": tfa_secret,
                    "model": model,
                }
            )
            atomic_write_csv(config.ACCOUNTS_CSV, fieldnames, rows)
            return True, "added %s (%s)" % (username, model)
        except Exception as e:
            return False, "could not save account: %s" % e
