# -*- coding: utf-8 -*-
# ig_pkg.py - shared Instagram package detection for farm / gap / install.
#
# Nomix Clone Index 900+ Instagram APKs may NOT use the old "androi*" suffix.
# Rule: any com.instagram.* except Threads barcel* / barcelona.
NATIVE_PKG = "com.instagram.android"


def is_ig_farm_pkg(pkg):
    pkg = (pkg or "").strip()
    if not pkg.startswith("com.instagram."):
        return False
    if "barcel" in pkg:  # Threads Nomix (barcel*) + stock barcelona
        return False
    return True


def native_installed(pkgs):
    return NATIVE_PKG in (pkgs or [])


def ig_pkgs_from_pm_list(out_text):
    """Parse `pm list packages` stdout -> sorted IG farm packages."""
    pkgs = []
    for ln in (out_text or "").splitlines():
        if ":" not in ln:
            continue
        pkg = ln.split(":", 1)[1].strip()
        if is_ig_farm_pkg(pkg):
            pkgs.append(pkg)
    return sorted(pkgs)


def force_stop_ig_farm(serial):
    """Stop IG clones on this phone so CDN/chat die. Session data stays (not pm clear).

    Threads barcel* left alone. Used after a job and on idle phones so Floppydata
    is not billed while Instagram sits in the foreground.
    """
    import subprocess
    serial = (serial or "").strip()
    if not serial:
        return 0
    try:
        out = subprocess.run(
            ["adb", "-s", serial, "shell", "pm", "list", "packages"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
        ).stdout or ""
    except Exception as e:
        print("[proxy-save] pm list fail %s: %s" % (serial[-8:], e))
        return 0
    pkgs = ig_pkgs_from_pm_list(out)
    n = 0
    for pkg in pkgs:
        try:
            subprocess.run(
                ["adb", "-s", serial, "shell", "am", "force-stop", pkg],
                capture_output=True, text=True, timeout=20,
            )
            n += 1
        except Exception:
            pass
    if n:
        print("[proxy-save] force-stop %d IG clone(s) on %s (login session kept)"
              % (n, serial[-8:]))
    return n


def quiet_idle_ig(keep_serials=None):
    """Force-stop IG on connected phones not in keep_serials.

    keep_serials=None or [] → quiet the whole fleet (cold start).
    keep_serials=[planned…] → only stop phones NOT in this run.
    """
    import subprocess
    keep = set(keep_serials or [])
    try:
        out = subprocess.run(
            ["adb", "devices"], capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        ).stdout or ""
    except Exception as e:
        print("[proxy-save] adb devices fail: %s" % e)
        return 0
    serials = [
        ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln
    ]
    n = 0
    for s in serials:
        if keep and s in keep:
            continue
        n += force_stop_ig_farm(s)
    return n


def farm_process_active():
    """True if any IG farm runner is active (farm, device, or schedule)."""
    return _process_match("run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler")


def ig_posting_active():
    """True if a batch or device post is in flight (not scheduler-only)."""
    return _process_match("run_ig_farm|run_ig_device")


def _process_match(pattern):
    import subprocess
    import sys
    try:
        if sys.platform.startswith("win"):
            cmd = (
                "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match '%s' } | "
                "Measure-Object).Count" % pattern
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command", cmd],
                capture_output=True, text=True, timeout=12,
                encoding="utf-8", errors="replace",
            ).stdout.strip()
            try:
                return int(out.splitlines()[-1] if out else "0") > 0
            except ValueError:
                return False
        out = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True, text=True, timeout=8,
        ).stdout or ""
        return bool(out.strip())
    except Exception:
        return False

