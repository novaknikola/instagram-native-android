# -*- coding: utf-8 -*-
# check_ig_clones.py - count IG clones on phones + APKs sitting on the farm PC.
#
#   python -u check_ig_clones.py
#   python -u check_ig_clones.py --target 20
from farm_root import ROOT
import os, sys, subprocess, collections
import ig_pkg

TARGET = 1
a = sys.argv[1:]
for i, x in enumerate(a):
    if x == "--target" and i + 1 < len(a):
        TARGET = int(a[i + 1])

NOMIX = os.path.join(ROOT, 'nomix_api')
if not os.path.isdir(NOMIX):
    HERE = os.path.dirname(os.path.abspath(__file__))
    NOMIX = os.path.abspath(os.path.join(HERE, "..", "threads-android", "nomix_api"))
if not os.path.isdir(NOMIX) and os.path.isdir(r"C:\threads-android\nomix_api"):
    NOMIX = r"C:\threads-android\nomix_api"
APK_DIRS = [
    os.path.join(NOMIX, "downloaded_ig"),
    os.path.join(NOMIX, "downloaded_clones"),
]


def sh(*args, timeout=60):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout or ""


def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def pm_list(serial):
    return sh("adb", "-s", serial, "shell", "pm", "list", "packages")


def threads_pkgs(out_text):
    pkgs = []
    for ln in (out_text or "").splitlines():
        if ":" not in ln:
            continue
        pkg = ln.split(":", 1)[1].strip()
        if pkg.startswith("com.instagram.") and "barcel" in pkg:
            pkgs.append(pkg)
    return sorted(pkgs)


def apk_count(folder):
    if not os.path.isdir(folder):
        return 0, 0
    n, bytes_ = 0, 0
    for f in os.listdir(folder):
        if not f.lower().endswith(".apk"):
            continue
        path = os.path.join(folder, f)
        try:
            sz = os.path.getsize(path)
        except OSError:
            continue
        if sz < 100_000_000:
            continue
        n += 1
        bytes_ += sz
    return n, bytes_


def main():
    print("=== Native IG inventory (unique Nomix clone / phone) ===")
    print("Nomix dir: %s" % NOMIX)
    print()
    print("--- APKs on this PC (disk, not installed) ---")
    total_apk, total_b = 0, 0
    for d in APK_DIRS:
        n, b = apk_count(d)
        total_apk += n
        total_b += b
        print("  %s  %d APKs  %.1f GB" % (d, n, b / 1e9 if b else 0))
    print("  TOTAL on disk: %d full APKs  (%.1f GB)" % (total_apk, total_b / 1e9 if total_b else 0))
    print()

    devs = devices()
    print("--- Phones (ADB) ---")
    print("connected: %d" % len(devs))
    if not devs:
        print("No phones. Plug USB / run repair_adb.bat, then this again.")
        sys.exit(1)

    all_ig = {}
    all_th = {}
    for d in devs:
        out = pm_list(d)
        all_ig[d] = ig_pkg.ig_pkgs_from_pm_list(out)
        all_th[d] = threads_pkgs(out)

    cnt = collections.Counter(p for ps in all_ig.values() for p in ps)
    shared = {p for p, n in cnt.items() if n > 1}

    print()
    print("%-24s %5s %5s %5s  %s" % ("SERIAL", "IG-U", "IG-S", "THR", "UNIQUE IG (suffix)"))
    print("-" * 88)
    unique_total = 0
    slots_need = 0
    at_target = 0
    uniq_counts = []
    for d in sorted(devs):
        ig = all_ig[d]
        uniq = [p for p in ig if p not in shared]
        shd = [p for p in ig if p in shared]
        unique_total += len(uniq)
        uniq_counts.append(len(uniq))
        need = max(0, TARGET - len(uniq))
        slots_need += need
        if len(uniq) >= TARGET:
            at_target += 1
        suf = ",".join(p.split(".")[-1] for p in uniq[:4])
        if len(uniq) > 4:
            suf += "+%d" % (len(uniq) - 4)
        print("%-24s %5d %5d %5d  %s" % (
            d, len(uniq), len(shd), len(all_th[d]), suf or "-"))

    print("-" * 88)
    print("unique IG clones on phones (farm can use): %d" % unique_total)
    print("shared IG packages (farm SKIPS):           %d  %s"
          % (len(shared), ", ".join(sorted(p.split(".")[-1] for p in shared)) or "(none)"))
    print("Threads clones on phones (not this densify): %d"
          % sum(len(v) for v in all_th.values()))
    n_phones = max(1, len(devs))
    have_lo = min(uniq_counts) if uniq_counts else 0
    have_hi = max(uniq_counts) if uniq_counts else 0
    even_now = unique_total // n_phones
    even_hi = even_now + (1 if unique_total % n_phones else 0)
    print("spread on phones now: %d–%d unique  (even would be %s)"
          % (have_lo, have_hi, even_now if even_now == even_hi else "%d–%d" % (even_now, even_hi)))
    leftover = max(0, total_apk - unique_total)
    if total_apk:
        try:
            import ig_clone_even as _even
            plan = _even.even_plan(uniq_counts, leftover, cap=TARGET)
            print("APKs on disk %d (≈%d leftover) → even install %s / phone (cap %d)"
                  % (total_apk, leftover, plan.get("even_each"), TARGET))
        except Exception:
            pool_even = leftover // n_phones
            print("APKs on disk %d leftover≈%d → ~%d extra / phone"
                  % (total_apk, leftover, pool_even))
    print("phones at %d unique IG: %d / %d" % (TARGET, at_target, len(devs)))
    print("slots still needed to hit %d/phone: %d" % (TARGET, slots_need))
    print()
    print("Farm uses UNIQUE only. Shared android/androie/androif do not count.")
    print("Install even-spreads unused APKs (install_ig_keep.bat). Not fill-first-to-20.")


if __name__ == "__main__":
    main()
