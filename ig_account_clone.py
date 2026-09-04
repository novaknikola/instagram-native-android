# -*- coding: utf-8 -*-
# One account -> one unique IG clone. Log is the source of truth.
#
# In the log -> use that clone again.
# Not in the log -> clone with the fewest accounts, then write the log.
# Phone off or clone gone -> skip. Do not pick another clone.
from farm_root import ROOT
import os, json, csv, time, collections
import ig_pkg

NATIVE_PKG = ig_pkg.NATIVE_PKG
STOCK = ("android", "androie", "androif")

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = ROOT
if not os.path.isdir(BASE):
    BASE = HERE

BIND_PATH = os.path.join(BASE, "ig_account_clone.json")
LOG_PATH = os.path.join(BASE, "ig_account_clone.log")
RESULTS = os.path.join(BASE, "ig_batch_results.csv")


def log(msg):
    print("[bind] %s" % msg, flush=True)


def key(username):
    return (username or "").strip().lower()


def full_pkg(clone):
    c = (clone or "").strip()
    if not c:
        return ""
    if c.startswith("com.instagram."):
        return c
    return "com.instagram." + c


def is_native(pkg):
    return full_pkg(pkg) == NATIVE_PKG


def is_stock(pkg):
    pkg = full_pkg(pkg)
    if not pkg.startswith("com.instagram.") or "barcel" in pkg:
        return True
    suf = pkg.rsplit(".", 1)[-1]
    return suf in STOCK or pkg == NATIVE_PKG


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _load():
    if not os.path.isfile(BIND_PATH):
        return {"binds": {}}
    try:
        data = json.load(open(BIND_PATH, encoding="utf-8"))
        if not isinstance(data, dict):
            return {"binds": {}}
        data.setdefault("binds", {})
        return data
    except Exception:
        return {"binds": {}}


def _save(data):
    tmp = BIND_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, BIND_PATH)


def _hist(line):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("%s  %s\n" % (_now(), line))


def all_binds():
    """username-key -> bind dict. Empty dict if file missing."""
    return dict((_load().get("binds") or {}))


def lookup(username):
    """Active bind for this username, or None. Seeds from ig_batch_results once."""
    k = key(username)
    if not k:
        return None
    data = _load()
    b = data["binds"].get(k)
    if b and full_pkg(b.get("clone") or "") and not is_stock(b.get("clone") or ""):
        return b
    seeded = _seed_from_results(k, username)
    if seeded:
        data["binds"][k] = seeded
        _save(data)
        _hist("seed  user=%s  clone=%s  serial=%s" % (
            seeded.get("username"), seeded.get("clone"), seeded.get("serial")))
        log("reuse from results %s -> %s" % (
            seeded.get("username"), (seeded.get("clone") or "").split(".")[-1]))
        return seeded
    return None


def _seed_from_results(k, username):
    if not os.path.isfile(RESULTS):
        return None
    last = None
    try:
        with open(RESULTS, encoding="utf-8") as f:
            rows = list(csv.reader(f))
    except Exception:
        return None
    body = rows[1:] if rows and rows[0] and rows[0][0] == "time" else rows
    for r in body:
        if len(r) < 4:
            continue
        u = (r[3] or "").strip()
        if key(u) != k:
            continue
        pkg = full_pkg(r[2] if len(r) > 2 else "")
        serial = (r[1] or "").strip()
        if not pkg or is_stock(pkg):
            continue
        last = {
            "username": u or username,
            "clone": pkg,
            "serial": serial,
            "ts": (r[0] or "").strip() or _now(),
        }
    return last


def loads(data=None):
    data = data or _load()
    c = collections.Counter()
    for b in (data.get("binds") or {}).values():
        pkg = full_pkg(b.get("clone") or "")
        if pkg and not is_stock(pkg):
            c[pkg] += 1
    return c


def assign_new(username, serial, clone):
    """Write bind. Does not overwrite an existing bind."""
    k = key(username)
    pkg = full_pkg(clone)
    if not k or not pkg or is_stock(pkg):
        return None
    data = _load()
    if data["binds"].get(k):
        return data["binds"][k]
    rec = {
        "username": (username or "").strip(),
        "clone": pkg,
        "serial": (serial or "").strip(),
        "ts": _now(),
    }
    data["binds"][k] = rec
    _save(data)
    _hist("new  user=%s  clone=%s  serial=%s" % (rec["username"], pkg, rec["serial"]))
    log("new %s -> %s on %s (n=%d on this clone)" % (
        rec["username"], pkg.split(".")[-1], rec["serial"][-8:], loads(data)[pkg]))
    return rec


def pick(username, live_slots, new_only_serials=None):
    """live_slots: list of (serial, package) unique clones on online phones.

    Returns (serial, package, err). Bound clone must be live — no rematch.
    """
    k = key(username)
    if not k:
        return None, None, "NO_USER"
    live_slots = [(s, full_pkg(p)) for s, p in (live_slots or []) if s and p]
    live_slots = [(s, p) for s, p in live_slots if not is_stock(p)]
    by_pkg = {}
    for s, p in live_slots:
        by_pkg.setdefault(p, []).append(s)

    b = lookup(username)
    if b:
        pkg = full_pkg(b.get("clone") or "")
        serials = by_pkg.get(pkg) or []
        if not serials:
            log("skip %s bound clone %s not on any online phone"
                % ((b.get("username") or username), pkg.split(".")[-1] if pkg else "?"))
            _hist("skip  user=%s  clone=%s  why=CLONE_GONE" % (username, pkg))
            return None, None, "CLONE_GONE"
        want = (b.get("serial") or "").strip()
        if want and want in serials:
            serial = want
        else:
            serial = serials[0]
            if want and want != serial:
                log("skip %s bound phone %s offline (clone is on %s) - not moving"
                    % (username, want[-8:], serial[-8:]))
                _hist("skip  user=%s  clone=%s  why=PHONE_OFFLINE" % (username, pkg))
                return None, None, "PHONE_OFFLINE"
        _hist("reuse  user=%s  clone=%s  serial=%s" % (username, pkg, serial))
        log("reuse %s -> %s" % (username, pkg.split(".")[-1]))
        return serial, pkg, ""

    slots = live_slots
    if new_only_serials:
        allow = set(new_only_serials)
        slots = [(s, p) for s, p in live_slots if s in allow]
    if not slots:
        return None, None, "NO_CLONE"
    n = loads()
    slots.sort(key=lambda sp: (n[sp[1]], sp[1], sp[0]))
    serial, pkg = slots[0]
    assign_new(username, serial, pkg)
    return serial, pkg, ""


if __name__ == "__main__":
    data = _load()
    n = loads(data)
    print("binds=%d  file=%s" % (len(data.get("binds") or {}), BIND_PATH))
    for pkg, c in n.most_common(15):
        print("  %s  accounts=%d" % (pkg.split(".")[-1], c))
