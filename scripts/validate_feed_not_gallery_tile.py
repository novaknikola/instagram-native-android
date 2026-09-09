# -*- coding: utf-8 -*-
"""Offline: FEED carousel must NOT count as gallery video tiles (savaonnell).

savaonnell583 2026-09-08 advance_timeout: picker scored Home feed
'Video 1 of 3 by Eating Healthy Today, Liked by …' as a Recents tile and
tapped it 6× → stuck FEED / POST_TIMEOUT.
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

FEED_DUMP = os.path.join(
    ROOT, "dumps", "reel_savaonnell583_20260908_162427_advance_timeout.xml")
# Known-good gallery-style desc (synthetic) — still accepted by detector
GOOD_DESC = "unselected video thumbnail created on september 8, 2026 3:49 pm"


def main():
    fails = []
    if not os.path.isfile(FEED_DUMP):
        print("FAIL missing %s" % FEED_DUMP)
        return 1

    xml = open(FEED_DUMP, encoding="utf-8").read()
    if t.detect_state(xml) != "FEED":
        fails.append("expected FEED state, got %r" % t.detect_state(xml))

    bad = "video 1 of 3 by eating healthy today, liked by connieassante and others, 50 comments"
    if not t._desc_is_feed_or_viewer_video(bad):
        fails.append("feed desc not classified as feed/viewer")
    if t._desc_is_video_tile(bad):
        fails.append("feed desc still treated as gallery video tile")
    if not t._desc_is_video_tile(GOOD_DESC):
        fails.append("good gallery thumbnail rejected")

    if t._gallery_has_video_tile(xml):
        fails.append("gallery_has_video_tile True on FEED dump")
    tiles = t._list_gallery_video_tiles(xml)
    if tiles:
        fails.append("list_gallery_video_tiles non-empty on FEED: %r"
                     % [(x[0], x[4][:50], x[5]) for x in tiles[:2]])
    gsum = t._gallery_trace_summary(xml)
    if gsum.get("unsel_video", 0) or gsum.get("sel_video", 0):
        fails.append("gallery_trace_summary still counts feed video: %r" % gsum)

    # Full-bleed bounds rejected
    if t._gallery_tile_bounds_ok((0, 802, 1440, 2722)):
        fails.append("full-bleed feed bounds accepted as gallery tile")
    if not t._gallery_tile_bounds_ok((48, 400, 480, 900)):
        fails.append("normal gallery thumb bounds rejected")

    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS feed-vs-gallery video tile filter")
    print("  dump=savaonnell583 advance_timeout FEED")
    print("  excluded: 'Video N of M by … Liked by …'")
    print("  gallery_has_video_tile=False tiles=0")
    print("  real thumbnail desc still accepted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
