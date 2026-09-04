# -*- coding: utf-8 -*-
# ig_clone_gap.py - IG Nomix capacity audit (the real volume blocker).
#
# Shows which phones already have UNIQUE com.instagram.androi* packages,
# which packages are SHARED (skipped by the farm), and which phones need
# a fresh unique install. Prefers Threads-healthy phones for the install queue.
#
#   python -u ig_clone_gap.py
#   python -u ig_clone_gap.py --target 20
#   python -u ig_clone_gap.py --json
#
# Run on Windows farm PC with phones attached.
import os
from farm_root import ROOT
import sys, csv, json, subprocess, collections, os
import ig_pkg

THREADS_RESULTS = os.path.join(ROOT, 'batch_results.csv')
IG_RESULTS = os.path.join(ROOT, 'ig_batch_results.csv')
META_SINCE = "2026-07-17"

def sh(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=60).stdout or ""

def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]

def ig_pkgs(serial):
    return ig_pkg.ig_pkgs_from_pm_list(
        sh("adb", "-s", serial, "shell", "pm", "list", "packages"))

def threads_wins():
    wins = collections.Counter()
    if not os.path.exists(THREADS_RESULTS):
        return wins
    for r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[7] == "LOGGED_IN" and r[0] >= META_SINCE:
            wins[r[1]] += 1
    return wins

def meta_denylist():
    bad = set()
    if not os.path.exists(IG_RESULTS):
        return bad
    for r in csv.reader(open(IG_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[7] == "LOGIN_META_ERROR" and r[1]:
            bad.add(r[1])
    return bad

def main():
    want_json = "--json" in sys.argv
    target = 1
    a = sys.argv[1:]
    for i, x in enumerate(a):
        if x == "--target" and i + 1 < len(a):
            target = int(a[i + 1])
    devs = devices()
    if not devs:
        print("No ADB devices. Fix USB / adb first."); sys.exit(1)

    all_pkgs = {d: ig_pkgs(d) for d in devs}
    cnt = collections.Counter(p for pkgs in all_pkgs.values() for p in pkgs)
    shared = {p for p, n in cnt.items() if n > 1}
    wins = threads_wins()
    meta_bad = meta_denylist()

    rows = []
    for d in sorted(devs, key=lambda s: (-wins.get(s, 0), s)):
        pkgs = all_pkgs[d]
        unique = [p for p in pkgs if p not in shared]
        rows.append({
            "serial": d,
            "threads_wins": wins.get(d, 0),
            "meta_denylist": d in meta_bad,
            "ig_all": [p.split(".")[-1] for p in pkgs],
            "ig_unique": [p.split(".")[-1] for p in unique],
            "needs_unique": len(unique) < target,
            "unique_n": len(unique),
            "need_n": max(0, target - len(unique)),
            "ok_for_install": len(unique) < target,
        })

    need = [r for r in rows if r["ok_for_install"]]
    have = [r for r in rows if r["unique_n"] >= target]
    blocked = [r for r in rows if r["meta_denylist"] and r["unique_n"] == 0]

    if want_json:
        print(json.dumps({
            "phones": len(devs),
            "shared_packages": sorted(p.split(".")[-1] for p in shared),
            "target": target,
            "phones_at_target": len(have),
            "phones_needing_unique": len(need),
            "slots_needed": sum(r["need_n"] for r in rows),
            "install_queue": [r["serial"] for r in need],
            "rows": rows,
        }, indent=2))
        return

    print("=== IG clone capacity gap (target=%d unique/phone) ===" % target)
    print("phones connected: %d" % len(devs))
    print("SHARED packages (farm skips): %s"
          % (", ".join(sorted(p.split(".")[-1] for p in shared)) or "(none)"))
    print("phones at target: %d / %d" % (len(have), len(devs)))
    print("phones below target: %d  |  slots still needed: %d"
          % (len(need), sum(r["need_n"] for r in rows)))
    if blocked:
        print("META denylist + 0 unique (still install clones): %s"
              % ", ".join(r["serial"] for r in blocked))
    print()
    print("%-24s %5s %6s %5s %-20s %s" % ("SERIAL", "WINS", "META", "HAVE", "UNIQUE IG", "STATUS"))
    print("-" * 96)
    for r in rows:
        n = r["unique_n"]
        if n >= target:
            status = "AT_TARGET"
        elif n == 0:
            status = "NEED_INSTALL"
        else:
            status = "NEED+%d" % r["need_n"]
        print("%-24s %5d %6s %5d %-20s %s" % (
            r["serial"], r["threads_wins"],
            "YES" if r["meta_denylist"] else "",
            n,
            ",".join(r["ig_unique"][:3]) + ("+" if n > 3 else "") or "-",
            status))
    print()
    slots = sum(r["need_n"] for r in rows)
    print("NEXT: generate %d fresh Instagram Nomix APKs (unique Clone Index)," % slots)
    print("      install ONE APK per slot. Never copy the same APK to a second phone.")
    print("      python -u densify_ig.py --generate   then   --install --per-phone %d" % target)
    print("See IG_RUNBOOK.md § Clone density.")

if __name__ == "__main__":
    main()
