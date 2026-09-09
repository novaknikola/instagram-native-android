# -*- coding: utf-8 -*-
"""Local validation: story live-ring false positives (no device/post)."""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t


CASES = [
    # (desc, username, expect_live)
    ("Add to your story", "daliatydfh256", False),
    ("add to story", "daliatydfh256", False),
    ("Your story", "daliatydfh256", False),
    ("your story.", "daliatydfh256", False),
    ("0 of 1", "daliatydfh256", False),
    ("Your story. 0 of 3", "daliatydfh256", False),
    ("daliatydfh256's story", "daliatydfh256", True),
    ("Daliatydfh256's story", "daliatydfh256", True),
    # Live ring with zero views (Jazlene FEED tray 2026-09-06)
    ("jazleneguyu258's story, 0 of 1, unseen", "jazleneguyu258", True),
    ("Your story, 0 of 1, unseen", "daliatydfh256", True),
    ("Your story, 2 unseen", "daliatydfh256", True),
    ("your story · 1 new item", "daliatydfh256", True),
    ("someone else's story", "daliatydfh256", True),  # contains 's story
    ("Create story", "daliatydfh256", False),
    ("Close friends story", "daliatydfh256", False),
]


def main():
    fails = []
    for desc, user, expect in CASES:
        got = t._desc_is_live_story_ring(desc, user)
        if bool(got) != bool(expect):
            fails.append("desc=%r user=%r got=%s expect=%s" % (desc, user, got, expect))
    if fails:
        print("FAIL")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS story live-ring validation (%d cases)" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
