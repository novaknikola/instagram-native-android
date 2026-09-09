# -*- coding: utf-8 -*-
"""Bind first N fresh CSV accounts to IG clones with new sticky IPs. Threads untouched."""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ensure_unique_exits as uex  # noqa: E402
import ig_account_clone as ac  # noqa: E402
import ig_pkg  # noqa: E402
import ig_sticky_ip as sticky  # noqa: E402
import ig_tunnel  # noqa: E402
import proxy_pool as pp  # noqa: E402


def adb_serials():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=30
    ).stdout or ""
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def main():
    print("=== bind fresh IG accounts + sticky (Threads untouched) ===")
    # clear sticky + binds (backup binds)
    bind_path = ROOT / "ig_account_clone.json"
    old = json.load(open(bind_path, encoding="utf-8")) if bind_path.exists() else {"binds": {}}
    bak = ROOT / ("ig_account_clone.json.bak_fresh_%s" % time.strftime("%Y%m%d_%H%M%S"))
    bak.write_text(json.dumps(old, indent=2), encoding="utf-8")
    with open(str(bind_path) + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"binds": {}}, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(str(bind_path) + ".tmp", bind_path)
    sticky._save({})
    print("cleared sticky + binds (bak=%s)" % bak.name)

    accts = list(csv.DictReader(open(ROOT / "Instagram_farm_accounts.csv", encoding="utf-8")))
    all_users = [(r.get("username") or "").strip() for r in accts if (r.get("username") or "").strip()]
    seen = set()
    results = ROOT / "ig_batch_results.csv"
    if results.exists():
        for r in csv.DictReader(open(results, encoding="utf-8")):
            u = (r.get("username") or "").strip()
            if u:
                seen.add(u)
    # Prefer never-tried accounts so CAPTCHA/fail pool is not rebound.
    users = [u for u in all_users if u not in seen] or all_users
    serials = adb_serials()
    print("csv=%d never_tried=%d phones=%d" % (len(all_users), len(users), len(serials)))

    phone_clone = {}
    for s in serials:
        pm = subprocess.run(
            ["adb", "-s", s, "shell", "pm", "list", "packages"],
            capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace",
        ).stdout or ""
        cs = [c for c in ig_pkg.ig_pkgs_from_pm_list(pm) if not ac.is_stock(c)]
        if cs:
            phone_clone[s] = sorted(cs)[0]

    phones = sorted(phone_clone.keys())
    n = min(len(phones), len(users), 20)
    jobs = []
    for i in range(n):
        s = phones[i]
        pkg = phone_clone[s]
        u = users[i]
        ac.assign_new(u, s, pkg)
        tok, cc = sticky.controlled_rotate_token(s, pkg, country="us")
        sticky.bind(s, pkg, tok, cc, "", username=u)
        port = pp.port_for_serial(s)
        if port:
            pp.set_session(port, tok, country=cc, lifetime="24h")
            pp._wire_reverse(s, port)
        jobs.append((s, pkg, u, tok))
        print("BOUND %s -> %s/%s sess=%s" % (u, s[-8:], pkg.split(".")[-1], tok))

    ig_tunnel.enable_many([j[0] for j in jobs], label="fresh-bind")
    ok = 0
    for s, pkg, u, tok in jobs:
        r = uex.ensure_one(s, pkg, username=u, country="us")
        print("EXIT %s ok=%s ip=%s" % (u, r.get("ok"), r.get("exit_ip")))
        if r.get("ok"):
            ok += 1
    print("SUMMARY bound=%d unique_exit_ok=%d/%d spare_csv=%d" % (
        len(jobs), ok, len(jobs), max(0, len(users) - len(jobs))))
    # force-stop IG after bind (no login yet) so apps stay off
    for s, pkg, u, tok in jobs:
        subprocess.run(
            ["adb", "-s", s, "shell", "am", "force-stop", pkg],
            capture_output=True, timeout=20,
        )
    ig_tunnel.disable_many([j[0] for j in jobs], label="post-bind")
    print("IG force-stopped + tun0 down after bind")


if __name__ == "__main__":
    main()
