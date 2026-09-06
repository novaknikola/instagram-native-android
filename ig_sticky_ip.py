# -*- coding: utf-8 -*-
# ig_sticky_ip.py - each Instagram clone keeps the same proxy session (exit IP).
# Client rule: sticky is per clone (serial+pkg), not per username.
# Controlled rotate only after IP_MASK when runner opts in.
#
# Ledger: ig_sticky_ip.json
#   { "<serial>|<pkg>": {
#       "session": "...", "country": "us", "last_exit": "...",
#       "username": "...", "updated": "..."
#     }, ... }
from farm_root import ROOT
import json
import os
from datetime import datetime

STICKY_PATH = os.path.join(ROOT, 'ig_sticky_ip.json')


def clone_key(serial, clone):
    """Stable ledger key: serial|full.package.name"""
    s = (serial or "").strip()
    pkg = (clone or "").strip()
    if not s or not pkg:
        return ""
    return "%s|%s" % (s, pkg)


def _load():
    if not os.path.exists(STICKY_PATH):
        return {}
    try:
        with open(STICKY_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    parent = os.path.dirname(STICKY_PATH)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = STICKY_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, STICKY_PATH)


def get(serial, clone=None):
    """Return sticky binding for a clone, or None.

    Accepts get(serial, clone) or get(key) where key already contains '|'.
    """
    if clone is None and isinstance(serial, str) and "|" in serial:
        k = serial.strip()
    else:
        k = clone_key(serial, clone)
    if not k:
        return None
    row = _load().get(k)
    if not row or not row.get("session"):
        return None
    return row


def bind(serial, clone, session, country, exit_ip="", username=""):
    """Persist sticky session for this clone after a verified exit / login."""
    k = clone_key(serial, clone)
    if not k or not session:
        return
    data = _load()
    row = {
        "session": session,
        "country": (country or "us").strip().lower() or "us",
        "last_exit": exit_ip or "",
        "updated": datetime.now().isoformat(timespec="seconds"),
    }
    u = (username or "").strip()
    if u:
        row["username"] = u
    elif isinstance(data.get(k), dict) and data[k].get("username"):
        row["username"] = data[k]["username"]
    if isinstance(data.get(k), dict) and data[k].get("rotate_n") is not None:
        row["rotate_n"] = data[k]["rotate_n"]
    data[k] = row
    _save(data)
    short = (clone or "").rsplit(".", 1)[-1]
    print(
        "[sticky] bound %s/%s -> session=%s country=%s exit=%s"
        % ((serial or "")[-8:], short, session, row["country"], exit_ip or "?")
    )


def make_session_token(base_session, serial, clone):
    """Stable token for first bind: prefer existing clone sticky; never random-rotate."""
    sticky = get(serial, clone)
    if sticky and sticky.get("session"):
        return sticky["session"], sticky.get("country") or "us", True
    base = (base_session or "a0000").strip()
    suffix = "".join(
        c for c in ((clone or "").rsplit(".", 1)[-1] or "clone") if c.isalnum()
    )[:16].lower() or "clone"
    token = "%s_%s" % (base, suffix)
    return token, None, False


def controlled_rotate_token(serial, clone, country="us"):
    """
    One deliberate new session after a failure (IP_MASK path) or exit collision.
    Sticky stays preferred; only runs when caller opts into rotate.
    Entropy suffix so Floppy/IPRoyal actually hand a new sticky node (shared
    exit 171.231… 2026-09-05: _rotN alone still collapsed).
    """
    import secrets
    k = clone_key(serial, clone)
    if not k:
        return "a0000_rot1%s" % secrets.token_hex(3), (country or "us").strip().lower() or "us"
    prev = get(serial, clone) or {}
    base = (prev.get("session") or "a0000").split("_rot")[0]
    n = int(prev.get("rotate_n") or 0) + 1
    cc = (country or prev.get("country") or "us").strip().lower() or "us"
    token = "%s_rot%d%s" % (base, n, secrets.token_hex(3))
    data = _load()
    row = dict(data.get(k) or {})
    row["rotate_n"] = n
    row["session"] = token
    row["country"] = cc
    row["updated"] = datetime.now().isoformat(timespec="seconds")
    data[k] = row
    _save(data)
    short = (clone or "").rsplit(".", 1)[-1]
    print("[sticky] controlled rotate %s/%s -> %s (n=%d)"
          % ((serial or "")[-8:], short, token, n))
    return token, cc
