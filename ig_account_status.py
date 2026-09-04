# -*- coding: utf-8 -*-
# ig_account_status.py — account health + auto-replace onto content lines.
#
# Ledger: C:\threads-android\ig_account_status.json
#   { "username": {"status": "active|restricted|banned|disabled|dead|captcha_cool",
#                  "reason": "", "updated": "", "replaced_by": ""} }
from farm_root import ROOT
import csv
import json
import os
from datetime import datetime

STATUS_PATH = os.path.join(ROOT, 'ig_account_status.json')
ACCOUNTS_CSV = os.path.join(ROOT, 'Instagram_farm_accounts.csv')

# Login/post codes that mean the account/content-line occupant is unusable
DEAD_LOGIN = {
    "ACCOUNT_LOCKED",
    "ACCOUNT_DISABLED",
    "ACCOUNT_BANNED",
    "ACCOUNT_NOT_FOUND",
    "LOGIN_REQUIRED",  # after unexpected logout mid-session — soft; caller decides
}
DEAD_HARD = {
    "ACCOUNT_LOCKED",
    "ACCOUNT_DISABLED",
    "ACCOUNT_BANNED",
}
RESTRICTED = {
    "ACCOUNT_RESTRICTED",
    "ALL_IPS_MASKED",
}
# POST_BLOCKED alone is often verify lag / false empty (bur_cu 2026-08-10) —
# do NOT mark account restricted and skip future runs.
# POST_RATE_LIMIT is the real Meta "Try again later / We limit how often…"
# dialog (suzukii20648 2026-08-12) — cool the account; retrying burns proxies.


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _load():
    if not os.path.exists(STATUS_PATH):
        return {}
    try:
        with open(STATUS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    parent = os.path.dirname(STATUS_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = STATUS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, STATUS_PATH)


def get(username):
    u = (username or "").strip()
    if not u:
        return None
    return _load().get(u)


def set_status(username, status, reason=""):
    u = (username or "").strip()
    if not u:
        return
    data = _load()
    prev = data.get(u) or {}
    data[u] = {
        "status": (status or "active").strip().lower(),
        "reason": reason or "",
        "updated": _now(),
        "replaced_by": prev.get("replaced_by") or "",
    }
    _save(data)
    print("[acct-status] %s -> %s (%s)" % (u, data[u]["status"], reason or "-"))


def note_outcome(username, login="", post=""):
    """Map farm outcome codes → status. Returns recommended action: ok|cool|replace."""
    u = (username or "").strip()
    login = (login or "").strip()
    post = (post or "").strip()
    if login in DEAD_HARD or login == "ACCOUNT_NOT_FOUND":
        set_status(u, "dead", login)
        return "replace"
    if login in RESTRICTED:
        set_status(u, "restricted", "%s/%s" % (login, post))
        return "cool"
    if post == "POST_RATE_LIMIT":
        set_status(u, "restricted", "%s/%s" % (login or "LOGGED_IN", post))
        return "cool"
    if post == "POST_BLOCKED" and login == "LOGGED_IN":
        # Keep usable — verify may have raced a lagging 0-posts counter
        set_status(u, "active", "POST_BLOCKED_verify")
        return "ok"
    if post == "POST_BLOCKED":
        set_status(u, "restricted", "%s/%s" % (login, post))
        return "cool"
    if login in ("CAPTCHA", "CONTACT_VERIFY", "HUMAN_BLOCKED", "LOGIN_REJECTED"):
        set_status(u, "captcha_cool", login)
        return "cool"
    if login == "LOGGED_IN" and post == "POST_DONE":
        set_status(u, "active", "POST_DONE")
        return "ok"
    if login == "LOGGED_IN":
        set_status(u, "active", post or login)
        return "ok"
    return "ok"


def is_usable(username):
    row = get(username)
    if not row:
        return True
    return row.get("status") in ("active", "captcha_cool", "") or not row.get("status")


def _pool_usernames():
    if not os.path.exists(ACCOUNTS_CSV):
        return []
    try:
        return [
            (r.get("username") or "").strip()
            for r in csv.DictReader(open(ACCOUNTS_CSV, encoding="utf-8"))
            if (r.get("username") or "").strip()
        ]
    except Exception:
        return []


def next_replacement(exclude=None):
    """Pick next unused active pool username not already on a live content line."""
    exclude = set(exclude or [])
    try:
        import ig_content_line as cl
    except Exception:
        cl = None
    data = _load()
    occupied = set()
    if cl:
        for line in cl.list_lines():
            au = (line.get("active_username") or "").strip()
            if au and line.get("status") == "active":
                occupied.add(au)
    for u in _pool_usernames():
        if u in exclude or u in occupied:
            continue
        st = (data.get(u) or {}).get("status") or "active"
        if st in ("dead", "banned", "disabled", "replaced"):
            continue
        return u
    return None


def auto_replace(dead_username, reason=""):
    """
    Find replacement from pool; bind to same content line.
    Returns (ok, new_username_or_msg).
    """
    dead = (dead_username or "").strip()
    if not dead:
        return False, "no username"
    set_status(dead, "dead", reason or "auto_replace")
    try:
        import ig_content_line as cl
    except Exception as e:
        return False, "content_line import: %s" % e
    if not cl.line_id_for(dead):
        return False, "no content line for %s — cannot continue pointer" % dead
    new_u = next_replacement(exclude={dead})
    if not new_u:
        return False, "no replacement in pool"
    ok, msg = cl.replace_account(dead, new_u, pool_note=reason or "")
    if not ok:
        return False, msg
    data = _load()
    data[dead] = {
        "status": "replaced",
        "reason": reason or "",
        "updated": _now(),
        "replaced_by": new_u,
    }
    data[new_u] = {
        "status": "active",
        "reason": "inherited_from:%s" % dead,
        "updated": _now(),
        "replaced_by": "",
    }
    _save(data)
    cl.mark_line_status(new_u, "active")
    return True, new_u


def list_statuses(limit=200):
    data = _load()
    rows = []
    for u, row in sorted(data.items(), key=lambda x: x[0].lower()):
        r = dict(row)
        r["username"] = u
        rows.append(r)
    return rows[:limit]
