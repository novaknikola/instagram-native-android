# -*- coding: utf-8 -*-
# install_ig_unique.py - UNIQUE Instagram Nomix APKs, even across phones.
#
# NEVER install the same APK on two phones (SHARED identity → farm skips).
# Default cap is 1 unique clone per phone (native farm).
#
#   python -u install_ig_unique.py [--dry] [--per-phone 1] [--delete-after]
from farm_root import ROOT
import sys, os, csv, json, glob, subprocess, collections, time
import ig_pkg
import ig_clone_even as even
import ig_account_clone as acc_clone

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
NOMIX = os.path.join(ROOT, "nomix_api")
CLONE_DIR = os.path.join(NOMIX, "downloaded_ig")
STATE_DIRS = [
    os.path.join(NOMIX, "state"),
    r"C:\threads-android\nomix_api\state",
]
THREADS_RESULTS = r"C:\threads-android\batch_results.csv"
if not os.path.isfile(THREADS_RESULTS):
    THREADS_RESULTS = os.path.join(r"C:\farm\threads-farm", "batch_results.csv")
PLACE_CSV = os.path.join(ROOT, "ig_clone_placement.csv")
META_SINCE = "2026-07-17"
MIN_FREE_MB = 400

DRY = "--dry" in sys.argv
DELETE_AFTER = "--delete-after" in sys.argv
FILL = "--fill" in sys.argv
PER = 1
a = sys.argv[1:]
for i, x in enumerate(a):
    if x == "--per-phone" and i + 1 < len(a):
        PER = max(1, int(a[i + 1]))


def sh(*args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def devices():
    out = sh("adb", "devices").stdout or ""
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def pkgs(serial):
    out = sh("adb", "-s", serial, "shell", "pm", "list", "packages").stdout or ""
    return set(ln.split(":", 1)[1].strip() for ln in out.splitlines() if ":" in ln)


def ig_list(serial):
    return ig_pkg.ig_pkgs_from_pm_list(
        sh("adb", "-s", serial, "shell", "pm", "list", "packages").stdout or "")


def data_free_mb(serial):
    out = sh("adb", "-s", serial, "shell", "df", "-k", "/data").stdout or ""
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) >= 4 and "/data" in ln:
            try:
                return int(parts[3]) // 1024
            except ValueError:
                continue
    return None


def threads_wins():
    wins = collections.Counter()
    if not os.path.exists(THREADS_RESULTS):
        return wins
    for r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[7] == "LOGGED_IN" and r[0] >= META_SINCE:
            wins[r[1]] += 1
    return wins


def load_pkg_map():
    pkg_of = {}
    for state in STATE_DIRS:
        for rf in glob.glob(os.path.join(state, "result_*.json")):
            try:
                for a in json.load(open(rf, encoding="utf-8")).get("apps", []):
                    name = (a.get("app_name") or "").replace(" ", "_")
                    aid = a.get("app_id") or ""
                    if aid and ig_pkg.is_ig_farm_pkg(aid):
                        pkg_of[name] = aid
            except Exception as e:
                print("  warn: bad result json %s: %s" % (rf, e))
    return pkg_of


def list_apks():
    if not os.path.isdir(CLONE_DIR):
        return []
    out = []
    for f in os.listdir(CLONE_DIR):
        if not f.lower().endswith(".apk"):
            continue
        path = os.path.join(CLONE_DIR, f)
        if os.path.getsize(path) < 80_000_000:
            print("  skip tiny/partial APK %s" % f)
            continue
        out.append(f[:-4])
    return sorted(out)


def fleet_shared(all_ig):
    cnt = collections.Counter(p for ps in all_ig.values() for p in ps)
    return {p for p, n in cnt.items() if n > 1}


def phone_haves(devs, all_ig, shared, wins):
    rows = []
    for d in sorted(devs, key=lambda s: (-wins.get(s, 0), s)):
        unique = [p for p in all_ig[d]
                  if p not in shared and not acc_clone.is_stock(p)]
        rows.append((d, len(unique), unique))
    return rows


def pick_poorest(counts, targets, skipped):
    elig = [s for s in counts if s not in skipped and counts[s] < targets.get(s, 0)]
    if not elig:
        return None
    return min(elig, key=lambda s: (counts[s], s))


def append_placement(serial, stem, pkg, status):
    new = not os.path.exists(PLACE_CSV)
    with open(PLACE_CSV, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "serial", "apk", "package", "status"])
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), serial, stem, pkg or "", status])


def install_one(serial, apk_path, pkg):
    have = pkgs(serial)
    if pkg and pkg in have:
        return "SKIP_ALREADY", pkg
    if DRY:
        return "DRY", pkg
    before_ig = set(ig_list(serial))
    r = sh("adb", "-s", serial, "install", "-r", apk_path, timeout=180)
    text = (r.stdout or "") + (r.stderr or "")
    if "Success" not in text:
        lines = text.strip().splitlines()
        return (lines[-1] if lines else "FAIL"), pkg
    after_ig = set(ig_list(serial))
    new_ig = sorted(p for p in (after_ig - before_ig) if not acc_clone.is_stock(p))
    if pkg and pkg in after_ig:
        return "OK", pkg
    if new_ig:
        return "OK", new_ig[0]
    all_new = sorted(pkgs(serial) - have)
    ig_new = [p for p in all_new if "instagram" in p.lower() and "barcel" not in p]
    if ig_new:
        return "OK_CHECK_FILTER", ig_new[0]
    return "OK_BUT_PKG_UNKNOWN", pkg


def main():
    mode = "FILL-TO-%d" % PER if FILL else "EVEN (cap %d)" % PER
    print("=== install_ig_unique %s ===" % mode)
    print("CLONE_DIR=%s  DRY=%s  CAP=%d  DELETE_AFTER=%s"
          % (CLONE_DIR, DRY, PER, DELETE_AFTER))
    apks = list_apks()
    pkg_of = load_pkg_map()
    print("result JSON mapped packages: %d" % len(pkg_of))
    for k in sorted(pkg_of)[:6]:
        print("  sample %s -> %s" % (k, pkg_of[k]))
    devs = devices()
    if not devs:
        print("No ADB devices. Plug phones in on the farm PC.")
        sys.exit(1)
    all_ig = {d: ig_list(d) for d in devs}
    shared = fleet_shared(all_ig)
    if shared:
        print("SHARED (will not count as unique): %s"
              % ", ".join(sorted(p.split(".")[-1] for p in shared)))
    wins = threads_wins()
    rows = phone_haves(devs, all_ig, shared, wins)
    haves = [have for _d, have, _u in rows]
    serials = [d for d, _h, _u in rows]

    fleet_pkgs = set()
    for ps in all_ig.values():
        fleet_pkgs |= set(ps)

    usable = []
    for stem in apks:
        pkg = pkg_of.get(stem)
        if not pkg:
            for k, v in pkg_of.items():
                if stem.lower() in k.lower() or k.lower() in stem.lower():
                    pkg = v
                    break
        if pkg and pkg in fleet_pkgs:
            print("  skip %s - package %s already on fleet (would share fingerprint)"
                  % (stem, pkg))
            continue
        if not pkg:
            print("  %s - no JSON map, will detect package after install" % stem)
        usable.append((stem, pkg or ""))

    unused = len(usable)
    if FILL:
        targets_list = [h if h >= PER else PER for h in haves]
        plan = {"even_each": PER, "targets": targets_list}
        print("mode=FILL: each phone filled to %d before leftovers" % PER)
    else:
        plan = even.even_plan(haves, unused, cap=PER)
        targets_list = plan["targets"]
        print("mode=EVEN: %s unique/phone after this pool (cap %d)"
              % (plan.get("even_each"), PER))

    targets = {serials[i]: targets_list[i] for i in range(len(serials))}
    counts = {serials[i]: haves[i] for i in range(len(serials))}
    total_need = sum(max(0, targets[s] - counts[s]) for s in serials)

    print("phones: %d | unused APKs: %d | unique now: %d"
          % (len(devs), unused, sum(haves)))
    print("plan: each phone -> %s unique (cap %d)  adds=%d"
          % (plan.get("even_each"), PER, total_need))
    for d, have, _u in rows:
        need = max(0, targets[d] - have)
        print("  %s  have=%d  target=%d  need+%d" % (d, have, targets[d], need))
    if total_need <= 0:
        print("Nothing to install - fleet already at cap %d." % PER)
        return
    if not apks:
        print("No APKs in %s - run download_ig_from_drive.py --get 20" % CLONE_DIR)
        sys.exit(1)
    if unused < total_need:
        print("WARNING: need %d installs, only %d unused APKs"
              % (total_need, unused))

    used_apks = set()
    ui = 0
    summary = []
    skipped = set()

    def skip_storage(serial, need_left):
        free = data_free_mb(serial)
        want_mb = MIN_FREE_MB + max(1, need_left) * 80
        if free is not None and free < want_mb:
            print("%s SKIP_STORAGE free=%dMB need~%dMB" % (serial, free, want_mb))
            summary.append((serial, "-", "SKIP_STORAGE", str(free)))
            skipped.add(serial)
            return True
        return False

    while ui < len(usable):
        serial = pick_poorest(counts, targets, skipped)
        if not serial:
            break
        need_left = targets[serial] - counts[serial]
        if skip_storage(serial, need_left):
            continue
        stem, pkg = usable[ui]
        ui += 1
        if stem in used_apks:
            summary.append((serial, stem, "BUG_REUSE", pkg or "-"))
            continue
        used_apks.add(stem)
        apk = os.path.join(CLONE_DIR, stem + ".apk")
        status, got = install_one(serial, apk, pkg)
        if got and got in fleet_pkgs and status in ("OK", "OK_CHECK_FILTER"):
            print("%s UNINSTALL shared fingerprint %s" % (serial, got))
            sh("adb", "-s", serial, "uninstall", got, timeout=90)
            status, got = "SHARED_ABORT", got
        print("%s <- %s (%s) -> %s  now=%d/%d" % (
            serial, stem, got or pkg or "?", status,
            counts[serial] + (1 if status in ("OK", "OK_CHECK_FILTER", "DRY", "SKIP_ALREADY") else 0),
            targets[serial]), flush=True)
        append_placement(serial, stem, got or pkg, status)
        summary.append((serial, stem, status, got or pkg or "?"))
        if status in ("OK", "OK_CHECK_FILTER", "DRY", "SKIP_ALREADY"):
            counts[serial] += 1
            if (got or pkg):
                fleet_pkgs.add(got or pkg)
        if DELETE_AFTER and not DRY and status in (
                "OK", "OK_CHECK_FILTER", "SKIP_ALREADY"):
            try:
                os.remove(apk)
            except OSError as e:
                print("  warn: could not delete %s: %s" % (apk, e))

    print("\nSUMMARY")
    ok_n = sum(1 for s in summary if s[2] in ("OK", "OK_CHECK_FILTER", "DRY", "SKIP_ALREADY"))
    print("  installed_or_dry=%d / rows=%d  placement=%s" % (ok_n, len(summary), PLACE_CSV))
    for d in serials:
        print("    %s  %d unique (target %d)" % (d, counts[d], targets[d]))
    for s in summary:
        print("  %s  %s  %s  %s" % s)


if __name__ == "__main__":
    main()
