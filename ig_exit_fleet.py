# -*- coding: utf-8 -*-
"""Fleet exit-IP claims — keep phones off the same public IP.

Instagram treats many clones on one residential exit as one network (spam /
disabled Share → POST_TIMEOUT). Each serial claims its live exit; conflicts
force a sticky session rotate until unique (or tries exhausted).

Ledger: ig_exit_claims.json
  { "<serial>": {
      "exit_ip": "...", "session": "...", "clone": "...",
      "username": "...", "updated": "..."
    }, ... }
"""
from farm_root import ROOT
import json
import os
import time
from datetime import datetime

CLAIMS_PATH = os.path.join(ROOT, "ig_exit_claims.json")
LOCK_PATH = CLAIMS_PATH + ".lock"
# Sticky last_exit older than this is ignored for conflict (stale history).
STICKY_FRESH_SEC = int(os.environ.get("IG_EXIT_STICKY_FRESH_SEC") or str(36 * 3600))


def _load():
    if not os.path.exists(CLAIMS_PATH):
        return {}
    try:
        with open(CLAIMS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data):
    parent = os.path.dirname(CLAIMS_PATH)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = CLAIMS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, CLAIMS_PATH)


def _lock(timeout=45.0):
    """Exclusive lock via O_EXCL (Windows + POSIX)."""
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
                if age > 180:
                    os.remove(LOCK_PATH)
                    continue
            except OSError:
                pass
            time.sleep(0.08)
    raise TimeoutError("ig_exit_fleet lock timeout")


def _unlock(fd):
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def _norm_ip(ip):
    return (ip or "").strip().lower()


def _parse_iso(ts):
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "")).timestamp()
    except Exception:
        return 0.0


def sticky_peer_exits(exclude_serial="", fresh_sec=None):
    """Recent last_exit from ig_sticky_ip.json for other serials."""
    fresh_sec = STICKY_FRESH_SEC if fresh_sec is None else int(fresh_sec)
    out = {}
    try:
        import ig_sticky_ip as sticky
        data = sticky._load()
    except Exception:
        return out
    now = time.time()
    ex = (exclude_serial or "").strip()
    for k, row in (data or {}).items():
        if not isinstance(row, dict):
            continue
        serial = (k.split("|", 1)[0] if "|" in k else "").strip()
        if not serial or serial == ex:
            continue
        ip = _norm_ip(row.get("last_exit"))
        if not ip:
            continue
        age = now - _parse_iso(row.get("updated"))
        if fresh_sec > 0 and age > fresh_sec:
            continue
        # Prefer newest holder for this IP
        prev = out.get(ip)
        if prev is None or _parse_iso(row.get("updated")) >= _parse_iso(prev.get("updated")):
            out[ip] = {
                "serial": serial,
                "clone": (k.split("|", 1)[1] if "|" in k else ""),
                "username": row.get("username") or "",
                "session": row.get("session") or "",
                "updated": row.get("updated") or "",
                "source": "sticky",
            }
    return out


def claimed_peer_exits(exclude_serial=""):
    """Live claims from ig_exit_claims.json for other serials."""
    out = {}
    ex = (exclude_serial or "").strip()
    for serial, row in _load().items():
        if not isinstance(row, dict) or serial == ex:
            continue
        ip = _norm_ip(row.get("exit_ip"))
        if not ip:
            continue
        out[ip] = {
            "serial": serial,
            "clone": row.get("clone") or "",
            "username": row.get("username") or "",
            "session": row.get("session") or "",
            "updated": row.get("updated") or "",
            "source": "claim",
        }
    return out


def peer_exits(exclude_serial=""):
    """Union of claim + fresh sticky exits held by other phones."""
    peers = sticky_peer_exits(exclude_serial)
    peers.update(claimed_peer_exits(exclude_serial))
    return peers


def holder_for(exit_ip, exclude_serial=""):
    ip = _norm_ip(exit_ip)
    if not ip:
        return None
    return peer_exits(exclude_serial).get(ip)


def try_claim(serial, clone, exit_ip, session="", username=""):
    """Claim exit for this serial. Returns (ok, holder_or_None).

    ok=True means this serial now owns exit_ip exclusively in the fleet claim
    file (and it was not already held by another phone).
    """
    serial = (serial or "").strip()
    ip = _norm_ip(exit_ip)
    if not serial or not ip:
        return False, None
    fd = _lock()
    try:
        holder = holder_for(ip, exclude_serial=serial)
        if holder:
            return False, holder
        data = _load()
        data[serial] = {
            "exit_ip": ip,
            "session": session or "",
            "clone": clone or "",
            "username": (username or "").strip(),
            "updated": datetime.now().isoformat(timespec="seconds"),
        }
        _save(data)
        return True, None
    finally:
        _unlock(fd)


def release(serial):
    serial = (serial or "").strip()
    if not serial:
        return
    fd = _lock()
    try:
        data = _load()
        if serial in data:
            del data[serial]
            _save(data)
    finally:
        _unlock(fd)


def clear_claims(serials=None):
    """Drop claims for given serials, or all claims if serials is None."""
    fd = _lock()
    try:
        if serials is None:
            _save({})
            return
        want = set((s or "").strip() for s in serials if (s or "").strip())
        data = _load()
        for s in list(data.keys()):
            if s in want:
                del data[s]
        _save(data)
    finally:
        _unlock(fd)


def summary_lines():
    rows = []
    data = _load()
    for serial in sorted(data.keys()):
        row = data[serial]
        rows.append(
            "%s  exit=%-18s  user=%s  session=%s"
            % (serial[-8:], row.get("exit_ip") or "?",
               row.get("username") or "-", row.get("session") or "-")
        )
    return rows


def unique_enabled():
    v = (os.environ.get("IG_UNIQUE_EXIT") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def max_rotate_tries():
    try:
        return max(1, int(os.environ.get("IG_UNIQUE_EXIT_TRIES") or "10"))
    except Exception:
        return 10
