# -*- coding: utf-8 -*-
"""NekoBox tun0 — same policy as Threads `device_quiet.py`.

NekoBox ON only while a farm job is running. Idle tun0 still sends the whole
phone through Floppy (Play Store, Chrome, leftover IG).

Threads: enable at run_device start; do not re-kick if tun0 already has inet
(QuickEnable while connected flaps the tunnel — TUNNEL_FLAP.md).
"""
from __future__ import annotations

import concurrent.futures
import subprocess
import time

NEKO = "moe.nb4a"
QUICK = "moe.nb4a/io.nekohasekai.sagernet.ui.QuickEnableShortcut"


def _sh(serial, *args, timeout=25):
    try:
        return subprocess.run(
            ["adb", "-s", serial, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception:
        return None


def vpn_iface_up(serial):
    r = _sh(serial, "shell", "ip", "-o", "addr", "show", "tun0", timeout=12)
    out = ((r.stdout if r else "") or "") + ((r.stderr if r else "") or "")
    return "tun0" in out and "inet " in out


def enable_vpn(serial, force=False, wait_sec=12):
    if not force and vpn_iface_up(serial):
        return True
    _sh(serial, "shell", "am", "start", "-n", QUICK, timeout=25)
    deadline = time.time() + max(3, int(wait_sec))
    while time.time() < deadline:
        if vpn_iface_up(serial):
            return True
        time.sleep(1.2)
    _sh(serial, "shell", "am", "start", "-n", QUICK, timeout=25)
    time.sleep(2.5)
    return vpn_iface_up(serial)


def disable_vpn(serial):
    _sh(serial, "shell", "am", "force-stop", NEKO, timeout=12)
    time.sleep(0.4)
    return not vpn_iface_up(serial)


def enable_many(serials, label="run-start"):
    serials = [s for s in (serials or []) if s]
    if not serials:
        return 0
    print("vpn %s: NekoBox tun0 on %d phone(s) (skip kick if already up)"
          % (label, len(serials)))
    n_ok = 0

    def one(s):
        try:
            return s, bool(enable_vpn(s))
        except Exception as e:
            print("  vpn enable fail %s: %s" % (s[-8:], e))
            return s, False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for s, ok in ex.map(lambda x: one(x), serials):
            if ok:
                n_ok += 1
            else:
                print("  vpn tun0 still down %s" % s[-8:])
    print("vpn %s: tun0 up %d/%d" % (label, n_ok, len(serials)))
    return n_ok


def disable_many(serials, label="run-end"):
    serials = [s for s in (serials or []) if s]
    if not serials:
        return 0
    print("vpn %s: force-stop NekoBox on %d phone(s)" % (label, len(serials)))
    n = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for ok in ex.map(disable_vpn, serials):
            if ok:
                n += 1
    print("vpn %s: tun0 down %d/%d" % (label, n, len(serials)))
    return n
