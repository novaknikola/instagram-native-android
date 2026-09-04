# -*- coding: utf-8 -*-
"""IG try-history ledger (ig_batch_results.csv) - never raises."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .. import config
from ..util import iter_csv_rows, safe_int

HEADERS = (
    "time",
    "serial",
    "clone",
    "username",
    "session",
    "country",
    "exit_ip",
    "login",
    "post",
    "image",
    "format",
    "mark",
    "story_link",
    "link_ok",
    "highlight_ok",
    "warmup",
)

# ig_run_2026-08-12_120836.txt or ig_run_2026-08-12_120836_1.txt
_RUN_START_RE = re.compile(
    r"^ig_run_(\d{4}-\d{2}-\d{2})_(\d{6})(?:_\d+)?\.txt$", re.I
)


@dataclass
class ResultRow:
    time: str
    serial: str
    clone: str
    username: str
    session: str
    country: str
    exit_ip: str
    login: str
    post: str
    image: str
    format: str = "feed"
    mark: str = ""
    story_link: str = ""
    link_ok: str = ""
    highlight_ok: str = ""
    warmup: str = ""

    def as_dict(self) -> dict:
        return {h: getattr(self, h) for h in HEADERS}


def parse_run_log_start(name: str) -> Optional[datetime]:
    """Parse run window start from ig_run_YYYY-MM-DD_HHMMSS[.N].txt."""
    name = (name or "").strip()
    m = _RUN_START_RE.match(Path_name(name))
    if not m:
        return None
    try:
        day, hms = m.group(1), m.group(2)
        return datetime.strptime("%s %s" % (day, hms), "%Y-%m-%d %H%M%S")
    except Exception:
        return None


def Path_name(name: str) -> str:
    """Basename only (avoid Path import cycles in helpers)."""
    return (name or "").replace("\\", "/").split("/")[-1]


def _parse_row_time(s: str) -> Optional[datetime]:
    s = (s or "").strip()
    if not s:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(s[:26], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", ""))
    except Exception:
        return None


def _aggregate_rows(rows: List[ResultRow]) -> dict:
    """Chart-ready aggregates for a row set."""
    login_c = Counter()  # type: Counter
    post_c = Counter()  # type: Counter
    fmt_c = Counter()  # type: Counter
    for row in rows:
        if row.login:
            login_c[row.login] += 1
        if row.post:
            post_c[row.post] += 1
        fmt_c[(row.format or "feed").lower()] += 1
    attempts = len(rows)
    post_done = post_c.get("POST_DONE", 0)
    rate = round((100.0 * post_done / attempts), 1) if attempts else 0.0
    # Top fail: prefer post fails, else login non-success
    fail_c = Counter()  # type: Counter
    for k, n in post_c.items():
        if k and k != "POST_DONE":
            fail_c[k] += n
    if not fail_c:
        for k, n in login_c.items():
            if k and k not in ("LOGGED_IN",):
                fail_c[k] += n
    top_fail = fail_c.most_common(1)[0][0] if fail_c else ""
    return {
        "attempts": attempts,
        "post_done": post_done,
        "logged_in": login_c.get("LOGGED_IN", 0),
        "success_rate": rate,
        "top_fail": top_fail,
        "login_counts": dict(login_c.most_common(16)),
        "post_counts": dict(post_c.most_common(12)),
        "format_counts": dict(fmt_c.most_common(8)),
        "top_fails": [
            {"code": k, "count": n} for k, n in fail_c.most_common(8)
        ],
    }


class ResultLedger:
    """Append-only farm outcomes - source of truth for POST_DONE / burns."""

    def __init__(self, path=None):
        self.path = path or config.RESULTS_CSV

    def _all_rows(self) -> List[ResultRow]:
        rows = []  # type: List[ResultRow]
        try:
            for r in iter_csv_rows(self.path):
                if not r or r[0] == "time":
                    continue
                if len(r) < 9:
                    continue
                while len(r) < 16:
                    r.append("")
                try:
                    fmt = (r[10] or "feed").strip() or "feed"
                    mark = (r[11] or "").strip()
                    rows.append(
                        ResultRow(
                            r[0],
                            r[1],
                            r[2],
                            r[3],
                            r[4],
                            r[5],
                            r[6],
                            r[7],
                            r[8],
                            r[9],
                            fmt,
                            mark,
                            (r[12] or "").strip(),
                            (r[13] or "").strip(),
                            (r[14] or "").strip(),
                            (r[15] or "").strip(),
                        )
                    )
                except Exception:
                    continue
        except Exception:
            return []
        return rows

    def load(
        self,
        limit: int = 250,
        login: str = "",
        post: str = "",
        format: str = "",
        mark: str = "",
    ) -> List[ResultRow]:
        limit = safe_int(limit, 250, lo=1, hi=5000)
        try:
            rows = self._all_rows()
            if login:
                rows = [r for r in rows if r.login == login]
            if post:
                rows = [r for r in rows if r.post == post]
            if format:
                want = format.strip().lower()
                rows = [r for r in rows if (r.format or "feed").lower() == want]
            if mark:
                want_m = mark.strip().upper()
                rows = [r for r in rows if (r.mark or "").strip().upper() == want_m]
            rows.sort(key=lambda x: x.time or "", reverse=True)
            return rows[:limit]
        except Exception:
            return []

    def latest_by_username(self) -> Dict[str, Dict[str, str]]:
        latest = {}  # type: Dict[str, Dict[str, str]]
        try:
            for row in self.load(limit=5000):
                if row.username and row.username not in latest:
                    latest[row.username] = {
                        "time": row.time,
                        "login": row.login,
                        "post": row.post,
                        "serial": row.serial,
                        "image": row.image,
                        "format": row.format,
                    }
        except Exception:
            return {}
        return latest

    def summary(self) -> dict:
        empty = {
            "total_attempts": 0,
            "post_done": 0,
            "logged_in": 0,
            "captcha_masked": 0,
            "not_found": 0,
            "login_counts": {},
            "post_counts": {},
            "format_counts": {},
        }
        try:
            login_c = Counter()  # type: Counter
            post_c = Counter()  # type: Counter
            fmt_c = Counter()  # type: Counter
            total = 0
            for row in self._all_rows():
                total += 1
                if row.login:
                    login_c[row.login] += 1
                if row.post:
                    post_c[row.post] += 1
                fmt_c[(row.format or "feed").lower()] += 1
            return {
                "total_attempts": total,
                "post_done": post_c.get("POST_DONE", 0),
                "logged_in": login_c.get("LOGGED_IN", 0),
                "captcha_masked": login_c.get("ALL_IPS_MASKED", 0),
                "not_found": login_c.get("ACCOUNT_NOT_FOUND", 0),
                "login_counts": dict(login_c.most_common(12)),
                "post_counts": dict(post_c.most_common(8)),
                "format_counts": dict(fmt_c.most_common(8)),
            }
        except Exception:
            return empty

    def rows_in_window(
        self,
        start: Optional[datetime],
        end: Optional[datetime] = None,
    ) -> List[ResultRow]:
        """Rows with time in [start, end). end=None means open-ended."""
        if start is None:
            return []
        out = []  # type: List[ResultRow]
        try:
            for row in self._all_rows():
                t = _parse_row_time(row.time)
                if t is None:
                    continue
                if t < start:
                    continue
                if end is not None and t >= end:
                    continue
                out.append(row)
        except Exception:
            return []
        out.sort(key=lambda r: r.time or "")
        return out

    def analytics_overview(self, days: int = 14) -> dict:
        """Cross-run BI: daily series + outcome mixes for last N days."""
        days = safe_int(days, 14, lo=1, hi=90)
        empty = {
            "days": days,
            "attempts": 0,
            "post_done": 0,
            "success_rate": 0.0,
            "daily": [],
            "login_counts": {},
            "post_counts": {},
            "format_counts": {},
            "top_fails": [],
        }
        try:
            now = datetime.now()
            # Inclusive calendar days ending today
            start_day = (now - timedelta(days=days - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            by_day = defaultdict(
                lambda: {"attempts": 0, "post_done": 0, "logged_in": 0}
            )  # type: dict
            window_rows = []  # type: List[ResultRow]
            for row in self._all_rows():
                t = _parse_row_time(row.time)
                if t is None or t < start_day:
                    continue
                window_rows.append(row)
                key = t.strftime("%Y-%m-%d")
                by_day[key]["attempts"] += 1
                if row.post == "POST_DONE":
                    by_day[key]["post_done"] += 1
                if row.login == "LOGGED_IN":
                    by_day[key]["logged_in"] += 1
            daily = []
            for i in range(days):
                d = start_day + timedelta(days=i)
                key = d.strftime("%Y-%m-%d")
                bucket = by_day.get(key) or {
                    "attempts": 0,
                    "post_done": 0,
                    "logged_in": 0,
                }
                att = bucket["attempts"]
                done = bucket["post_done"]
                daily.append(
                    {
                        "date": key,
                        "attempts": att,
                        "post_done": done,
                        "logged_in": bucket["logged_in"],
                        "success_rate": round((100.0 * done / att), 1)
                        if att
                        else 0.0,
                    }
                )
            agg = _aggregate_rows(window_rows)
            return {
                "days": days,
                "attempts": agg["attempts"],
                "post_done": agg["post_done"],
                "success_rate": agg["success_rate"],
                "daily": daily,
                "login_counts": agg["login_counts"],
                "post_counts": agg["post_counts"],
                "format_counts": agg["format_counts"],
                "top_fails": agg["top_fails"],
            }
        except Exception:
            return empty

    def analytics_for_run(
        self,
        run_name: str,
        next_run_name: str = "",
    ) -> dict:
        """Per-run BI: CSV rows in [run_start, next_run_start)."""
        empty = {
            "name": Path_name(run_name),
            "start": "",
            "end": "",
            "attempts": 0,
            "post_done": 0,
            "success_rate": 0.0,
            "top_fail": "",
            "login_counts": {},
            "post_counts": {},
            "format_counts": {},
            "top_fails": [],
            "rows": [],
        }
        try:
            start = parse_run_log_start(run_name)
            if start is None:
                empty["error"] = "bad run name"
                return empty
            end = parse_run_log_start(next_run_name) if next_run_name else None
            rows = self.rows_in_window(start, end)
            agg = _aggregate_rows(rows)
            # Cap table rows for UI
            sample = [r.as_dict() for r in rows[-80:]]
            sample.reverse()  # newest first in panel
            return {
                "name": Path_name(run_name),
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds") if end else "",
                "attempts": agg["attempts"],
                "post_done": agg["post_done"],
                "logged_in": agg["logged_in"],
                "success_rate": agg["success_rate"],
                "top_fail": agg["top_fail"],
                "login_counts": agg["login_counts"],
                "post_counts": agg["post_counts"],
                "format_counts": agg["format_counts"],
                "top_fails": agg["top_fails"],
                "rows": sample,
            }
        except Exception:
            return empty

    def enrich_run_logs(self, logs: List[dict]) -> List[dict]:
        """Attach attempts/post_done/success_rate/top_fail to list_run_logs entries.

        logs must be newest-first (same order as FarmRunner.list_run_logs).
        Window for logs[i] is [start_i, start_{i-1}) — open-ended for i==0.
        """
        out = []  # type: List[dict]
        try:
            for i, item in enumerate(logs or []):
                name = item.get("name") or ""
                start = parse_run_log_start(name)
                end = None
                if i > 0:
                    end = parse_run_log_start((logs[i - 1] or {}).get("name") or "")
                rows = self.rows_in_window(start, end) if start else []
                agg = _aggregate_rows(rows)
                enriched = dict(item)
                enriched.update(
                    {
                        "attempts": agg["attempts"],
                        "post_done": agg["post_done"],
                        "success_rate": agg["success_rate"],
                        "top_fail": agg["top_fail"],
                    }
                )
                out.append(enriched)
        except Exception:
            return list(logs or [])
        return out
