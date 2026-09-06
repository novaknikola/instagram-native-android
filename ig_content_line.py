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
import time
from datetime import datetime

LINES_PATH = os.path.join(ROOT, 'ig_content_lines.json')
LOCK_PATH = LINES_PATH + ".lock"
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


def _lock(timeout=45.0):
    """Exclusive lock so parallel farm phones don't race .tmp → json replace.

    Nylah/Jazlene/Kianna 2026-09-05: WinError 32 on ig_content_lines.json.tmp
    when 12 devices wrote the same ledger at once → account ERROR/SKIPPED.
    """
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            try:
                os.write(fd, ("%d\n" % os.getpid()).encode("ascii", "replace"))
            except OSError:
                pass
            return fd
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(LOCK_PATH)
                if age > 120:
                    os.remove(LOCK_PATH)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
    raise TimeoutError("ig_content_lines lock timeout")


def _unlock(fd):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def _save(data):
    parent = os.path.dirname(LINES_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Per-PID tmp — never share one .tmp across parallel writers
    tmp = "%s.tmp.%d.%d" % (LINES_PATH, os.getpid(), time.time_ns() % 1000000)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    last_err = None
    for attempt in range(16):
        try:
            os.replace(tmp, LINES_PATH)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
        except OSError as e:
            last_err = e
            time.sleep(0.05 * (attempt + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    raise last_err or PermissionError("ig_content_lines save failed")


def _mutate(fn):
    """Run fn(data) under lock; return data to save, or None to skip save."""
    fd = _lock()
    try:
        data = _load()
        out = fn(data)
        if out is not None:
            _save(out)
        return out
    finally:
        _unlock(fd)


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

    result = {"lid": None, "msg": ""}

    def _do(data):
        existing = data["by_username"].get(u)
        if existing and existing in data["lines"]:
            line = data["lines"][existing]
            if fid and line.get("drive_folder_id") != fid:
                line["drive_folder_id"] = fid
            if warmup_profiles is not None:
                line["warmup_profiles"] = list(warmup_profiles)
            line["active_username"] = u
            line["status"] = "active"
            result["lid"], result["msg"] = existing, "updated"
            return data
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
                    data["by_username"][old] = lid
                data["by_username"][u] = lid
                result["lid"], result["msg"] = lid, "attached_existing_folder"
                return data
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
        result["lid"], result["msg"] = lid, "created"
        return data

    _mutate(_do)
    return result["lid"], result["msg"]


def replace_account(dead_username, new_username, pool_note=""):
    """
    Mark dead username replaced; new username inherits same content line
    (Drive folder + used ledgers). Sticky IP is NOT inherited (new identity).
    """
    dead = (dead_username or "").strip()
    new = (new_username or "").strip()
    if not dead or not new:
        return False, "need dead and new username"
    out = {"ok": False, "lid": None, "err": ""}

    def _do(data):
        lid = data["by_username"].get(dead)
        if not lid or lid not in data["lines"]:
            out["err"] = "no content line for %s" % dead
            return None
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
        data["by_username"][dead] = lid
        out["ok"], out["lid"] = True, lid
        print(
            "[content-line] %s -> %s inherits line=%s folder=%s"
            % (dead, new, lid, line.get("drive_folder_id"))
        )
        return data

    _mutate(_do)
    if out["ok"]:
        return True, out["lid"]
    return False, out["err"] or "replace failed"


def mark_line_status(username, status):
    """status: active | restricted | banned | disabled | dead | paused"""
    lid = line_id_for(username)
    if not lid:
        return False

    def _do(data):
        line = data["lines"].get(lid)
        if not line:
            return None
        line["status"] = (status or "dead").strip().lower()
        line.setdefault("history", []).append(
            {"username": username, "at": _now(), "event": "status:%s" % line["status"]}
        )
        return data

    return _mutate(_do) is not None


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

    def _do(data):
        if lid not in data["lines"]:
            return None
        data["lines"][lid]["warmup_profiles"] = [
            p.strip().lstrip("@") for p in (profiles or []) if p and str(p).strip()
        ]
        return data

    if _mutate(_do) is None:
        return False, "no content line - ensure_line first"
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
