# -*- coding: utf-8 -*-
"""Offline validation: Your-algorithm interests screen dismiss (sage / pecntiag).

2026-09-09 share_timeout dumps landed on Reels 'Your algorithm' interests UI
(not caption). Detector must recognize it; dismiss must tap action_bar Back
only — never Add / Options / Share.
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

SAGE = os.path.join(
    ROOT, "dumps", "reel_sage85948_20260909_051550_share_timeout.xml")
PECN = os.path.join(
    ROOT, "dumps", "reel_pecntiag145_20260909_051606_share_timeout.xml")
# Prior caption dump must NOT match (location-chip still_caption).
SAGE_CAPTION = os.path.join(
    ROOT, "dumps", "reel_sage85948_20260908_150623_still_caption.xml")


def _check_algo_dump(path, label):
    fails = []
    if not os.path.isfile(path):
        return ["missing dump: %s" % path]
    xml = open(path, encoding="utf-8").read()

    if not t._is_your_algorithm_screen(xml):
        fails.append("%s: Your algorithm screen not detected" % label)
    if t._is_caption_composer(xml):
        fails.append("%s: wrongly treated as caption composer" % label)
    if t._reel_at_caption(xml):
        fails.append("%s: wrongly treated as reel caption" % label)

    taps = []
    orig_tapn, orig_adb = t.tapn, t.adb

    def _fake_tapn(n, label=""):
        taps.append(label)
        return True

    def _fake_adb(*a, **k):
        taps.append("adb:%s" % ",".join(str(x) for x in a[:4]))
        return ""

    t.tapn = _fake_tapn
    t.adb = _fake_adb
    try:
        ok = t._dismiss_your_algorithm_screen(xml)
    finally:
        t.tapn, t.adb = orig_tapn, orig_adb

    if not ok:
        fails.append("%s: dismiss returned False" % label)
    if "algorithm-back" not in taps:
        fails.append("%s: expected algorithm-back tap, got %r" % (label, taps))
    if any("Add" in x or "Options" in x or "share" in x.lower() for x in taps):
        fails.append("%s: dismiss tapped wrong target %r" % (label, taps))
    return fails


def main():
    fails = []
    fails.extend(_check_algo_dump(SAGE, "sage"))
    fails.extend(_check_algo_dump(PECN, "pecntiag"))

    if os.path.isfile(SAGE_CAPTION):
        xml = open(SAGE_CAPTION, encoding="utf-8").read()
        if t._is_your_algorithm_screen(xml):
            fails.append("sage_caption: false-positive Your algorithm")
        # Caption dump should still be caption (working path unchanged)
        if not t._is_caption_composer(xml) and not t._reel_at_caption(xml):
            # still_caption may be disabled-share with location — either helper
            # should still see caption chrome
            tb = t.text_block(xml).lower()
            if "write a caption" not in tb and "new reel" not in tb:
                fails.append("sage_caption: caption helpers broke on still_caption dump")

    if fails:
        print("FAIL")
        for f in fails:
            print(" ", f)
        return 1
    print("OK your-algorithm dismiss (sage + pecntiag dumps)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
