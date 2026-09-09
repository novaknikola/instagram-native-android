# -*- coding: utf-8 -*-
"""IG-only session wipe: pm clear Nomix IG clones. Never touch Threads barcel*."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ig_pkg  # noqa: E402


def main():
    print("=== IG session wipe ONLY (no Threads, no rebind) ===")
    rec_root = ROOT / "per device recordings"
    nstop = 0
    if rec_root.is_dir():
        for d in rec_root.iterdir():
            if d.is_dir() and (d / "manifest.json").exists() and not (d / "STOP").exists():
                (d / "STOP").write_text("", encoding="utf-8")
                nstop += 1
    print("STOP flags:", nstop)

    ps = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'device_record.py' "
                "-and $_.CommandLine -match 'instagram-native' } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force "
                "-ErrorAction SilentlyContinue; Write-Output $_.ProcessId }"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=90,
    )
    print("killed recorders:", (ps.stdout or "").strip() or "(none)")

    out = subprocess.run(
        ["adb", "devices"], capture_output=True, text=True, timeout=30
    ).stdout or ""
    serials = [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]
    print("phones", len(serials))

    cleared = 0
    for s in serials:
        pm = subprocess.run(
            ["adb", "-s", s, "shell", "pm", "list", "packages"],
            capture_output=True,
            text=True,
            timeout=40,
            encoding="utf-8",
            errors="replace",
        ).stdout or ""
        pkgs = ig_pkg.ig_pkgs_from_pm_list(pm)
        barcel = sum(1 for ln in pm.splitlines() if ":" in ln and "barcel" in ln)
        n = 0
        for pkg in pkgs:
            r = subprocess.run(
                ["adb", "-s", s, "shell", "pm", "clear", pkg],
                capture_output=True,
                text=True,
                timeout=45,
            )
            if "Success" in ((r.stdout or "") + (r.stderr or "")):
                n += 1
                cleared += 1
        print("%s cleared_ig=%d threads_untouched=%d" % (s[-8:], n, barcel))
    print("TOTAL_IG_CLEARED", cleared)
    print("Threads packages listed only — never cleared/stopped/launched")


if __name__ == "__main__":
    main()
