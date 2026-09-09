# -*- coding: utf-8 -*-
"""Local validation: Dalia phone-20 About Reels POST_TIMEOUT dump (no device/post).

Uses logs/error_shots/...POST_TIMEOUT_125111.xml — About Reels + footer Next both
visible. Asserts CTA ranking prefers clips_nux_share, dismiss taps NUX Share, and
Note8 hard coords refuse to fire (1068,2762 == Cancel on that sheet).
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

DUMP = os.path.join(
    ROOT,
    "logs",
    "error_shots",
    "loose",
    "988a98454b3949444a30_Daliatydfh256_POST_TIMEOUT_125111.xml",
)


def main():
    if not os.path.isfile(DUMP):
        print("FAIL missing dump:", DUMP)
        return 1
    xml = open(DUMP, encoding="utf-8").read()
    fails = []

    if not t._is_clips_nux_sheet(xml):
        fails.append("detector missed About Reels sheet")

    cands = t._find_publish_nodes(xml, fmt="reel")
    if not cands or cands[0][5] != "clips_nux_share":
        fails.append("CTA rank top=%r want clips_nux_share" % (
            cands[0][5] if cands else None,))

    taps = []
    orig_tapn = t.tapn
    orig_tapn_cta = t.tapn_cta
    orig_dump = t.dump
    orig_wm = t._wm_size
    orig_tap = t.tap
    orig_tap_exact = t.tap_exact

    def _fake_tapn(n, label=""):
        taps.append(("tapn", label))
        return True

    def _fake_tapn_cta(n, label=""):
        taps.append(("cta", label))
        return True

    def _fake_tap_exact(xml, *labels, **kwargs):
        taps.append(("exact", ",".join(str(x) for x in labels)))
        return False  # force rid/text path only

    t.tapn = _fake_tapn
    t.tapn_cta = _fake_tapn_cta
    t.tap_exact = _fake_tap_exact
    t.dump = lambda timeout=8, attempts=2: xml  # noqa: E731
    t._wm_size = lambda: (1440, 2960)  # noqa: E731
    hard = []
    t.tap = lambda x, y: hard.append((x, y))  # noqa: E731

    try:
        taps[:] = []
        ok = t._tap_publish_cta(xml, fmt="reel", label="val")
        kind = getattr(t._tap_publish_cta, "last_kind", None)
        if not ok or kind != "clips_nux_share":
            fails.append("tap_publish kind=%r" % (kind,))

        # Footer share_button must not appear while NUX is up
        cands2 = t._find_publish_nodes(xml, fmt="reel")
        kinds = [c[5] for c in cands2]
        if "share_button" in kinds:
            fails.append("footer share_button still a candidate under NUX: %r" % kinds)

        taps[:] = []
        t._dismiss_clips_nux.shared = False
        handled = t._dismiss_clips_nux(xml)
        if not handled or not getattr(t._dismiss_clips_nux, "shared", False):
            fails.append("dismiss_clips_nux shared=%r" % (
                getattr(t._dismiss_clips_nux, "shared", None),))
        if not any("clips-nux-share" in x[1] for x in taps):
            fails.append("dismiss did not tap clips-nux-share: %r" % (taps,))
        if any(x[0] == "exact" and "Share" in x[1] and "Cancel" not in x[1]
               for x in taps):
            fails.append("dismiss used bare tap_exact Share: %r" % (taps,))

        hard[:] = []
        blocked = t._note8_ok_then_share()
        if blocked is not False:
            fails.append("note8 should refuse when NUX up, got %r" % (blocked,))
        if hard:
            fails.append("note8 hard-tapped while NUX up: %r" % (hard,))

        if t._publish_succeeded(xml, fmt="reel"):
            fails.append("publish_succeeded True while About Reels up")
    finally:
        t.tapn = orig_tapn
        t.tapn_cta = orig_tapn_cta
        t.tap_exact = orig_tap_exact
        t.dump = orig_dump
        t._wm_size = orig_wm
        t.tap = orig_tap

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS dalia NUX dump validation")
    print("  detector=About Reels")
    print("  top_cta=clips_nux_share (footer suppressed)")
    print("  dismiss=NUX Share (no bare Share exact)")
    print("  note8=blocked (Cancel coords)")
    print("  publish_succeeded=False while NUX up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
