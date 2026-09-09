# -*- coding: utf-8 -*-
import re
P = r"C:\farm\instagram-native\logs\prove_nux_imanidur\final_hung_caption.xml"
xml = open(P, encoding="utf-8", errors="ignore").read()
print("focused nodes:")
for m in re.finditer(r'<node [^>]*focused="true"[^>]*/?>', xml):
    s = m.group(0)
    def g(a):
        mm = re.search(r'%s="([^"]*)"' % a, s)
        return mm.group(1) if mm else ""
    print(" ", g("class")[-30:], "text=%r" % g("text")[:40], "desc=%r" % g("content-desc")[:40], g("resource-id")[-50:])

print("\nhashtag/suggest/ime:")
for m in re.finditer(r'<node [^>]*/?>', xml):
    s = m.group(0).lower()
    if any(k in s for k in ("hashtag", "suggest", "ime", "keyboard", "gboard", "adbkeyboard", "mention", "autocomplete")):
        print(m.group(0)[:220])

print("\ncaption_input full:")
for m in re.finditer(r'<node [^>]*caption_input[^>]*/?>', xml, re.I):
    print(m.group(0)[:400])

print("\nSave draft?")
print("save_draft" in xml.lower(), "draft" in xml.lower())
for m in re.finditer(r'<node [^>]*(?:draft|save)[^>]*/?>', xml, re.I):
    if "draft" in m.group(0).lower() or "save" in m.group(0).lower():
        print(m.group(0)[:250])
