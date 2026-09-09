# -*- coding: utf-8 -*-
"""Local validation: imanidur hung New-reel caption (no device/post).

Dump has AutoCompleteTextView caption_input text='  # ' and share_button
Next enabled=false. Asserts incomplete-hashtag detection + caption field
discovery (edits() alone misses AutoCompleteTextView).
"""
from __future__ import print_function

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ig_loop as t

DUMP = os.path.join(
    ROOT, "logs", "prove_nux_imanidur", "final_hung_caption.xml")


def main():
    if not os.path.isfile(DUMP):
        print("FAIL missing dump", DUMP)
        return 1
    xml = open(DUMP, encoding="utf-8", errors="ignore").read()
    fails = []

    if t.edits(xml):
        fails.append("edits() unexpectedly non-empty on AutoComplete dump")

    fields = t._caption_field_nodes(xml)
    if not fields:
        fails.append("caption_field_nodes missed AutoCompleteTextView")
    else:
        txt = (t.attr(fields[0], "text") or "").strip()
        if "#" not in txt:
            fails.append("caption text expected '#...' got %r" % (txt,))

    if not t._is_incomplete_hashtag_caption(xml):
        fails.append("incomplete hashtag not detected for text=%r"
                     % (t._caption_field_text(xml),))

    if t._caption_looks_filled(xml, want="nux prove"):
        fails.append("incomplete '#' must not count as filled")

    n, kind = t._find_reel_caption_share_target(xml)
    if kind not in ("share_button_disabled", "share_container_disabled_btn"):
        fails.append("share target kind=%r want disabled" % (kind,))
    if n is not None and t.attr(n, "enabled").lower() == "true" and \
            kind == "share_button":
        fails.append("share_button should be enabled=false in hung dump")

    if t._is_clips_nux_sheet(xml):
        fails.append("About Reels must not be present on hung caption")

    # Negative cases
    for good in ("hello world", "nux prove", "#realhashtag", ""):
        # synthesize via monkeypatch of _caption_field_text is heavy — unit the regex path
        pass
    if t._is_incomplete_hashtag_caption.__doc__ is None:
        fails.append("missing docstring")

    # Direct text classifier via temporary node-less checks:
    # reuse _is_incomplete by injecting through field text helper
    samples = [
        ("#", True),
        ("  # ", True),
        ("##", True),
        ("#cool", False),
        ("nux prove", False),
        ("", False),
    ]
    for text, expect in samples:
        # Build tiny xml fragment
        frag = (
            '<?xml version="1.0"?><hierarchy><node class="android.widget.AutoCompleteTextView" '
            'resource-id="com.instagram.andrqgg:id/caption_input_text_view" '
            'content-desc="Write a caption" text="%s" bounds="[0,0][10,10]"/></hierarchy>'
        ) % text.replace('"', "")
        got = t._is_incomplete_hashtag_caption(frag)
        if bool(got) != bool(expect):
            fails.append("text=%r got=%s expect=%s" % (text, got, expect))

    if fails:
        print("FAIL imanidur hung-caption validation")
        for f in fails:
            print(" -", f)
        return 1
    print("PASS imanidur hung-caption validation")
    print("  caption_field=AutoCompleteTextView")
    print("  incomplete_hashtag=True (text=%r)" % (t._caption_field_text(xml),))
    print("  share_kind=%s" % kind)
    print("  about_reels=False")
    return 0


if __name__ == "__main__":
    sys.exit(main())
