# -*- coding: utf-8 -*-
# uninstall_ig_clones.py — remove Nomix / leftover IG packages; keep Play Store IG.
#
# Keeps:  com.instagram.android
# Leaves: Threads (barcel* / barcelona)
# Removes: every other com.instagram.* (Nomix clones, androie/androif, etc.)
#
#   python -u uninstall_ig_clones.py --dry
#   python -u uninstall_ig_clones.py
from farm_root import ROOT
import subprocess
import sys
import ig_pkg

KEEP = {ig_pkg.NATIVE_PKG}


def sh(*args, timeout=60):
    r = subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    )
    return (r.stdout or "") + (r.stderr or "")


def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def all_ig_pkgs(serial):
    out = sh("adb", "-s", serial, "shell", "pm", "list", "packages", timeout=40)
    pkgs = []
    for ln in (out or "").splitlines():
        if ":" not in ln:
            continue
        pkg = ln.split(":", 1)[1].strip()
        if pkg.startswith("com.instagram."):
            pkgs.append(pkg)
    return sorted(set(pkgs))


def is_threads(pkg):
    return "barcel" in (pkg or "")


def clone_pkgs(pkgs):
    return [p for p in pkgs if p not in KEEP and not is_threads(p)]


def uninstall(serial, pkg):
    """Try user-0 then full uninstall. Returns (ok, detail)."""
    out1 = sh(
        "adb", "-s", serial, "shell", "pm", "uninstall", "--user", "0", pkg,
        timeout=90,
    )
    blob = (out1 or "").lower()
    if "success" in blob:
        return True, "user0 " + (out1 or "").strip()[:80]
    out2 = sh("adb", "-s", serial, "uninstall", pkg, timeout=90)
    blob2 = (out2 or "").lower()
    if "success" in blob2:
        return True, "full " + (out2 or "").strip()[:80]
    return False, ((out1 or "") + " | " + (out2 or "")).strip()[:160]


def main():
    dry = "--dry" in sys.argv
    print("=== Uninstall IG clones (keep %s, leave Threads) ===" % ig_pkg.NATIVE_PKG)
    print("mode: %s" % ("DRY (no uninstall)" if dry else "LIVE"))
    devs = devices()
    print("phones: %d" % len(devs))
    if not devs:
        print("No adb devices. Plug USB / repair_adb.bat")
        sys.exit(1)

    total = kept_native = left_threads = failed = 0
    for d in devs:
        pkgs = all_ig_pkgs(d)
        native = ig_pkg.NATIVE_PKG in pkgs
        threads = [p for p in pkgs if is_threads(p)]
        clones = clone_pkgs(pkgs)
        kept_native += 1 if native else 0
        left_threads += len(threads)
        print("\n%s native=%s threads=%d clones=%d"
              % (d, "YES" if native else "NO", len(threads), len(clones)))
        if not native:
            print("  WARN: Play Store Instagram missing — not installing, only removing clones")
        if threads:
            print("  keep Threads: %s" % ", ".join(p.split(".")[-1] for p in threads[:8]))
        for pkg in clones:
            total += 1
            if dry:
                print("  would uninstall %s" % pkg)
                continue
            ok, detail = uninstall(d, pkg)
            if ok:
                print("  removed %s (%s)" % (pkg, detail))
            else:
                failed += 1
                print("  FAIL %s: %s" % (pkg, detail))

        if not dry:
            left = clone_pkgs(all_ig_pkgs(d))
            if left:
                print("  still present: %s" % ", ".join(left))
            else:
                print("  clones gone")

    print("\n=== done dry=%s clone_targets=%d fail=%d phones_with_native=%d/%d ==="
          % (dry, total, failed, kept_native, len(devs)))
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
