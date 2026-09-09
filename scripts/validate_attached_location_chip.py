# -*- coding: utf-8 -*-
"""Offline validation: attached location chip clear (sage / emersyn).

Proves the Emersyn-class disabled-Share fix: caption composer has a tagged
venue chip (venue_name + clear_button content-desc='Remove location'), which is
NOT the location picker sheet. Detector + dismiss must tap Remove location and
must not tap Turn on Location Services / venue_name row.
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

SAGE = os.path.join(
    ROOT, "dumps", "reel_sage85948_20260908_150623_still_caption.xml")
SAGE_SHOT = os.path.join(
    ROOT, "logs", "error_shots", "loose",
    "988ad045575a36335630_sage85948_POST_TIMEOUT_150627.xml")
EMERSYN = os.path.join(
    ROOT, "dumps", "reel_emersyn39342_20260907_144137_still_caption.xml")
DALIA_PICKER = os.path.join(
    ROOT, "dumps", "reel_Daliatydfh256_20260906_130815_share_timeout.xml")


def _check_dump(path, expect_venue_substr, label):
    fails = []
    if not os.path.isfile(path):
        return ["missing dump: %s" % path]
    xml = open(path, encoding="utf-8").read()

    if t._is_location_picker_sheet(xml):
        fails.append("%s: wrongly detected as location picker sheet" % label)
    if not t._is_attached_location_chip(xml):
        fails.append("%s: attached location chip not detected" % label)

    clear = t._attached_location_clear_node(xml)
    if clear is None:
        fails.append("%s: no clear_button / Remove location node" % label)
    else:
        rid = t.attr(clear, "resource-id").lower()
        d = t.attr(clear, "content-desc").strip().lower()
        if "clear_button" not in rid and d != "remove location":
            fails.append("%s: clear node unexpected rid=%r desc=%r" % (label, rid, d))
        if t.attr(clear, "clickable") != "true":
            fails.append("%s: clear node not clickable" % label)
        x, y = t.bounds_center(clear)
        if x is None or not (1200 <= x <= 1440) or not (900 <= y <= 1200):
            fails.append("%s: clear bounds center unexpected %s,%s" % (label, x, y))

    venue = ""
    for n in t.nodes(xml):
        if "venue_name" in t.attr(n, "resource-id").lower():
            venue = (t.attr(n, "text") or "").strip()
            break
    if expect_venue_substr.lower() not in venue.lower():
        fails.append("%s: venue_name=%r missing %r" % (label, venue, expect_venue_substr))

    # Share still disabled in these dumps (pre-clear state)
    node, kind = t._find_reel_caption_share_target(xml) or (None, "")
    if kind != "share_button_disabled":
        fails.append("%s: expected share_button_disabled, got %r" % (label, kind))

    taps = []
    orig_tapn, orig_adb, orig_dump, orig_tap = t.tapn, t.adb, t.dump, t.tap

    def _fake_tapn(n, label=""):
        taps.append(label)
        return True

    def _fake_tap(x, y, *a, **k):
        taps.append("tap:%s,%s" % (x, y))
        return True

    t.tapn = _fake_tapn
    t.tap = _fake_tap
    t.adb = lambda *a, **k: taps.append("adb") or ""
    t.dump = lambda timeout=8, attempts=2: xml
    try:
        ok = t._dismiss_attached_location_chip(xml)
        if not ok:
            fails.append("%s: dismiss returned False" % label)
        if not any("location-chip-remove" in x for x in taps):
            fails.append("%s: dismiss did not tap location-chip-remove: %r" % (label, taps))
        if any("turn on" in x.lower() or "ls_action" in x for x in taps):
            fails.append("%s: dismiss tapped Location Services: %r" % (label, taps))
        # unstick path should hit chip clear first, then wait for Share enable
        taps[:] = []
        dumps_after = [xml]  # frozen dump: Share stays disabled → wait then nudge

        def _fake_dump(timeout=8, attempts=2):
            return dumps_after[0]

        t.dump = _fake_dump
        t._unstick_disabled_reel_share(xml)
        if not any("location-chip-remove" in x for x in taps):
            fails.append("%s: unstick never cleared location chip: %r" % (label, taps[:12]))
        # Must not jump straight to force-tap without attempting clear.
        first_chip = next(
            (i for i, x in enumerate(taps) if "location-chip-remove" in x), None)
        first_force = next(
            (i for i, x in enumerate(taps)
             if "unstick-force" in x or "unstick-after-loc-clear" in x), None)
        if first_chip is not None and first_force is not None and first_force < first_chip:
            fails.append("%s: force-tap before chip clear: %r" % (label, taps[:12]))
    finally:
        t.tapn, t.adb, t.dump, t.tap = orig_tapn, orig_adb, orig_dump, orig_tap

    return fails


def main():
    fails = []
    fails += _check_dump(SAGE, "Private Location", "sage")
    if os.path.isfile(SAGE_SHOT):
        fails += _check_dump(SAGE_SHOT, "Private Location", "sage_shot")
    fails += _check_dump(EMERSYN, "Miami", "emersyn")

    # Dalia picker must stay on picker path (not attached-chip).
    if os.path.isfile(DALIA_PICKER):
        xml = open(DALIA_PICKER, encoding="utf-8").read()
        if not t._is_location_picker_sheet(xml):
            fails.append("dalia: picker detector broken")
        if t._is_attached_location_chip(xml):
            fails.append("dalia: picker wrongly classified as attached chip")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS attached location chip validation")
    print("  sage: venue=Private Location clear_button Remove location")
    print("  sage_shot: same chip on POST_TIMEOUT error XML")
    print("  emersyn: venue=Miami, Florida clear_button Remove location")
    print("  dismiss=location-chip-remove (not picker / not GPS)")
    print("  unstick clears chip then waits for Share enabled")
    print("  dalia picker still distinct from attached chip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
