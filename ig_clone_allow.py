# -*- coding: utf-8 -*-
# ig_clone_allow.py - farm uses only probe OPEN_OK clones (serial + pkg).
#
# Source of truth: ig_clone_allow.json (written from clone_probe CSV).
# Missing file or --no-clone-allow / IG_CLONE_ALLOW=0 → all unique clones (old behavior).
#
# Bound account whose clone is not on the list is skipped (not rematched).
from farm_root import ROOT
import csv
import json
import os
from datetime import datetime

ALLOW_PATH = os.path.join(ROOT, "ig_clone_allow.json")


def _env_off():
    v = (os.environ.get("IG_CLONE_ALLOW") or "").strip().lower()
    return v in ("0", "off", "false", "no")


def _norm_pkg(pkg):
    p = (pkg or "").strip()
    if not p:
        return ""
    if p.startswith("com.instagram."):
        return p
    return "com.instagram." + p


def _load_file(path=None):
    path = path or ALLOW_PATH
    if not os.path.isfile(path):
        return None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    pairs = []
    for row in data.get("pairs") or []:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        serial = (row[0] or "").strip()
        pkg = _norm_pkg(row[1])
        if serial and pkg:
            pairs.append((serial, pkg))
    data["_pairs"] = pairs
    return data


def active(path=None):
    if _env_off():
        return False
    data = _load_file(path)
    return bool(data and data.get("_pairs"))


def pairs(path=None):
    data = _load_file(path)
    return list((data or {}).get("_pairs") or [])


def pair_set(path=None):
    return set(pairs(path))


def allows(serial, pkg, path=None):
    serial = (serial or "").strip()
    pkg = _norm_pkg(pkg)
    if not serial or not pkg:
        return False
    return (serial, pkg) in pair_set(path)


def filter_slots(live_slots, path=None):
    """live_slots: [(serial, pkg), ...] → only OPEN_OK pairs when allowlist is on."""
    if not active(path):
        return list(live_slots or [])
    ok = pair_set(path)
    out = []
    for s, p in live_slots or []:
        s = (s or "").strip()
        p = _norm_pkg(p)
        if (s, p) in ok:
            out.append((s, p))
    return out


def count_by_serial(path=None):
    c = {}
    for s, _p in pairs(path):
        c[s] = c.get(s, 0) + 1
    return c


def write_from_probe_csv(csv_path, dest=None, verdict="OPEN_OK"):
    """Build allowlist JSON from clone_probe CSV. Returns (n_pairs, dest)."""
    dest = dest or ALLOW_PATH
    csv_path = os.path.abspath(csv_path)
    seen = []
    have = set()
    with open(csv_path, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if (row.get("verdict") or "").strip() != verdict:
                continue
            serial = (row.get("serial") or "").strip()
            pkg = _norm_pkg(row.get("pkg") or "")
            if not serial or not pkg or (serial, pkg) in have:
                continue
            have.add((serial, pkg))
            seen.append([serial, pkg])
    seen.sort()
    payload = {
        "source": csv_path,
        "verdict": verdict,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "count": len(seen),
        "pairs": seen,
    }
    parent = os.path.dirname(dest)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    tmp = dest + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, dest)
    return len(seen), dest


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    src = None
    for i, x in enumerate(args):
        if x == "--from-probe" and i + 1 < len(args):
            src = args[i + 1]
    if not src:
        print("usage: python -u ig_clone_allow.py --from-probe <probe.csv>")
        sys.exit(2)
    n, dest = write_from_probe_csv(src)
    print("wrote %d OPEN_OK pairs -> %s" % (n, dest))
