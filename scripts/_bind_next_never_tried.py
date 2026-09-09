# -*- coding: utf-8 -*-
"""Clear sticky IPs, bind next never-tried IG accounts, leave CSV farm-ready.

Does NOT touch Threads. Does NOT start login (caller runs run_ig_farm).
"""
from __future__ import annotations

import csv
import json
import os
import shutil
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

CSV_PATH = ROOT / "Instagram_farm_accounts.csv"
FULL_PATH = ROOT / "Instagram_farm_accounts.all.csv"
COOL_PATH = ROOT / "Instagram_farm_accounts.cooldown.csv"


def adb_serials():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=30
    ).stdout or ""
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def main():
    print("=== clear IPs + bind never-tried IG accounts (Threads untouched) ===")
    ts = time.strftime("%Y%m%d_%H%M%S")

    rows = list(csv.DictReader(open(CSV_PATH, encoding="utf-8")))
    # Prefer all.csv as canonical pool if present
    if FULL_PATH.exists():
        rows = list(csv.DictReader(open(FULL_PATH, encoding="utf-8")))
    else:
        shutil.copy2(CSV_PATH, FULL_PATH)
        print("saved canonical pool ->", FULL_PATH.name)

    seen = set()
    results = ROOT / "ig_batch_results.csv"
    if results.exists():
        for r in csv.DictReader(open(results, encoding="utf-8")):
            u = (r.get("username") or "").strip()
            if u:
                seen.add(u)

    cool = [r for r in rows if (r.get("username") or "").strip() in seen]
    fresh = [r for r in rows if (r.get("username") or "").strip() not in seen]
    print("pool=%d seen=%d fresh=%d" % (len(rows), len(seen), len(fresh)))
    if len(fresh) < 20:
        print("NEED_MORE_ACCOUNTS have=%d need=20" % len(fresh))
        sys.exit(2)

    # Persist cooldown + active never-tried CSV so farm won't re-pick CAPTCHA wave
    bak = ROOT / ("Instagram_farm_accounts.csv.bak_%s" % ts)
    shutil.copy2(CSV_PATH, bak)
    with open(COOL_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["username", "password", "tfa_secret", "model"])
        w.writeheader()
        for r in cool:
            w.writerow({
                "username": (r.get("username") or "").strip(),
                "password": (r.get("password") or "").strip(),
                "tfa_secret": (r.get("tfa_secret") or "").strip(),
                "model": (r.get("model") or "ig").strip() or "ig",
            })
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["username", "password", "tfa_secret", "model"])
        w.writeheader()
        for r in fresh:
            w.writerow({
                "username": (r.get("username") or "").strip(),
                "password": (r.get("password") or "").strip(),
                "tfa_secret": (r.get("tfa_secret") or "").strip(),
                "model": (r.get("model") or "ig").strip() or "ig",
            })
    print("active CSV = %d never-tried; cooldown=%d bak=%s" % (
        len(fresh), len(cool), bak.name))

    # Clear sticky + proxy session files + binds
    bind_path = ROOT / "ig_account_clone.json"
    old = json.load(open(bind_path, encoding="utf-8")) if bind_path.exists() else {"binds": {}}
    bak_b = ROOT / ("ig_account_clone.json.bak_%s" % ts)
    bak_b.write_text(json.dumps(old, indent=2), encoding="utf-8")
    with open(str(bind_path) + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"binds": {}}, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(str(bind_path) + ".tmp", bind_path)
    sticky._save({})
    # wipe proxy_state session_* so relays don't keep burned sticky tokens
    ps = ROOT / "proxy_state"
    n_sess = 0
    if ps.is_dir():
        for p in ps.glob("session_*.txt"):
            p.write_text("us|cleared_%s|24h\n" % ts, encoding="utf-8")
            n_sess += 1
    print("cleared sticky+binds; reset session files=%d" % n_sess)

    users = [(r.get("username") or "").strip() for r in fresh]
    serials = adb_serials()
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
    n = min(20, len(phones), len(users))
    jobs = []
    for i in range(n):
        s, pkg, u = phones[i], phone_clone[phones[i]], users[i]
        ac.assign_new(u, s, pkg)
        tok, cc = sticky.controlled_rotate_token(s, pkg, country="us")
        sticky.bind(s, pkg, tok, cc, "", username=u)
        port = pp.port_for_serial(s)
        if port:
            pp.set_session(port, tok, country=cc, lifetime="24h")
            pp._wire_reverse(s, port)
        jobs.append((s, pkg, u, tok))
        print("BOUND %s -> %s/%s %s" % (u, s[-8:], pkg.split(".")[-1], tok))

    ig_tunnel.enable_many([j[0] for j in jobs], label="clear-ip-bind")
    ok = 0
    for s, pkg, u, tok in jobs:
        r = uex.ensure_one(s, pkg, username=u, country="us")
        print("EXIT %s ok=%s ip=%s" % (u, r.get("ok"), r.get("exit_ip")))
        if r.get("ok"):
            ok += 1
    print("SUMMARY bound=%d unique_exit_ok=%d/%d" % (len(jobs), ok, len(jobs)))
    for s, pkg, u, tok in jobs:
        subprocess.run(
            ["adb", "-s", s, "shell", "am", "force-stop", pkg],
            capture_output=True, timeout=20,
        )
    ig_tunnel.disable_many([j[0] for j in jobs], label="post-bind")
    print("IG force-stopped + tun0 down; ready for login-only farm")


if __name__ == "__main__":
    main()
