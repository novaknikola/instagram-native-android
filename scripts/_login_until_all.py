# -*- coding: utf-8 -*-
"""Keep LOGGED_IN phones; rebind never-tried + fresh sticky on the rest; login-only.

Loops until 20 LOGGED_IN or stock empty. Threads untouched. IG force-stopped after.
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

FULL = ROOT / "Instagram_farm_accounts.all.csv"
CSV = ROOT / "Instagram_farm_accounts.csv"
RESULTS = ROOT / "ig_batch_results.csv"
BIND = ROOT / "ig_account_clone.json"


def adb_serials():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=30
    ).stdout or ""
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def last_login_by_user():
    last = {}
    if not RESULTS.exists():
        return last
    for r in csv.DictReader(open(RESULTS, encoding="utf-8")):
        u = (r.get("username") or "").strip()
        if u:
            last[u] = (r.get("login") or "").strip()
    return last


def load_pool():
    path = FULL if FULL.exists() else CSV
    return list(csv.DictReader(open(path, encoding="utf-8")))


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["username", "password", "tfa_secret", "model"])
        w.writeheader()
        for r in rows:
            w.writerow({
                "username": (r.get("username") or "").strip(),
                "password": (r.get("password") or "").strip(),
                "tfa_secret": (r.get("tfa_secret") or "").strip(),
                "model": (r.get("model") or "ig").strip() or "ig",
            })


def phone_unique_clone(serial):
    pm = subprocess.run(
        ["adb", "-s", serial, "shell", "pm", "list", "packages"],
        capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace",
    ).stdout or ""
    cs = [c for c in ig_pkg.ig_pkgs_from_pm_list(pm) if not ac.is_stock(c)]
    return sorted(cs)[0] if cs else ""


def quiet_ig():
    ig_pkg.quiet_idle_ig(keep_serials=None)


def run_login(serials, log_dir):
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "phones").mkdir(exist_ok=True)
    farm_log = log_dir / "farm_master.log"
    env = os.environ.copy()
    env["IG_VISION"] = "0"
    env["IG_STEP_WATCH"] = "1"
    env["IG_STEP_WATCH_VISION"] = "0"
    env["IG_ERROR_SHOT_DIR"] = str(log_dir)
    env["IG_RUN_LOG"] = str(farm_log)
    env["IG_PHONE_LOG_DIR"] = str(log_dir / "phones")
    cmd = [
        sys.executable, "-u", "run_ig_farm.py",
        "--login-only", "--allow-unproven", "--allow-cold",
        "--stagger", "5", "--max-inflight", "2", "--no-record",
        "--devices", ",".join(serials),
    ]
    print("LOGIN", " ".join(cmd))
    print("LOG_DIR", log_dir)
    with open(farm_log, "w", encoding="utf-8") as fh:
        p = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT)
    return p.returncode


def summarize(serials):
    last = last_login_by_user()
    data = (json.load(open(BIND, encoding="utf-8")).get("binds") or {})
    by_serial = {}
    for b in data.values():
        s = (b.get("serial") or "").strip()
        u = (b.get("username") or "").strip()
        if s and u:
            by_serial[s] = u
    ok, bad = [], []
    for s in serials:
        u = by_serial.get(s, "")
        lg = last.get(u, "")
        if lg == "LOGGED_IN":
            ok.append((s, u))
        else:
            bad.append((s, u, lg or "?"))
    return ok, bad


def wave(need_serials, fresh_users, wave_n):
    ts = time.strftime("%Y%m%d_%H%M%S")
    print("\n===== WAVE %d need=%d stock=%d =====" % (wave_n, len(need_serials), len(fresh_users)))
    if len(fresh_users) < len(need_serials):
        print("STOCK_SHORT have=%d need=%d" % (len(fresh_users), len(need_serials)))
        return False

    # rebuild binds: keep LOGGED_IN only, then add new
    last = last_login_by_user()
    old = json.load(open(BIND, encoding="utf-8")) if BIND.exists() else {"binds": {}}
    bak = ROOT / ("ig_account_clone.json.bak_loop_%s" % ts)
    bak.write_text(json.dumps(old, indent=2), encoding="utf-8")
    keep = {}
    for k, b in (old.get("binds") or {}).items():
        u = (b.get("username") or "").strip()
        if last.get(u) == "LOGGED_IN":
            keep[k] = b
    with open(str(BIND) + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"binds": keep}, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(str(BIND) + ".tmp", BIND)
    print("kept LOGGED_IN binds=%d" % len(keep))

    # clear sticky only for phones we will rebind
    st = sticky._load()
    for s in need_serials:
        for k in list(st.keys()):
            if k.startswith(s + "|"):
                del st[k]
    sticky._save(st)
    for s in need_serials:
        port = pp.port_for_serial(s)
        if port:
            p = ROOT / "proxy_state" / ("session_%d.txt" % port)
            if p.exists():
                p.write_text("us|wave%d_%s|24h\n" % (wave_n, ts), encoding="utf-8")

    jobs = []
    for i, s in enumerate(sorted(need_serials)):
        pkg = phone_unique_clone(s)
        if not pkg:
            print("NO_CLONE", s)
            continue
        u = fresh_users[i]
        ac.assign_new(u, s, pkg)
        tok, cc = sticky.controlled_rotate_token(s, pkg, country="us")
        sticky.bind(s, pkg, tok, cc, "", username=u)
        port = pp.port_for_serial(s)
        if port:
            pp.set_session(port, tok, country=cc, lifetime="24h")
            pp._wire_reverse(s, port)
        jobs.append((s, pkg, u))
        print("BOUND %s -> %s/%s" % (u, s[-8:], pkg.split(".")[-1]))

    # active CSV = kept logged-in rows + this wave users (so farm matches binds)
    pool = { (r.get("username") or "").strip(): r for r in load_pool() }
    active_users = []
    for b in keep.values():
        u = (b.get("username") or "").strip()
        if u in pool:
            active_users.append(pool[u])
    for s, pkg, u in jobs:
        if u in pool:
            active_users.append(pool[u])
    write_csv(active_users, CSV)

    ig_tunnel.enable_many([j[0] for j in jobs], label="wave-%d" % wave_n)
    ok_ex = 0
    for s, pkg, u in jobs:
        r = uex.ensure_one(s, pkg, username=u, country="us")
        print("EXIT %s ok=%s ip=%s" % (u, r.get("ok"), r.get("exit_ip")))
        if r.get("ok"):
            ok_ex += 1
    print("unique_exit_ok=%d/%d" % (ok_ex, len(jobs)))
    quiet_ig()

    log_dir = ROOT / "logs" / ("login_loop_w%d_%s" % (wave_n, ts))
    open(ROOT / "logs" / "LAST_LOGIN_FRESH.txt", "w", encoding="utf-8").write(str(log_dir))
    rc = run_login([j[0] for j in jobs], log_dir)
    quiet_ig()
    ig_tunnel.disable_many(adb_serials(), label="post-wave-%d" % wave_n)
    print("wave %d farm_rc=%d" % (wave_n, rc))
    return True


def main():
    if not FULL.exists() and CSV.exists():
        shutil.copy2(CSV, FULL)
    serials = adb_serials()
    if len(serials) < 20:
        print("phones=%d expected 20" % len(serials))
    wave_n = 0
    while True:
        wave_n += 1
        last = last_login_by_user()
        # determine current success by latest bind-intent: scan sticky usernames
        st = sticky._load()
        ok_serials = set()
        for k, row in st.items():
            if "|" not in k:
                continue
            s = k.split("|", 1)[0]
            u = (row.get("username") or "").strip()
            if last.get(u) == "LOGGED_IN":
                ok_serials.add(s)
        # also check binds
        binds = (json.load(open(BIND, encoding="utf-8")).get("binds") or {}) if BIND.exists() else {}
        for b in binds.values():
            u = (b.get("username") or "").strip()
            s = (b.get("serial") or "").strip()
            if s and last.get(u) == "LOGGED_IN":
                ok_serials.add(s)

        need = [s for s in sorted(serials) if s not in ok_serials]
        print("STATUS logged_phones=%d/%d need=%d" % (len(ok_serials), len(serials), len(need)))
        if not need:
            print("ALL_PHONES_LOGGED_IN")
            quiet_ig()
            return 0

        seen = set(last.keys())
        pool = load_pool()
        fresh = [
            (r.get("username") or "").strip()
            for r in pool
            if (r.get("username") or "").strip() and (r.get("username") or "").strip() not in seen
        ]
        # also exclude currently sticky LOGGED_IN usernames
        print("never_tried_stock=%d" % len(fresh))
        if not wave(need, fresh, wave_n):
            print("STOPPED_OUT_OF_STOCK logged=%d/%d" % (len(ok_serials), len(serials)))
            quiet_ig()
            return 2
        if wave_n >= 10:
            print("STOPPED_MAX_WAVES")
            return 3


if __name__ == "__main__":
    sys.exit(main() or 0)
