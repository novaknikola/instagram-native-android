# -*- coding: utf-8 -*-
"""Schedule Sheet / CSV peek - read-only; Sheet remains source of truth."""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .. import config

for _p in (Path(config.BASE), Path(__file__).resolve().parents[2]):
    s = str(_p)
    if s not in sys.path:
        sys.path.insert(0, s)

HEADERS = [
    "due_at_iso",
    "username",
    "format",
    "caption",
    "status",
    "result",
    "serial",
    "clone",
    "notes",
    "story_link",
    "highlight_title",
    "warmup",
]


def _resolve_sheet_id() -> str:
    """Env → ig_schedule_sheet.txt (same as ig_scheduler.schedule_sheet_id)."""
    try:
        import ig_scheduler as sch  # type: ignore

        return (sch.schedule_sheet_id() or "").strip()
    except Exception:
        pass
    env = (os.environ.get("IG_SCHEDULE_SHEET") or "").strip()
    if env:
        return env
    path = Path(getattr(config, "SCHEDULE_SHEET_FILE", config.BASE / "ig_schedule_sheet.txt"))
    if path.is_file():
        try:
            for ln in path.read_text(encoding="utf-8").splitlines():
                s = ln.split("#")[0].strip()
                if s:
                    return s
        except Exception:
            pass
    return (getattr(config, "SCHEDULE_SHEET", "") or "").strip()


def sheet_config_snapshot() -> Dict[str, Any]:
    sid = _resolve_sheet_id()
    path = str(getattr(config, "SCHEDULE_SHEET_FILE", config.BASE / "ig_schedule_sheet.txt"))
    from_env = bool((os.environ.get("IG_SCHEDULE_SHEET") or "").strip())
    return {
        "sheet_id": sid,
        "path": path,
        "ok": bool(sid),
        "from_env": from_env,
        "error": "" if sid else "Schedule Sheet not set",
    }


def _parse_due(s: str):
    """Parse Sheet due times. Blank/unparseable → None (treated as due-now in UI)."""
    s = (s or "").strip()
    if not s:
        return None
    raw = s.replace("Z", "").strip()
    if "+" in raw[10:] or (raw.count("-") > 2 and "T" in raw):
        for sep in ("+", "-"):
            if "T" in raw and sep in raw[11:]:
                idx = raw.find(sep, 11)
                if idx > 0:
                    raw = raw[:idx]
                    break
    if "." in raw and ("T" in raw or " " in raw):
        raw = raw.partition(".")[0]
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


class ScheduleView:
    """Due / pending / recent schedule rows for the console."""

    def __init__(self):
        self.csv_path = Path(config.SCHEDULE_CSV)
        self.sheet_id = _resolve_sheet_id()
        self.sheet_file = str(
            getattr(config, "SCHEDULE_SHEET_FILE", config.BASE / "ig_schedule_sheet.txt")
        )

    def _normalize(self, raw: Dict[str, Any], row_num: int = 0) -> Dict[str, str]:
        out = {h: str(raw.get(h) or "").strip() for h in HEADERS}
        out["row"] = str(row_num or raw.get("row") or "")
        out["format"] = (out["format"] or "feed").lower()
        out["status"] = (out["status"] or "pending").lower()
        # Display heal: pending + PROXY_DEAD/SKIPPED (etc.) must not look runnable
        if out["status"] in ("pending", ""):
            result = (out.get("result") or "").strip().upper()
            if result:
                login = result.split("/", 1)[0].strip()
                post = result.split("/", 1)[1].strip() if "/" in result else ""
                if login in ("PROXY_DEAD", "ALL_IPS_MASKED"):
                    out["status"] = "skipped"
                elif post == "SKIPPED" and login and login not in ("LOGGED_IN", "OK"):
                    out["status"] = "skipped"
                elif login and post and post != "SKIPPED" and login not in ("",):
                    # Has a real outcome pair but status never updated
                    if post == "POST_DONE":
                        out["status"] = "done"
                    elif login not in ("",):
                        out["status"] = "failed"
        return out

    def _from_csv(self) -> List[Dict[str, str]]:
        if not self.csv_path.exists():
            return []
        rows = []
        with open(self.csv_path, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for i, r in enumerate(reader, start=2):
                rows.append(self._normalize(r, i))
        return rows

    def _from_sheet(self) -> List[Dict[str, str]]:
        if not self.sheet_id:
            return []
        try:
            import drive_content_ig as dc

            sheets = dc.services()[1]
            resp = (
                sheets.spreadsheets()
                .values()
                .get(spreadsheetId=self.sheet_id, range="A1:L5000")
                .execute()
            )
            values = resp.get("values") or []
            if not values:
                return []
            header = [h.strip().lower() for h in values[0]]
            rows = []
            for i, raw in enumerate(values[1:], start=2):
                while len(raw) < len(HEADERS):
                    raw.append("")
                d = {}
                for j, h in enumerate(HEADERS):
                    try:
                        idx = header.index(h)
                        d[h] = raw[idx] if idx < len(raw) else ""
                    except ValueError:
                        d[h] = raw[j] if j < len(raw) else ""
                rows.append(self._normalize(d, i))
            return rows
        except Exception:
            return []

    def load(self) -> Dict[str, Any]:
        # Re-read file/env each request so Console Save works without restart.
        self.sheet_id = _resolve_sheet_id()
        err = ""
        source = "none"
        rows: List[Dict[str, str]] = []
        try:
            sheet_rows = self._from_sheet()
            if sheet_rows:
                rows = sheet_rows
                source = "sheet"
            else:
                rows = self._from_csv()
                source = "csv" if rows or self.csv_path.exists() else "none"
                if not rows and not self.sheet_id and not self.csv_path.exists():
                    err = (
                        "No schedule - paste Sheet ID below and Save "
                        "(or create ig_schedule.csv)"
                    )
        except Exception as e:
            try:
                import ig_scheduler as sch  # type: ignore

                err = sch.friendly_sheets_error(e)
            except Exception:
                err = "Google Sheet is busy. Wait a minute and refresh."
            rows = []

        now = datetime.now()
        due = []
        upcoming = []
        pending = []
        recent = []
        counts = {
            "pending": 0,
            "claimed": 0,
            "done": 0,
            "failed": 0,
            "skipped": 0,
            "other": 0,
            "due_now": 0,
            "upcoming": 0,
        }
        for r in rows:
            st = r.get("status") or "pending"
            if st in counts:
                counts[st] += 1
            else:
                counts["other"] += 1
            if st == "pending":
                pending.append(r)
                dt = _parse_due(r.get("due_at_iso") or "")
                if dt is None or dt <= now:
                    due.append(r)
                    counts["due_now"] += 1
                else:
                    upcoming.append(r)
                    counts["upcoming"] += 1
            if st in ("done", "failed", "skipped", "claimed"):
                recent.append(r)

        upcoming.sort(key=lambda x: x.get("due_at_iso") or "")
        recent = list(reversed(recent[-80:]))
        return {
            "source": source,
            "sheet_id": self.sheet_id,
            "sheet_file": self.sheet_file,
            "csv_path": str(self.csv_path),
            "error": err,
            "counts": counts,
            "due": due,
            "upcoming": upcoming,
            "pending": pending,
            "recent": recent,
            "total": len(rows),
            "server_now": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
