# -*- coding: utf-8 -*-
"""Preflight: give each phone a unique Floppy/proxy exit IP.

  python -u ensure_unique_exits.py --manifest plan_reel_validate_all_manifest.json
  python -u ensure_unique_exits.py --serials s1,s2,...
  python -u ensure_unique_exits.py --from-adb

Does NOT open Instagram — only sets proxy session + curl exit check + sticky bind.
Requires proxy_pool.py running and NekoBox tunnels up.

Env:
  IG_UNIQUE_EXIT=1          (default on)
  IG_UNIQUE_EXIT_TRIES=10
"""
from farm_root import ROOT
import argparse
import json
import os
import subprocess
import sys
import time

import ig_loop as t
import ig_sticky_ip as sticky
import ig_exit_fleet as exit_fleet
import proxy_pool


def _adb_serials():
    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=30
    ).stdout or ""
    serials = []
    for line in out.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def _jobs_from_manifest(path):
    data = json.load(open(path, encoding="utf-8"))
    jobs = []
    for row in data:
        serial = (row.get("serial") or "").strip()
        clone = (row.get("clone") or "").strip()
        user = (row.get("username") or "").strip()
        if serial and clone:
            jobs.append({"serial": serial, "clone": clone, "username": user})
    return jobs


def _jobs_from_sticky(serials):
    jobs = []
    data = sticky._load()
    want = set(serials) if serials else None
    for k, row in data.items():
        if "|" not in k or not isinstance(row, dict):
            continue
        serial, clone = k.split("|", 1)
        if want is not None and serial not in want:
            continue
        jobs.append({
            "serial": serial,
            "clone": clone,
            "username": (row.get("username") or "").strip(),
        })
    return jobs


def _assert_reverse(serial):
    port = proxy_pool.port_for_serial(serial)
    if port is None:
        return None
    subprocess.run(
        ["adb", "-s", serial, "reverse", "tcp:1080", "tcp:%d" % port],
        capture_output=True, text=True,
    )
    return port


def ensure_one(serial, clone, username="", country="us", max_tries=None):
    """Rotate until this serial has a unique exit. Returns dict result."""
    max_tries = max_tries or exit_fleet.max_rotate_tries()
    t.SERIAL = serial
    port = _assert_reverse(serial)
    if port is None:
        return {
            "serial": serial, "username": username, "ok": False,
            "error": "no_relay_port", "exit_ip": None, "session": None,
        }

    token, sticky_cc, reused = sticky.make_session_token("a0000", serial, clone)
    cc = sticky_cc or country or "us"
    print("\n=== ensure unique exit %s %s (%s) session=%s reused=%s ==="
          % (serial[-8:], username or "-", clone.rsplit(".", 1)[-1], token, reused))

    exit_ip = t.set_proxy_session(token, country=cc)
    if not exit_ip:
        time.sleep(2.0)
        exit_ip = t.set_proxy_session(token, country=cc)
    if not exit_ip:
        return {
            "serial": serial, "username": username, "ok": False,
            "error": "PROXY_DEAD", "exit_ip": None, "session": token,
        }

    for i in range(max_tries):
        ok, holder = exit_fleet.try_claim(
            serial, clone, exit_ip, session=token, username=username)
        if ok:
            sticky.bind(serial, clone, token, cc, exit_ip, username=username)
            print("[ensure] OK %s exit=%s session=%s (rotates=%d)"
                  % (serial[-8:], exit_ip, token, i))
            return {
                "serial": serial, "username": username, "ok": True,
                "error": None, "exit_ip": exit_ip, "session": token,
                "rotates": i,
            }
        who = (holder or {}).get("serial", "?")[-8:]
        print("[ensure] %s exit=%s conflict with %s — rotate %d/%d"
              % (serial[-8:], exit_ip, who, i + 1, max_tries))
        token, cc = sticky.controlled_rotate_token(serial, clone, country=cc)
        exit2 = t.set_proxy_session(token, country=cc)
        if not exit2:
            time.sleep(1.5)
            exit2 = t.set_proxy_session(token, country=cc)
        if not exit2:
            return {
                "serial": serial, "username": username, "ok": False,
                "error": "PROXY_DEAD_AFTER_ROTATE", "exit_ip": None,
                "session": token,
            }
        if exit2 == exit_ip:
            print("[ensure] provider returned SAME exit — continue")
        exit_ip = exit2
        time.sleep(0.6)

    sticky.bind(serial, clone, token, cc, exit_ip, username=username)
    exit_fleet.try_claim(serial, clone, exit_ip, session=token, username=username)
    return {
        "serial": serial, "username": username, "ok": False,
        "error": "UNIQUE_EXIT_FAILED", "exit_ip": exit_ip, "session": token,
    }


def ensure_jobs(jobs, country="us", clear_first=True):
    """Serially ensure unique exits for a list of job dicts."""
    if not exit_fleet.unique_enabled():
        print("[ensure] IG_UNIQUE_EXIT=0 — skip")
        return []
    serials = [j["serial"] for j in jobs]
    if clear_first:
        exit_fleet.clear_claims(serials)
        print("[ensure] cleared prior claims for %d serial(s)" % len(serials))
    results = []
    for j in jobs:
        results.append(ensure_one(
            j["serial"], j["clone"], username=j.get("username") or "",
            country=country,
        ))
    ok_n = sum(1 for r in results if r.get("ok"))
    ips = [r.get("exit_ip") for r in results if r.get("exit_ip")]
    uniq = len(set(ips))
    print("\n[ensure] done ok=%d/%d unique_ips=%d/%d"
          % (ok_n, len(results), uniq, len(ips)))
    for r in results:
        print("  %s  %s  exit=%s  %s" % (
            r["serial"][-8:],
            (r.get("username") or "-")[:18].ljust(18),
            (r.get("exit_ip") or "?"),
            "OK" if r.get("ok") else (r.get("error") or "FAIL"),
        ))
    # Collision report among successes
    by_ip = {}
    for r in results:
        ip = r.get("exit_ip")
        if not ip:
            continue
        by_ip.setdefault(ip, []).append(r)
    shared = {ip: rows for ip, rows in by_ip.items() if len(rows) > 1}
    if shared:
        print("[ensure] SHARED EXITS still present:")
        for ip, rows in shared.items():
            print("  %s <- %s" % (
                ip, ", ".join("%s/%s" % (x["serial"][-8:], x.get("username") or "?")
                              for x in rows)))
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ensure unique proxy exit IP per phone")
    ap.add_argument("--manifest", help="JSON list with serial/clone/username")
    ap.add_argument("--serials", help="Comma-separated serials (use sticky ledger)")
    ap.add_argument("--from-adb", action="store_true", help="All adb devices + sticky")
    ap.add_argument("--country", default="us")
    ap.add_argument("--no-clear", action="store_true",
                    help="Do not clear claims before assigning")
    args = ap.parse_args(argv)

    jobs = []
    if args.manifest:
        path = args.manifest
        if not os.path.isabs(path):
            path = os.path.join(ROOT, path)
        jobs = _jobs_from_manifest(path)
    elif args.serials:
        serials = [s.strip() for s in args.serials.split(",") if s.strip()]
        jobs = _jobs_from_sticky(serials)
    elif args.from_adb:
        jobs = _jobs_from_sticky(_adb_serials())
    else:
        ap.error("pass --manifest, --serials, or --from-adb")

    if not jobs:
        print("[ensure] no jobs")
        return 2

    # Prefer connected phones only
    online = set(_adb_serials())
    jobs = [j for j in jobs if j["serial"] in online]
    if not jobs:
        print("[ensure] none of the targets are adb-online")
        return 2

    try:
        import ig_tunnel
        ig_tunnel.enable_many([j["serial"] for j in jobs], label="ensure-exit")
    except Exception as e:
        print("[ensure] vpn skip: %s" % e)

    results = ensure_jobs(
        jobs, country=args.country, clear_first=not args.no_clear)
    if not results:
        return 1
    if all(r.get("ok") for r in results):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
