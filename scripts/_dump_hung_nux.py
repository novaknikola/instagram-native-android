# -*- coding: utf-8 -*-
import re
import subprocess
import sys

SER = "988ad045575a36335630"
OUT = r"C:\farm\instagram-native\logs\prove_nux_imanidur\final_hung_caption.xml"

subprocess.run(["adb", "-s", SER, "shell", "uiautomator", "dump", "/sdcard/uidump.xml"], check=False)
subprocess.run(["adb", "-s", SER, "pull", "/sdcard/uidump.xml", OUT], check=False)
xml = open(OUT, encoding="utf-8", errors="ignore").read()
low = xml.lower()
keys = [
    "about reels", "clips_nux", "write a caption", "select a location",
    "share", 'enabled="false"', 'enabled="true"', "create your first",
]
print({k: (k in low) for k in keys})
texts = [t for t in re.findall(r'text="([^"]{1,50})"', xml) if t.strip()]
print("TEXTS", texts[:40])
with open(r"C:\farm\instagram-native\logs\prove_nux_imanidur\prove_nux_imanidur_20260906_143817.log", "a", encoding="utf-8") as f:
    f.write("\n[agent] NUX prove hung on disabled Share; About Reels not in live dump. Recording/log retained.\n")
print("OK")
