# -*- coding: utf-8 -*-
"""Parse hung imanidur caption dump for Share/Next blockers."""
import re
import sys

P = r"C:\farm\instagram-native\logs\prove_nux_imanidur\final_hung_caption.xml"
xml = open(P, encoding="utf-8", errors="ignore").read()

print("=== share/next/ok/also-share nodes ===")
for m in re.finditer(r"<node [^>]+/?>", xml):
    s = m.group(0)
    low = s.lower()
    if not any(k in low for k in (
        "share", "next", "also", "facebook", "threads", "location",
        "nux", "ok", "caption", "cover", "audience", "tag people")):
        continue
    def g(a):
        mm = re.search(r'%s="([^"]*)"' % a, s)
        return mm.group(1) if mm else ""
    print("en=%-5s click=%-5s text=%r desc=%r rid=%s bounds=%s" % (
        g("enabled"), g("clickable"), g("text")[:40], g("content-desc")[:50],
        g("resource-id")[-70:], g("bounds")))

print("\n=== share_button specifically ===")
for m in re.finditer(r"<node [^>]*share_button[^>]*/?>", xml, re.I):
    print(m.group(0)[:300])
