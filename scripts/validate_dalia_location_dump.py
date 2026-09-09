# -*- coding: utf-8 -*-
"""Local validation: Dalia location-picker POST_TIMEOUT dump (no device/post).

Uses dumps/reel_Daliatydfh256_20260906_130815_share_timeout.xml — Select a
location / Location Services overlay. Asserts detect + dismiss Close (never
Turn on Location Services), and still_in_composer stays True.
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

DUMP = os.path.join(
    ROOT,
    "dumps",
    "reel_Daliatydfh256_20260906_130815_share_timeout.xml",
)


def main():
    if not os.path.isfile(DUMP):
        print("FAIL missing dump:", DUMP)
        return 1
    xml = open(DUMP, encoding="utf-8").read()
    fails = []

    if not t._is_location_picker_sheet(xml):
        fails.append("detector missed location picker")

    if not t._still_in_composer(xml, fmt="reel"):
        fails.append("still_in_composer False on location sheet")

    taps = []
    orig_tapn = t.tapn
    orig_adb = t.adb
    orig_dump = t.dump

    def _fake_tapn(n, label=""):
        taps.append(label)
        return True

    def _fake_adb(*args):
        taps.append("adb:" + " ".join(str(a) for a in args[:4]))
        return ""

    t.tapn = _fake_tapn
    t.adb = _fake_adb
    t.dump = lambda timeout=8, attempts=2: xml  # noqa: E731

    try:
        taps[:] = []
        ok = t._dismiss_location_picker(xml)
        if not ok:
            fails.append("dismiss_location_picker returned False")
        if not any("location-upsell-close" in x or "location-picker-cancel" in x
                   for x in taps):
            fails.append("dismiss did not tap close/cancel: %r" % (taps,))
        if any("ls_action" in x or "turn on" in x.lower() for x in taps):
            fails.append("dismiss tapped Turn on Location Services: %r" % (taps,))

        # Chrome path should also clear location first
        taps[:] = []
        chrome = t._dismiss_composer_chrome(xml, fmt="reel")
        if not chrome:
            fails.append("dismiss_composer_chrome missed location sheet")
    finally:
        t.tapn = orig_tapn
        t.adb = orig_adb
        t.dump = orig_dump

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS dalia location-picker dump validation")
    print("  detector=Select a location")
    print("  dismiss=Close/Cancel (not Turn on Location Services)")
    print("  still_in_composer=True")
    print("  composer_chrome=clears location")
    return 0


if __name__ == "__main__":
    sys.exit(main())
