# One-shot probe: dismiss chrome, open profile, report empty/first-reel signals.
# Usage: python scripts/_probe_first_reel_profile.py <serial> <pkg> [username]
import re
import subprocess
import sys
import time

SERIAL = sys.argv[1]
PKG = sys.argv[2]
USER = (sys.argv[3] if len(sys.argv) > 3 else "").lower()
OUT = r"C:\farm\instagram-native\logs\prove_nux_probe_%s.xml" % (SERIAL[-8:],)


def adb(*args):
    return subprocess.run(
        ["adb", "-s", SERIAL, *args],
        capture_output=True, text=True, encoding="utf-8", errors="ignore")


def dump_xml(path):
    adb("shell", "uiautomator", "dump", "/sdcard/uidump.xml")
    adb("pull", "/sdcard/uidump.xml", path)
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def centers(xml, needle):
    out = []
    for attr in ("text", "content-desc"):
        pat = r'%s="%s"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"' % (
            attr, re.escape(needle))
        for m in re.finditer(pat, xml, re.I):
            x1, y1, x2, y2 = map(int, m.groups())
            out.append(((x1 + x2) // 2, (y1 + y2) // 2))
    return out


def tap(x, y):
    adb("shell", "input", "tap", str(x), str(y))
    print("tap", x, y)


def main():
    adb("shell", "am", "start", "-n",
        "%s/com.instagram.mainactivity.LauncherActivity" % PKG)
    time.sleep(3)
    xml = dump_xml(OUT)
    for label in ("Not now", "Cancel", "Close", "Skip", "Not Now"):
        pts = centers(xml, label)
        if pts:
            tap(*pts[0])
            time.sleep(1.5)
            xml = dump_xml(OUT)
            break
    # profile tab ~ bottom-right
    size = adb("shell", "wm", "size").stdout
    m = re.search(r"(\d+)x(\d+)", size)
    sw, sh = (int(m.group(1)), int(m.group(2))) if m else (1440, 2960)
    for x, y in ((int(sw * 0.90), int(sh * 0.96)),
                 (int(sw * 0.90), int(sh * 0.92)),
                 (int(sw * 0.78), int(sh * 0.96))):
        tap(x, y)
        time.sleep(2.2)
        xml = dump_xml(OUT)
        low = xml.lower()
        if "posts" in low or "edit profile" in low or "grid" in low or (
                USER and USER in low):
            break
    texts = [t for t in re.findall(r'text="([^"]{1,80})"', xml) if t.strip()]
    descs = [d for d in re.findall(r'content-desc="([^"]{1,120})"', xml) if d.strip()]
    print("TEXTS", texts[:50])
    print("DESCS", descs[:50])
    low = xml.lower()
    signals = {
        "0_posts": "0 posts" in low or 'text="0"' in low and "posts" in low,
        "1_post": "1 post" in low,
        "n_posts": bool(re.search(r"\b([2-9]|\d{2,})\s+posts?\b", low)),
        "create_first_reel": "create your first reel" in low,
        "about_reels": "about reels" in low,
        "reel_by": "reel by" in low,
        "edit_profile": "edit profile" in low,
        "user_seen": bool(USER) and USER in low,
    }
    print("SIGNALS", signals)
    empty = signals["0_posts"] or signals["create_first_reel"]
    has_reel = signals["reel_by"] or signals["n_posts"] or signals["1_post"]
    if empty and not has_reel:
        print("VERDICT first_reel_candidate")
        return 0
    if has_reel:
        print("VERDICT already_has_content")
        return 2
    print("VERDICT inconclusive")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
