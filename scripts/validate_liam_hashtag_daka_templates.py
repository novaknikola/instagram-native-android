# -*- coding: utf-8 -*-
"""Offline validation: liam trailing '#' clear+retype + dakaierrez Templates.

liam45225 2026-09-09: caption '2026-08-20 07:35:00#' greys Share; MOVE_END+DEL
must fall back to clear+retype keep when dump still incomplete.
dakaierrez584 2026-09-09: Templates tab must not be force_caption Share surface.
"""
from __future__ import print_function

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

LIAM = os.path.join(
    ROOT, "dumps", "reel_liam45225_20260909_074041_share_timeout.xml")
DAKA = os.path.join(
    ROOT, "dumps", "reel_dakaierrez584_20260909_072347_share_timeout.xml")
SAVA = os.path.join(
    ROOT, "dumps", "reel_savaonnell583_20260908_160656_share_timeout.xml")


def main():
    fails = []

    if not os.path.isfile(LIAM):
        fails.append("missing liam dump")
    else:
        xml = open(LIAM, encoding="utf-8").read()
        cap = t._caption_field_text(xml)
        if not cap or not cap.endswith("#"):
            fails.append("liam caption not trailing #: %r" % (cap,))
        if not t._is_incomplete_hashtag_caption(xml):
            fails.append("liam incomplete hashtag not detected")
        node, kind = t._find_reel_caption_share_target(xml) or (None, "")
        if kind != "share_button_disabled":
            fails.append("liam expected share_button_disabled got %r" % kind)

        taps = []
        adb_cmds = []
        dump_n = [0]
        orig = (t.tapn, t.adb, t.dump, t.tap, t.type_text,
                t._dismiss_caption_keyboard, t.clear_field)
        orig_sleep = __import__("time").sleep

        def _fake_tapn(n, label=""):
            taps.append(label)
            return True

        def _fake_adb(*a, **k):
            adb_cmds.append(" ".join(str(x) for x in a))
            return ""

        def _fake_dump(*a, **k):
            # First dump after DEL still incomplete → triggers clear+retype.
            dump_n[0] += 1
            return xml

        def _fake_type(text, human=True):
            taps.append("type:%s" % text)
            return True

        def _fake_clear():
            taps.append("clear_field")
            adb_cmds.append("clear_field")

        __import__("time").sleep = lambda *a, **k: None
        t.time.sleep = lambda *a, **k: None
        t.tapn = _fake_tapn
        t.adb = _fake_adb
        t.dump = _fake_dump
        t.type_text = _fake_type
        t.clear_field = _fake_clear
        t._dismiss_caption_keyboard = lambda *a, **k: True
        try:
            ok = t._clear_incomplete_hashtag_caption(xml)
            if not ok:
                fails.append("liam clear returned False")
            joined = " | ".join(adb_cmds)
            if "KEYCODE_MOVE_END" not in joined:
                fails.append("liam expected MOVE_END first: %r" % adb_cmds[:6])
            if "clear_field" not in taps and "clear_field" not in joined:
                fails.append("liam expected clear+retype fallback: taps=%r" % taps)
            if not any(x.startswith("type:2026-08-20") for x in taps):
                fails.append("liam expected retype keep: taps=%r" % taps)
        finally:
            (t.tapn, t.adb, t.dump, t.tap, t.type_text,
             t._dismiss_caption_keyboard, t.clear_field) = orig
            __import__("time").sleep = orig_sleep
            t.time.sleep = orig_sleep

    if not os.path.isfile(DAKA):
        fails.append("missing dakaierrez dump")
    else:
        xml = open(DAKA, encoding="utf-8").read()
        if not t._is_reel_templates_tab(xml):
            fails.append("dakaierrez Templates tab not detected")
        if t._force_caption_surface_ok(xml):
            fails.append("dakaierrez force_caption surface wrongly OK")
        if t._want_caption_publish(xml, force_caption=True):
            fails.append("dakaierrez want_caption_publish True with force")
        if t._is_caption_composer(xml):
            fails.append("dakaierrez wrongly caption composer")

        taps = []
        orig_tapn, orig_adb = t.tapn, t.adb

        def _fake_tapn2(n, label=""):
            taps.append(label)
            return True

        t.tapn = _fake_tapn2
        t.adb = lambda *a, **k: taps.append("adb") or ""
        try:
            if not t._dismiss_reel_templates_tab(xml):
                fails.append("dakaierrez dismiss returned False")
            if "templates-back" not in taps and "adb" not in taps:
                fails.append("dakaierrez dismiss no Back: %r" % taps)
        finally:
            t.tapn, t.adb = orig_tapn, orig_adb

    DAKA2 = os.path.join(
        ROOT, "dumps", "reel_dakaierrez584_20260909_075524_advance_timeout.xml")
    if os.path.isfile(DAKA2):
        xml = open(DAKA2, encoding="utf-8").read()
        if not t._is_profile_grid_sort_menu(xml):
            fails.append("dakaierrez2 Latest/Most viewed menu not detected")
        if t._want_caption_publish(xml, force_caption=True):
            fails.append("dakaierrez2 force caption wrongly true on sort menu")
        taps = []
        orig_adb = t.adb
        t.adb = lambda *a, **k: taps.append("adb:" + " ".join(str(x) for x in a[:3])) or ""
        try:
            if not t._dismiss_profile_grid_sort_menu(xml):
                fails.append("dakaierrez2 sort-menu dismiss False")
            if not any("keyevent" in x.lower() for x in taps):
                fails.append("dakaierrez2 expected BACK: %r" % taps)
        finally:
            t.adb = orig_adb
    else:
        fails.append("missing dakaierrez advance_timeout dump")

    # Regression: real caption still allows force_caption surface
    if os.path.isfile(SAVA):
        xml = open(SAVA, encoding="utf-8").read()
        if t._is_reel_templates_tab(xml):
            fails.append("savaonnell false-positive Templates")
        if not t._force_caption_surface_ok(xml) and not t._is_caption_composer(xml):
            fails.append("savaonnell caption surface broken")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS liam trailing-# + dakaierrez Templates + sort-menu dismiss")
    return 0


if __name__ == "__main__":
    sys.exit(main())
