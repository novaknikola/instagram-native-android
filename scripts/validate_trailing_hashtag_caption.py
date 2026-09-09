# -*- coding: utf-8 -*-
"""Offline validation: trailing incomplete hashtag greys Share (savaonnell).

Proves savaonnell583 2026-09-08 POST_TIMEOUT class:
  caption text like '20    26-08-20 07:35:00# # # # # ##'
  + share_button enabled=false, NO location chip.
Detector must catch trailing bare '#' and clear must strip (not full wipe)
via unstick fallback chain.
"""
from __future__ import print_function

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

SAVA = os.path.join(
    ROOT, "dumps", "reel_savaonnell583_20260908_160656_share_timeout.xml")


def main():
    fails = []
    if not os.path.isfile(SAVA):
        print("FAIL missing dump: %s" % SAVA)
        return 1

    xml = open(SAVA, encoding="utf-8").read()
    cap = t._caption_field_text(xml)
    expect_substr = "# # #"
    if expect_substr not in (cap or ""):
        fails.append("caption missing trailing hashes: %r" % (cap,))

    if t._is_attached_location_chip(xml):
        fails.append("unexpected attached location chip on savaonnell dump")
    if t._is_location_picker_sheet(xml):
        fails.append("unexpected location picker on savaonnell dump")

    node, kind = t._find_reel_caption_share_target(xml) or (None, "")
    if kind != "share_button_disabled":
        fails.append("expected share_button_disabled, got %r" % kind)

    if not t._is_incomplete_hashtag_caption(xml):
        fails.append("detector missed trailing incomplete hashtags: %r" % (cap,))

    keep = re.sub(r"(?:\s*#+)+\s*$", "", cap or "").rstrip()
    if not keep or keep == (cap or "").strip():
        fails.append("strip plan broken keep=%r cap=%r" % (keep, cap))
    if "#" in keep:
        fails.append("strip left hashes in keep=%r" % (keep,))

    # Bare-stub still detected (regression)
    bare_xml = re.sub(
        r'(<node(?=[^>]*caption_input_text_view)[^>]*\stext=")([^"]*)(")',
        r'\1# \3',
        xml,
        count=1,
    )
    if bare_xml == xml or not t._is_incomplete_hashtag_caption(bare_xml):
        fails.append("bare '# ' stub no longer detected")

    # Complete trailing hashtag must NOT trigger
    good_xml = re.sub(
        r'(<node(?=[^>]*caption_input_text_view)[^>]*\stext=")([^"]*)(")',
        r'\1hello #travel\3',
        xml,
        count=1,
    )
    if good_xml == xml or t._is_incomplete_hashtag_caption(good_xml):
        fails.append("false positive on complete #travel caption")

    taps = []
    adb_cmds = []
    orig_tapn, orig_adb, orig_dump, orig_tap = t.tapn, t.adb, t.dump, t.tap
    orig_sleep = __import__("time").sleep
    orig_dismiss = t._dismiss_caption_keyboard

    def _fake_tapn(n, label=""):
        taps.append(label)
        return True

    def _fake_tap(x, y, *a, **k):
        taps.append("tap:%s,%s" % (x, y))
        return True

    def _fake_adb(*a, **k):
        adb_cmds.append(" ".join(str(x) for x in a))
        return ""

    __import__("time").sleep = lambda *a, **k: None
    t.time.sleep = lambda *a, **k: None
    t.tapn = _fake_tapn
    t.tap = _fake_tap
    t.adb = _fake_adb
    t.dump = lambda timeout=8, attempts=2: xml
    t._dismiss_caption_keyboard = lambda *a, **k: True
    try:
        ok = t._clear_incomplete_hashtag_caption(xml)
        if not ok:
            fails.append("clear returned False")
        if not any("caption-clear-hashtag" in x for x in taps):
            fails.append("clear did not focus caption: %r" % taps)
        # Non-destructive path: MOVE_END + DEL, not full clear_field only
        joined = " | ".join(adb_cmds)
        if "KEYCODE_MOVE_END" not in joined:
            fails.append("expected MOVE_END strip path, adb=%r" % adb_cmds[:8])
        if "KEYCODE_DEL" not in joined:
            fails.append("expected DEL strip path, adb=%r" % adb_cmds[:8])

        # Unstick must invoke hashtag clear before force-tap
        taps[:] = []
        adb_cmds[:] = []
        t._unstick_disabled_reel_share(xml)
        if not any("caption-clear-hashtag" in x for x in taps):
            fails.append("unstick never cleared hashtag caption: %r" % taps[:12])
    finally:
        t.tapn, t.adb, t.dump, t.tap = orig_tapn, orig_adb, orig_dump, orig_tap
        t._dismiss_caption_keyboard = orig_dismiss
        __import__("time").sleep = orig_sleep
        t.time.sleep = orig_sleep

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1

    print("PASS trailing hashtag caption validation")
    print("  savaonnell caption=%r" % (cap,))
    print("  detector=trailing incomplete '#'")
    print("  strip_keep=%r" % (keep,))
    print("  clear=MOVE_END+DEL (non-destructive)")
    print("  unstick clears hashtag before force-tap")
    print("  bare '# ' still detected; complete #tag not")
    return 0


if __name__ == "__main__":
    sys.exit(main())
