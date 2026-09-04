# -*- coding: utf-8 -*-
# ig_scheduler.py — poll Schedule Google Sheet, dispatch due IG jobs.
# One run_ig_device process PER PHONE (assigned serial). All phones start together
# (short stagger). Multiple due rows on the same serial stay in order on that phone.
#
# Sheet columns (row 1 header):
#   due_at_iso | username | format | caption | status | result | serial | clone | notes
#   | story_link | highlight_title | warmup
#
# status: pending | claimed | done | failed | skipped
# format: feed | story | carousel | reel
# warmup: 1/true to run Reels warm-up before post
#
# Env / config:
#   IG_SCHEDULE_SHEET = spreadsheet id (optional if ig_schedule_sheet.txt exists)
#   Or Console Schedule page → paste Sheet ID → Save (writes ig_schedule_sheet.txt)
#   Share sheet Editor with farm service_account.json
#
#   python ig_scheduler.py              # run due now (parallel by phone)
#   python ig_scheduler.py --dry        # list due only
#   python ig_scheduler.py --loop 60    # poll every 60s
#   python ig_scheduler.py --max-jobs 1 # one job only (prove)
# Default: all due jobs, grouped by serial, launched together.
# IG_STAGGER seconds between phone process starts (default 2).
# IG_SCHEDULE_MAX=0 or unset = no cap. Set IG_SCHEDULE_MAX=1 to cap.
from farm_root import ROOT
SCHED_BUILD = "sched_parallel_20260820a"
import os
import sys
import csv
import json
import re
import time
import subprocess
import datetime
import tempfile
import collections

import drive_content_ig as dc
import drive_content_ig_account as dca
import ig_account_clone as acc_clone

# Optional local override file (same columns) if sheet not set:
SCHEDULE_CSV = os.path.join(ROOT, 'ig_schedule.csv')
ACCOUNTS_CSV = os.path.join(ROOT, 'Instagram_farm_accounts.csv')
BASE = os.environ.get("IG_FARM_BASE", ROOT)
RESULTS_CSV = os.path.join(BASE, "ig_batch_results.csv")
SCHEDULE_SHEET_FILE = os.path.join(BASE, "ig_schedule_sheet.txt")
SCHEDULE_SHEET_ENV = "IG_SCHEDULE_SHEET"


def normalize_sheet_id(raw):
    """Accept bare spreadsheet id or a docs.google.com/spreadsheets/d/... URL."""
    s = (raw or "").strip().strip('"').strip("'")
    if not s:
        return ""
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    # Paste sometimes includes /edit#gid=…
    if "/" in s and "http" not in s.lower():
        s = s.split("/")[0].strip()
    return s.split("?")[0].split("#")[0].strip()


def schedule_sheet_id():
    """Spreadsheet id: env IG_SCHEDULE_SHEET, else ig_schedule_sheet.txt (Console Save)."""
    env = (os.environ.get(SCHEDULE_SHEET_ENV) or "").strip()
    if env:
        return normalize_sheet_id(env)
    if os.path.isfile(SCHEDULE_SHEET_FILE):
        try:
            for ln in open(SCHEDULE_SHEET_FILE, encoding="utf-8"):
                s = ln.split("#")[0].strip()
                if s:
                    return normalize_sheet_id(s)
        except Exception as e:
            print("[sched] sheet file read fail: %s" % e)
    return ""


def set_schedule_sheet_id(sheet_id):
    """Persist spreadsheet id for Console / farm (no env required after handoff)."""
    global SCHEDULE_SHEET
    sid = normalize_sheet_id(sheet_id)
    parent = os.path.dirname(SCHEDULE_SHEET_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(SCHEDULE_SHEET_FILE, "w", encoding="utf-8") as fh:
        fh.write("# IG Schedule Google Spreadsheet ID (Console Schedule page)\n")
        fh.write("# Share the sheet Editor with service_account.json\n")
        fh.write(sid + "\n")
    SCHEDULE_SHEET = sid
    return sid


# Module alias for older callers / tests that assign SCHEDULE_SHEET = "…".
# Prefer _active_sheet_id() / schedule_sheet_id() (re-reads file after Console Save).
SCHEDULE_SHEET = ""


def _active_sheet_id():
    """Env / file first; fall back to module SCHEDULE_SHEET if tests set it."""
    sid = schedule_sheet_id()
    if sid:
        return sid
    return normalize_sheet_id(SCHEDULE_SHEET or "")


# Default: run ALL due jobs. Cap with IG_SCHEDULE_MAX=1 (or --max-jobs 1).
def _default_max_jobs():
    raw = (os.environ.get("IG_SCHEDULE_MAX") or "0").strip()
    try:
        return int(raw)
    except ValueError:
        return 0

HEADERS = [
    "due_at_iso", "username", "format", "caption", "status",
    "result", "serial", "clone", "notes",
    "story_link", "highlight_title", "warmup",
]

# Sheets need write for status updates
WRITE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def _sheets_rw():
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_service_account_file(dc.SA_FILE, scopes=WRITE_SCOPES)
    return build("sheets", "v4", credentials=creds)


def _sheets_http_status(err):
    try:
        return int(getattr(getattr(err, "resp", None), "status", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _sheets_retryable(err):
    st = _sheets_http_status(err)
    if st in (429, 500, 502, 503):
        return True
    s = str(err)
    return "RATE_LIMIT_EXCEEDED" in s or "Quota exceeded" in s


def _sheets_retry_wait(err, attempt):
    try:
        h = getattr(err, "resp", None)
        ra = None
        if h is not None:
            ra = h.get("retry-after") or h.get("Retry-After")
        if ra:
            return min(90, max(2, int(float(ra))))
    except (TypeError, ValueError, AttributeError):
        pass
    return min(60, 2 ** max(1, attempt))


def friendly_sheets_error(err):
    """Short operator text — never dump Google's quota JSON into the Console."""
    s = str(err or "")
    if "RATE_LIMIT" in s or "Quota exceeded" in s or "429" in s:
        return (
            "Google Sheet is busy (write limit). Wait about a minute, "
            "then click Queue retries once."
        )
    msg = "%s: %s" % (type(err).__name__, s)
    return msg if len(msg) < 180 else (msg[:177] + "...")


def _sheets_execute(make_req, what="sheets"):
    """Run a Sheets call; rebuild the request each try and back off on 429."""
    last = None
    for attempt in range(1, 8):
        try:
            return make_req().execute()
        except Exception as e:
            last = e
            if not _sheets_retryable(e) or attempt >= 7:
                raise
            wait = _sheets_retry_wait(e, attempt)
            print("[sched] %s HTTP %s — wait %ss [%d/7]"
                  % (what, _sheets_http_status(e) or "?", wait, attempt))
            time.sleep(wait)
    raise last


def _parse_due(s):
    s = (s or "").strip()
    if not s:
        return None
    # Strip timezone / Z / fractional seconds for strptime
    raw = s.replace("Z", "").strip()
    if "+" in raw[10:] or (raw.count("-") > 2 and "T" in raw):
        # e.g. 2026-08-05T15:45:01+00:00 or -05:00
        for sep in ("+", "-"):
            if "T" in raw and sep in raw[11:]:
                idx = raw.find(sep, 11)
                if idx > 0:
                    raw = raw[:idx]
                    break
    if "." in raw and ("T" in raw or " " in raw):
        # drop .ffffff
        head, _, _frac = raw.partition(".")
        raw = head
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
            return datetime.datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def due_rows(rows, now=None):
    """Pending rows ready to run.

    Console ScheduleView treats blank/unparseable due_at_iso as due-now.
    Scheduler used to SKIP those → due=0 while UI showed Due now=21
    (2026-08-11 test_schedule_ready: pending_status=21 due=0).
    """
    now = now or datetime.datetime.now()
    out = []
    for r in rows:
        st = (r.get("status") or "pending").strip().lower()
        if st not in ("pending", ""):
            continue
        due = _parse_due(r.get("due_at_iso"))
        # Match Console: None (blank / bad parse) OR due <= now → runnable
        if due is not None and due > now:
            continue
        fmt = (r.get("format") or "feed").strip().lower()
        if fmt not in ("feed", "story", "carousel", "reel"):
            fmt = "feed"
        r["format"] = fmt
        out.append(r)
    out.sort(key=lambda x: _parse_due(x.get("due_at_iso")) or now)
    return out


def _load_accounts():
    rows = list(csv.DictReader(open(ACCOUNTS_CSV, encoding="utf-8")))
    by_user = {}
    for i, r in enumerate(rows):
        u = (r.get("username") or "").strip()
        if u:
            by_user[u] = (i, r)
    return by_user


def _adb_devices():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True
    ).stdout or ""
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def _clones(serial):
    import ig_pkg
    out = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "list", "packages"],
        capture_output=True, text=True,
    ).stdout or ""
    return ig_pkg.ig_pkgs_from_pm_list(out)


def read_schedule_sheet(sheets):
    sid = _active_sheet_id()
    if not sid:
        return []
    resp = _sheets_execute(
        lambda: sheets.spreadsheets().values().get(
            spreadsheetId=sid, range="A1:L5000"
        ),
        "read schedule",
    )
    values = resp.get("values") or []
    if not values:
        return []
    header = [h.strip().lower() for h in values[0]]
    rows = []
    for i, raw in enumerate(values[1:], start=2):  # 1-indexed sheet row
        while len(raw) < len(HEADERS):
            raw.append("")
        row = {HEADERS[j]: (raw[j] if j < len(raw) else "") for j in range(len(HEADERS))}
        row["_sheet_row"] = i
        rows.append(row)
    return rows


def read_schedule_csv():
    if not os.path.exists(SCHEDULE_CSV):
        return []
    rows = []
    with open(SCHEDULE_CSV, encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh), start=2):
            row = {h: (r.get(h) or "") for h in HEADERS}
            row["_sheet_row"] = i
            row["_csv"] = True
            rows.append(row)
    return rows


def write_schedule_row(sheets, row):
    """Update status/result/serial/clone/notes for a sheet row."""
    if row.get("_csv"):
        # rewrite whole CSV
        all_rows = read_schedule_csv()
        for r in all_rows:
            if r["_sheet_row"] == row["_sheet_row"]:
                for h in HEADERS:
                    r[h] = row.get(h, r.get(h, ""))
        with open(SCHEDULE_CSV, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADERS, extrasaction="ignore")
            w.writeheader()
            for r in all_rows:
                w.writerow({h: r.get(h, "") for h in HEADERS})
        return
    sid = _active_sheet_id()
    if not sid or not sheets:
        return
    rnum = row["_sheet_row"]
    values = [[row.get(h, "") for h in HEADERS]]
    _sheets_execute(
        lambda: sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range="A%d:L%d" % (rnum, rnum),
            valueInputOption="RAW",
            body={"values": values},
        ),
        "update row %s" % rnum,
    )


# Recoverable fails — queue a second due-now job. Original history row stays.
RETRY_PAIRS = (
    ("LOGGED_IN", "POST_BLOCKED"),
    ("LOGGED_IN", "POST_TIMEOUT"),
    ("LOGIN_TIMEOUT", "SKIPPED"),
)
RETRY_NOTE_PREFIX = "RETRY from "


def _is_retry_eligible(login, post):
    login = (login or "").strip().upper()
    post = (post or "").strip().upper()
    return (login, post) in RETRY_PAIRS


def _latest_results_by_user(limit_lines=12000):
    """username -> last CSV row dict (does not overwrite older rows)."""
    path = RESULTS_CSV
    out = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        body = rows[1:] if rows and rows[0] and (rows[0][0] or "").lower() == "time" else rows
        for r in body[-limit_lines:]:
            if len(r) < 9:
                continue
            u = (r[3] or "").strip()
            if not u:
                continue
            while len(r) < 12:
                r.append("")
            out[u] = {
                "time": r[0],
                "serial": r[1],
                "clone": r[2],
                "username": u,
                "login": (r[7] or "").strip().upper(),
                "post": (r[8] or "").strip().upper(),
                "format": (r[10] or "feed").strip().lower() or "feed",
                "mark": (r[11] or "").strip().upper(),
            }
    except Exception as e:
        print("[sched] results read fail: %s" % e)
    return out


def _last_schedule_for_user(rows, username):
    last = None
    u = (username or "").strip()
    for r in rows:
        if (r.get("username") or "").strip() == u:
            last = r
    return last


def preview_retry_queue(rows=None):
    """How many latest-fail accounts would be queued (no write)."""
    jobs = list_retry_candidates(rows=rows)
    return {"ready": len(jobs), "jobs": jobs}


def list_retry_candidates(rows=None):
    """Latest ledger row per user is a recoverable fail, and no pending job yet."""
    if rows is None:
        sid = _active_sheet_id()
        try:
            rows = read_schedule_sheet(_sheets_rw()) if sid else read_schedule_csv()
        except Exception as e:
            print("[sched] retry preview schedule read fail: %s" % e)
            rows = read_schedule_csv()
    pending_keys = set()
    for r in rows or []:
        st = (r.get("status") or "pending").strip().lower()
        if st not in ("pending", "claimed", ""):
            continue
        u = (r.get("username") or "").strip()
        fmt = (r.get("format") or "feed").strip().lower() or "feed"
        if u:
            pending_keys.add((u.lower(), fmt))
    jobs = []
    for u, rec in _latest_results_by_user().items():
        if not _is_retry_eligible(rec.get("login"), rec.get("post")):
            continue
        fmt = rec.get("format") or "feed"
        if (u.lower(), fmt) in pending_keys:
            rec = dict(rec)
            rec["skip"] = "already_pending"
            continue
        prev = _last_schedule_for_user(rows or [], u) or {}
        pair = "%s/%s" % (rec.get("login"), rec.get("post"))
        jobs.append({
            "username": u,
            "format": fmt,
            "serial": (rec.get("serial") or prev.get("serial") or "").strip(),
            "clone": (rec.get("clone") or prev.get("clone") or "").strip(),
            "caption": (prev.get("caption") or "").strip(),
            "warmup": (prev.get("warmup") or "").strip(),
            "story_link": (prev.get("story_link") or "").strip(),
            "highlight_title": (prev.get("highlight_title") or "").strip(),
            "from": pair,
            "notes": RETRY_NOTE_PREFIX + pair,
        })
    jobs.sort(key=lambda x: x.get("username") or "")
    return jobs


def append_schedule_row(sheets, row):
    """Append one new job. Prefer append_schedule_rows for many jobs (one API write)."""
    return append_schedule_rows(sheets, [row])


def append_schedule_rows(sheets, rows):
    """Append many jobs in one Sheets write. Never overwrites history rows."""
    rows = list(rows or [])
    if not rows:
        return "csv" if (not _active_sheet_id() or not sheets) else "sheet"
    use_csv = any(r.get("_csv") for r in rows) or not _active_sheet_id() or not sheets
    if use_csv:
        parent = os.path.dirname(SCHEDULE_CSV)
        if parent:
            os.makedirs(parent, exist_ok=True)
        new = not os.path.isfile(SCHEDULE_CSV)
        with open(SCHEDULE_CSV, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=HEADERS, extrasaction="ignore")
            if new:
                w.writeheader()
            for row in rows:
                w.writerow({h: row.get(h, "") for h in HEADERS})
        return "csv"
    sid = _active_sheet_id()
    values = [[row.get(h, "") for h in HEADERS] for row in rows]
    _sheets_execute(
        lambda: sheets.spreadsheets().values().append(
            spreadsheetId=sid,
            range="A1:L1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ),
        "append %d rows" % len(values),
    )
    return "sheet"


def queue_recoverable_retries(max_jobs=200):
    """Add due-now pending jobs for recoverable fails. Original CSV rows stay.

    Returns {ok, queued, skipped_pending, dest, usernames, error}.
    """
    out = {
        "ok": False,
        "queued": 0,
        "skipped_pending": 0,
        "ready": 0,
        "dest": "",
        "usernames": [],
        "error": "",
    }
    try:
        sheets = None
        sid = _active_sheet_id()
        if sid:
            sheets = _sheets_rw()
            rows = read_schedule_sheet(sheets)
        else:
            rows = read_schedule_csv()
        pending_n = 0
        latest = _latest_results_by_user()
        pending_keys = set()
        for r in rows or []:
            st = (r.get("status") or "pending").strip().lower()
            if st not in ("pending", "claimed", ""):
                continue
            u = (r.get("username") or "").strip()
            fmt = (r.get("format") or "feed").strip().lower() or "feed"
            if u:
                pending_keys.add((u.lower(), fmt))
        for u, rec in latest.items():
            if _is_retry_eligible(rec.get("login"), rec.get("post")):
                fmt = rec.get("format") or "feed"
                if (u.lower(), fmt) in pending_keys:
                    pending_n += 1
        jobs = list_retry_candidates(rows=rows)
        out["ready"] = len(jobs) + pending_n
        out["skipped_pending"] = pending_n
        if max_jobs and len(jobs) > max_jobs:
            jobs = jobs[:max_jobs]
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        dest = "sheet" if sid and sheets else "csv"
        new_rows = []
        for job in jobs:
            row = {h: "" for h in HEADERS}
            row["due_at_iso"] = now
            row["username"] = job["username"]
            row["format"] = job["format"]
            row["caption"] = job.get("caption") or ""
            row["status"] = "pending"
            row["result"] = ""
            row["serial"] = job.get("serial") or ""
            row["clone"] = job.get("clone") or ""
            row["notes"] = job.get("notes") or (RETRY_NOTE_PREFIX + job.get("from", ""))
            row["story_link"] = job.get("story_link") or ""
            row["highlight_title"] = job.get("highlight_title") or ""
            row["warmup"] = job.get("warmup") or ""
            new_rows.append(row)
            out["usernames"].append(job["username"])
            print("[sched] queued RETRY %s %s (%s)"
                  % (job["username"], job["format"], job.get("from")))
        if new_rows:
            dest = append_schedule_rows(sheets, new_rows)
        out["queued"] = len(new_rows)
        out["dest"] = dest
        out["ok"] = True
        return out
    except Exception as e:
        out["error"] = friendly_sheets_error(e)
        print("[sched] queue retries fail: %s" % e)
        return out


def _status_for_outcome(login, post, result=""):
    """Map login/post codes → schedule status. Never leave terminal skips as pending."""
    post = (post or "").strip().upper()
    login = (login or "").strip().upper()
    result = (result or "").strip().upper()
    if not login and result:
        parts = result.split("/", 1)
        login = parts[0]
        if len(parts) > 1 and not post:
            post = parts[1]
    if post == "POST_DONE":
        return "done"
    if login in ("PROXY_DEAD", "ALL_IPS_MASKED"):
        return "skipped"
    if post == "SKIPPED" and login and login not in ("LOGGED_IN", "OK"):
        return "skipped"
    if not login and not post and not result:
        return "pending"
    return "failed"


def heal_pending_terminal_rows(rows, sheets=None):
    """Fix Sheet rows stuck pending after PROXY_DEAD/SKIPPED (and similar)."""
    n = 0
    for job in rows:
        st = (job.get("status") or "pending").strip().lower()
        if st not in ("pending", ""):
            continue
        result = (job.get("result") or "").strip()
        if not result:
            continue
        login = result.split("/", 1)[0].strip().upper()
        post = result.split("/", 1)[1].strip().upper() if "/" in result else ""
        new_st = _status_for_outcome(login, post, result)
        if new_st in ("pending",):
            continue
        job["status"] = new_st
        write_schedule_row(sheets, job)
        print("[sched] heal %s: pending + %s -> %s"
              % (job.get("username"), result, new_st))
        n += 1
    return n


def _live_slots():
    """Unique IG clone slots on online phones (stock/shared skipped in pick)."""
    devices = _adb_devices()
    slots = []
    for d in devices:
        for p in _clones(d):
            if not acc_clone.is_stock(p):
                slots.append((d, p))
    return slots


def _pick_device_clone(username, preferred_serial, preferred_clone, accounts):
    """Bind log wins for identity. Sheet serial pins NEW accounts onto that live phone."""
    slots = _live_slots()
    if not _adb_devices():
        return None, None, "no adb devices"
    pref = (preferred_serial or "").strip()
    allow = [pref] if pref and pref in _adb_devices() else None
    serial, clone, err = acc_clone.pick(username, slots, new_only_serials=allow)
    return serial, clone, err


def _recent_bad_outcomes(limit_lines=4000):
    """username -> last login, serial -> last login from ig_batch_results."""
    by_user = {}
    by_serial = {}
    path = RESULTS_CSV
    if not os.path.isfile(path):
        return by_user, by_serial
    try:
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        # skip header; take last N
        body = rows[1:] if rows and rows[0] and rows[0][0] == "time" else rows
        for r in body[-limit_lines:]:
            if len(r) < 9:
                continue
            serial, u, login = r[1], r[3], r[7]
            if u:
                by_user[u] = login
            if serial:
                by_serial[serial] = login
    except Exception as e:
        print("[sched] results ledger read fail: %s" % e)
    return by_user, by_serial


def group_jobs_by_serial(prepared):
    """Keep first-seen serial order. prepared items need a 'serial' key."""
    by = collections.OrderedDict()
    for row in prepared:
        s = (row.get("serial") or "").strip()
        if not s:
            continue
        by.setdefault(s, []).append(row)
    return by


def _sched_stagger():
    raw = (os.environ.get("IG_STAGGER") or "2").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 2


def _last_csv_for_user(username):
    login = post = ""
    try:
        with open(RESULTS_CSV, encoding="utf-8") as fh:
            last = None
            for r in csv.reader(fh):
                if len(r) > 8 and r[3] == username:
                    last = r
            if last:
                login, post = last[7], last[8]
    except Exception:
        pass
    result = ""
    if login or post:
        result = "%s/%s" % (login, post)
    return login, post, result


def run_due(dry=False, max_jobs=None):
    if max_jobs is None:
        max_jobs = _default_max_jobs()
    sheets = None
    sid = _active_sheet_id()
    if sid:
        sheets = _sheets_rw()
        rows = read_schedule_sheet(sheets)
        print("[sched] sheet=%s rows=%d" % (sid, len(rows)))
    else:
        rows = read_schedule_csv()
        print("[sched] local csv=%s rows=%d" % (SCHEDULE_CSV, len(rows)))

    print("[sched] build=%s" % SCHED_BUILD)

    healed = heal_pending_terminal_rows(rows, sheets)
    if healed:
        print("[sched] healed %d stale pending+terminal rows -> skipped/failed" % healed)

    due = due_rows(rows)
    print("[sched] due now: %d max_jobs=%s" % (len(due), max_jobs if max_jobs > 0 else "unlimited"))
    if not due:
        return 0
    if max_jobs and max_jobs > 0:
        due = due[:max_jobs]

    accounts = _load_accounts()
    bad_user, bad_serial = _recent_bad_outcomes()
    prepared = []
    for job in due:
        u = (job.get("username") or "").strip()
        fmt = job.get("format") or "feed"
        preferred_serial = (job.get("serial") or "").strip()
        if not preferred_serial:
            print("[sched] WARN %s: serial blank — bind/least-loaded phone"
                  % u)
        if u not in accounts:
            print("[sched] skip unknown username %s" % u)
            job["status"] = "failed"
            job["result"] = "UNKNOWN_USER"
            write_schedule_row(sheets, job)
            continue
        last_login = bad_user.get(u) or ""
        if last_login in ("ACCOUNT_NOT_FOUND",):
            print("[sched] skip %s — recent ledger %s" % (u, last_login))
            job["status"] = "failed"
            job["result"] = "SKIP_LEDGER_%s" % last_login
            write_schedule_row(sheets, job)
            continue
        row_idx, _acct = accounts[u]
        serial, clone, err = _pick_device_clone(
            u, preferred_serial, (job.get("clone") or "").strip(), accounts)
        if err or not clone:
            print("[sched] %s device/clone fail: %s" % (u, err))
            job["status"] = "failed"
            job["result"] = err or "NO_CLONE"
            write_schedule_row(sheets, job)
            continue
        if (bad_serial.get(serial) or "") == "APP_WONT_OPEN":
            print("[sched] skip serial %s — recent APP_WONT_OPEN" % serial[-8:])
            job["status"] = "failed"
            job["result"] = "SKIP_LEDGER_APP_WONT_OPEN"
            job["serial"] = serial
            write_schedule_row(sheets, job)
            continue

        notes = (job.get("notes") or "").strip()
        is_retry = notes.upper().startswith("RETRY")
        plan_item = {
            "clone": clone,
            "row": row_idx,
            "session": "a%04d" % row_idx,
            "model": "ig",
            "format": fmt,
            "caption": (job.get("caption") or "").strip(),
            "story_link": (job.get("story_link") or "").strip(),
            "highlight_title": (job.get("highlight_title") or "").strip(),
            "warmup": str(job.get("warmup") or "").strip().lower() in ("1", "true", "yes", "y"),
            "retry": is_retry,
            "mark": "RETRY" if is_retry else "",
        }
        prepared.append({
            "job": job,
            "serial": serial,
            "clone": clone,
            "username": u,
            "plan_item": plan_item,
        })

    by_serial = group_jobs_by_serial(prepared)
    print("[sched] PARALLEL phones=%d jobs=%d (same-phone clones stay in order)"
          % (len(by_serial), len(prepared)))
    if dry:
        for serial, items in by_serial.items():
            names = ", ".join(x["username"] for x in items)
            print("[dry] %s jobs=%d: %s" % (serial, len(items), names))
        return len(prepared)
    if not by_serial:
        return 0

    for item in prepared:
        job = item["job"]
        job["status"] = "claimed"
        job["serial"] = item["serial"]
        job["clone"] = item["clone"]
        write_schedule_row(sheets, job)

    try:
        import ig_pkg
        if ig_pkg.ig_posting_active():
            print("[proxy-save] skip fleet quiet — IG batch/device run active")
        else:
            ig_pkg.quiet_idle_ig(keep_serials=None)
    except Exception as e:
        print("[proxy-save] idle IG stop skip: %s" % e)

    try:
        import ig_phone_logs
    except Exception:
        ig_phone_logs = None

    stagger = _sched_stagger()
    handles = []
    procs = collections.OrderedDict()
    launched = 0
    for serial, items in by_serial.items():
        plan_items = [x["plan_item"] for x in items]
        plan_path = os.path.join(
            tempfile.gettempdir(), "ig_sched_%s.json" % serial[-8:]
        )
        json.dump(plan_items, open(plan_path, "w", encoding="utf-8"))
        if launched > 0 and stagger > 0:
            print("[sched] stagger %ds before phone %d/%d (%s…)"
                  % (stagger, launched + 1, len(by_serial), serial[-8:]))
            time.sleep(stagger)
        names = ", ".join(x["username"] for x in items)
        print("[sched] LAUNCH %d/%d serial=%s jobs=%d %s"
              % (launched + 1, len(by_serial), serial, len(items), names))
        cmd = [sys.executable, "-u", "run_ig_device.py", serial, plan_path, "us", "us"]
        if any(x["plan_item"].get("warmup") for x in items):
            cmd.append("--warmup")
        kwargs = {
            "cwd": BASE if os.path.isdir(BASE) else os.getcwd(),
        }
        if ig_phone_logs is not None:
            try:
                fh, lp = ig_phone_logs.open_phone_log(serial)
            except Exception:
                fh, lp = None, ""
            if fh:
                handles.append(fh)
                kwargs["stdout"] = fh
                kwargs["stderr"] = subprocess.STDOUT
                print("[sched] phone log -> %s" % lp)
        procs[serial] = subprocess.Popen(cmd, **kwargs)
        launched += 1

    print("[sched] %d phone process(es) running together — waiting" % len(procs))
    while True:
        alive = [(s, p) for s, p in procs.items() if p.poll() is None]
        if not alive:
            break
        time.sleep(1.5)

    for fh in handles:
        try:
            fh.close()
        except Exception:
            pass

    ran = 0
    for serial, items in by_serial.items():
        rc = procs[serial].returncode
        for item in items:
            u = item["username"]
            job = item["job"]
            login, post, result = _last_csv_for_user(u)
            if not result:
                result = "OK" if rc == 0 else "RC_%s" % rc
            job["result"] = result
            job["status"] = _status_for_outcome(login, post, result)
            job["serial"] = serial
            job["clone"] = item["clone"]
            write_schedule_row(sheets, job)
            print("[sched] result %s -> %s %s" % (u, job["status"], result))
            ran += 1
    return ran


def main():
    args = sys.argv[1:]
    if "--queue-retries" in args:
        info = queue_recoverable_retries()
        print("[sched] queue-retries queued=%s skipped_pending=%s dest=%s err=%s"
              % (info.get("queued"), info.get("skipped_pending"),
                 info.get("dest"), info.get("error") or "-"))
        sys.exit(0 if info.get("ok") else 1)
    dry = "--dry" in args
    loop_s = 0
    max_jobs = _default_max_jobs()
    if "--loop" in args:
        i = args.index("--loop")
        loop_s = int(args[i + 1]) if i + 1 < len(args) else 60
    if "--max-jobs" in args:
        i = args.index("--max-jobs")
        try:
            max_jobs = int(args[i + 1]) if i + 1 < len(args) else max_jobs
        except (ValueError, IndexError):
            pass
    if not _active_sheet_id() and not os.path.exists(SCHEDULE_CSV):
        print("Set schedule Sheet in Console (or IG_SCHEDULE_SHEET / %s) or create %s"
              % (SCHEDULE_SHEET_FILE, SCHEDULE_CSV))
        print("Header: %s" % ",".join(HEADERS))
        # seed empty csv
        if not os.path.exists(SCHEDULE_CSV):
            with open(SCHEDULE_CSV, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, fieldnames=HEADERS).writeheader()
            print("Created empty %s" % SCHEDULE_CSV)
        sys.exit(1)
    if loop_s > 0:
        print("[sched] loop every %ds max_jobs=%s" % (loop_s, max_jobs))
        while True:
            run_due(dry=dry, max_jobs=max_jobs)
            time.sleep(loop_s)
    else:
        n = run_due(dry=dry, max_jobs=max_jobs)
        print("[sched] finished jobs=%d" % n)


if __name__ == "__main__":
    main()
