# -*- coding: utf-8 -*-
# ig_content_line.py - content pipeline survives account death/replacement.
#
# A "content line" owns Drive folder + used-media ledgers. Usernames are
# occupants. When an account dies, assign a replacement username to the same
# line - posting continues from the exact media pointer (no restart).
#
# Ledger: C:\threads-android\ig_content_lines.json
#   {
#     "lines": {
#       "line_abc": {
#         "drive_folder_id": "...",
#         "active_username": "user1",
#         "status": "active",
#         "warmup_profiles": ["nike", "adidas"],
#         "history": [{"username":"user1","at":"...","event":"bound"}]
#       }
#     },
#     "by_username": {"user1": "line_abc"}
#   }
from farm_root import ROOT
import json
import os
import re
from datetime import datetime

LINES_PATH = os.path.join(ROOT, 'ig_content_lines.json')
USED_DIR = os.path.join(ROOT, 'used_ig_accounts')
FORMATS = ("feed", "story", "carousel", "reel")


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _safe(s):
    return re.sub(r"[^\w.\-]+", "_", (s or "").strip()) or "unknown"


def _load():
    if not os.path.exists(LINES_PATH):
        return {"lines": {}, "by_username": {}}
    try:
        with open(LINES_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return {"lines": {}, "by_username": {}}
        data.setdefault("lines", {})
        data.setdefault("by_username", {})
        return data
    except Exception:
        return {"lines": {}, "by_username": {}}


def _save(data):
    parent = os.path.dirname(LINES_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = LINES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, LINES_PATH)


def line_id_for(username):
    u = (username or "").strip()
    if not u:
        return None
    return _load()["by_username"].get(u)


def get_line(line_id):
    if not line_id:
        return None
    return _load()["lines"].get(line_id)


def drive_folder_for(username):
    """Resolve Drive folder via content line (preferred) or None."""
    lid = line_id_for(username)
    line = get_line(lid) if lid else None
    if line and line.get("drive_folder_id"):
        return line["drive_folder_id"], lid
    return None, lid


def used_path(username_or_line, fmt):
    """Used-media ledger keyed by content line when bound, else username."""
    os.makedirs(USED_DIR, exist_ok=True)
    fmt = (fmt or "feed").lower()
    lid = line_id_for(username_or_line)
    if lid:
        key = lid
    elif get_line(username_or_line):
        key = username_or_line
    else:
        key = _safe(username_or_line)
    return os.path.join(USED_DIR, "%s_%s.txt" % (_safe(key), fmt))


def ensure_line(username, drive_folder_id, line_id=None, warmup_profiles=None):
    """Bind username to a content line (create if needed). Same folder = continuity."""
    u = (username or "").strip()
    fid = (drive_folder_id or "").strip()
    if not u or not fid:
        return None, "username and drive_folder_id required"
    data = _load()
    existing = data["by_username"].get(u)
    if existing and existing in data["lines"]:
        line = data["lines"][existing]
        if fid and line.get("drive_folder_id") != fid:
            line["drive_folder_id"] = fid
        if warmup_profiles is not None:
            line["warmup_profiles"] = list(warmup_profiles)
        line["active_username"] = u
        line["status"] = "active"
        _save(data)
        return existing, "updated"
    # Reuse line that already owns this Drive folder (continuity attach)
    for lid, line in data["lines"].items():
        if line.get("drive_folder_id") == fid:
            old = line.get("active_username") or ""
            line["active_username"] = u
            line["status"] = "active"
            line.setdefault("history", []).append(
                {"username": u, "at": _now(), "event": "attached", "prev": old}
            )
            if warmup_profiles is not None:
                line["warmup_profiles"] = list(warmup_profiles)
            if old and old != u:
                data["by_username"][old] = lid  # keep history pointer
            data["by_username"][u] = lid
            _save(data)
            return lid, "attached_existing_folder"
    lid = (line_id or "").strip() or ("line_%s" % _safe(u)[:24])
    n = 1
    base = lid
    while lid in data["lines"]:
        lid = "%s_%d" % (base, n)
        n += 1
    data["lines"][lid] = {
        "drive_folder_id": fid,
        "active_username": u,
        "status": "active",
        "warmup_profiles": list(warmup_profiles or []),
        "history": [{"username": u, "at": _now(), "event": "created"}],
    }
    data["by_username"][u] = lid
    _save(data)
    return lid, "created"


def replace_account(dead_username, new_username, pool_note=""):
    """
    Mark dead username replaced; new username inherits same content line
    (Drive folder + used ledgers). Sticky IP is NOT inherited (new identity).
    """
    dead = (dead_username or "").strip()
    new = (new_username or "").strip()
    if not dead or not new:
        return False, "need dead and new username"
    data = _load()
    lid = data["by_username"].get(dead)
    if not lid or lid not in data["lines"]:
        return False, "no content line for %s" % dead
    line = data["lines"][lid]
    line["active_username"] = new
    line["status"] = "active"
    line.setdefault("history", []).append(
        {
            "username": new,
            "at": _now(),
            "event": "replace",
            "prev": dead,
            "note": pool_note or "",
        }
    )
    data["by_username"][new] = lid
    # Keep dead username mapped to same line for audit (optional tombstone)
    data["by_username"][dead] = lid
    _save(data)
    print(
        "[content-line] %s -> %s inherits line=%s folder=%s"
        % (dead, new, lid, line.get("drive_folder_id"))
    )
    return True, lid


def mark_line_status(username, status):
    """status: active | restricted | banned | disabled | dead | paused"""
    lid = line_id_for(username)
    if not lid:
        return False
    data = _load()
    line = data["lines"].get(lid)
    if not line:
        return False
    line["status"] = (status or "dead").strip().lower()
    line.setdefault("history", []).append(
        {"username": username, "at": _now(), "event": "status:%s" % line["status"]}
    )
    _save(data)
    return True


def warmup_profiles_for(username):
    lid = line_id_for(username)
    line = get_line(lid) if lid else None
    if line and line.get("warmup_profiles"):
        return list(line["warmup_profiles"])
    return []


def set_warmup_profiles(username, profiles):
    lid = line_id_for(username)
    if not lid:
        return False, "no content line - ensure_line first"
    data = _load()
    data["lines"][lid]["warmup_profiles"] = [
        p.strip().lstrip("@") for p in (profiles or []) if p and str(p).strip()
    ]
    _save(data)
    return True, "ok"


def list_lines():
    data = _load()
    out = []
    for lid, line in sorted(data["lines"].items()):
        row = dict(line)
        row["line_id"] = lid
        out.append(row)
    return out


def bootstrap_from_drive_map(map_dict):
    """Import username -> folder_id map into content lines (idempotent)."""
    n = 0
    for u, fid in (map_dict or {}).items():
        if not u or not fid or str(u).startswith("_"):
            continue
        ensure_line(str(u).strip(), str(fid).strip())
        n += 1
    return n
