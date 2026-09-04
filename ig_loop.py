# -*- coding: utf-8 -*-
# ig_loop.py - Instagram publishing on the existing farm spine.
# Formats: feed | story | carousel | reel (do_post(..., format=...)).
# Pattern from threads_loop.py (ADB + UIAutomator); clones com.instagram.androi*.
# Sticky IP handled by run_ig_device + ig_sticky_ip (not here).
import os
from farm_root import ROOT
import subprocess, re, time, csv, hmac, hashlib, base64, struct, sys, os, random

# Found 2026-07-03: a picker-node's content-desc can contain a character the
# Windows console (cp1252) can't display, crashing the whole device process
# on an unhandled UnicodeEncodeError (same class of bug as the earlier Rich
# console crash - documented project-wide, just hit a different print site).
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

SERIAL  = "988a9b39585859325730"
CSVPATH = os.environ.get("IG_ACCOUNTS_CSV") or os.path.join(ROOT, 'Instagram_farm_accounts.csv')
RESULTS = os.path.join(ROOT, 'ig_post_results.csv')
CAPTION = "Good morning"

# IG Nomix clones look like com.instagram.androi* (stock = com.instagram.android).
# Threads clones are com.instagram.barcel* — never mix those filters.
#
# JOBS = list of (csv_row_index, ig_clone_package)
JOBS = [
    (0, "com.instagram.android"),
]

MAX_STEPS  = 90
STEP_PAUSE = 2.0
UNKNOWN_DUMP_AT = 5    # after this many UNKNOWN in a row, dump the screen once for inspection
UNKNOWN_BAIL_AT = 12   # after this many UNKNOWN in a row, hard-fail fast as UNKNOWN_STUCK
CURRENT_PKG = None     # set by do_login / do_post for create-intent fallback
CURRENT_REMOTE_IMG = None  # last pushed gallery path (for ACTION_SEND)
CURRENT_REMOTE_STEM = None  # basename stem of last push (gallery match)

# Last successful uiautomator XML — dump timeouts on Reels/caption preview
# return this instead of sitting 20s×4 on a playing video.
_LAST_XML = ""
# (name, t0, deadline) — operator: no UI section may sit > 60s.
_SECTION = None

try:
    import ig_reel_trace as rt
except Exception:
    rt = None

try:
    import ig_media_names as mn
except Exception:
    mn = None

try:
    import ig_error_shots as errshots
except Exception:
    errshots = None

try:
    import ig_step_watch as _sw
except Exception:
    _sw = None

CURRENT_USER = ""          # set by do_login / do_post for error shots
_PUBLISH_BUILD = "reel_cleanup_20260819a"
LAST_POST_META = {
    "story_link": "",
    "link_ok": "",
    "highlight_ok": "",
    "warmup": "",
}


def last_post_meta():
    return dict(LAST_POST_META)


def _reset_post_meta():
    LAST_POST_META.update(
        story_link="", link_ok="", highlight_ok="", warmup=""
    )


def _sw_step(phase, state="", note="", expected="", last_action=""):
    """Screenshot + vision analyze on every farm step. Soft-fail."""
    if _sw is None:
        return {}
    try:
        return _sw.step(
            phase,
            state=state or "",
            note=note or "",
            expected=expected or "",
            last_action=last_action or "",
        ) or {}
    except Exception as e:
        print("[step-watch] skip: %s" % e)
        return {}


def _sw_act(label):
    if _sw is not None:
        try:
            _sw.note_action(label)
        except Exception:
            pass


def emit_fail(code, note=""):
    """Hard-fail helper: capture error shot+XML, then return code. Never raises."""
    code = (code or "").strip() or "UNKNOWN_STUCK"
    _sw_step("fail_%s" % code, note=note or "", expected="recover or abort")
    try:
        if errshots is not None:
            errshots.capture(
                serial=SERIAL,
                result=code,
                username=CURRENT_USER or "",
                note=note or "",
                pkg=CURRENT_PKG or "",
                include_xml=True,
                build=_PUBLISH_BUILD,
            )
    except Exception as e:
        print("[shot] emit_fail error: %s" % e)
    return code

# #region agent log
def _dbg479(hypothesisId, location, message, data=None):
    """Debug-mode NDJSON (session 479f30). Windows + Mac paths; also reel-trace."""
    import json as _json
    payload = {
        "sessionId": "479f30",
        "hypothesisId": hypothesisId,
        "location": location,
        "message": message,
        "data": data or {},
        "timestamp": int(time.time() * 1000),
    }
    line = _json.dumps(payload, ensure_ascii=False)
    print("[dbg-479f30] %s" % line)
    for path in (
            r"/Volumes/Work/Projects/thread-android/.cursor/debug-479f30.log",
            os.path.join(os.environ.get("IG_FARM_BASE", ROOT),
                         "ig_debug_479f30.ndjson")):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass
    try:
        if rt is not None:
            _rt_log("dbg", hypothesisId=hypothesisId, location=location,
                    message=message, **(data or {}))
    except Exception:
        pass
# #endregion

# ---------------------------------------------------------------------------
# ADB helpers
# ---------------------------------------------------------------------------


def _rt_log(phase, **fields):
    if rt is None:
        return
    try:
        rt.log(phase, **fields)
    except Exception:
        pass


def _rt_fail(phase, **fields):
    if rt is None:
        return ""
    try:
        return rt.fail(phase, serial=SERIAL, **fields)
    except Exception:
        return ""


def _reel_at_caption(xml=None):
    """True caption screen for Reels — not music/search EditText alone."""
    xml = xml or dump()
    tb = text_block(xml)
    if in_caption_screen(xml):
        return True
    if any(p in tb for p in ("write a caption", "add a caption", "caption…", "caption...")):
        return True
    if "share" in tb and any(p in tb for p in ("tag people", "add location", "also share",
                                                 "new reel", "audience")):
        return True
    return False

def _lock_portrait():
    """Force natural portrait for the whole device (not just IG).

    Farm phones flip system-wide to landscape (tablet UI / orientation=3,
    2960x1440). Share and Create taps then miss. Lock before every post.
    """
    try:
        adb("shell", "settings", "put", "system", "accelerometer_rotation", "0")
        adb("shell", "settings", "put", "system", "user_rotation", "0")
        adb("shell", "wm", "user-rotation", "lock", "0")
        print("[orient] portrait locked (accel=0 user_rotation=lock 0)")
    except Exception as e:
        print("[orient] lock failed: %s" % e)


def adb(*args, timeout=60):
    try:
        r = subprocess.run(
            ["adb", "-s", SERIAL, *args],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout
        )
        return r.stdout or ""
    except Exception as e:
        print("[adb-err]", e); return ""

def _section_max_sec():
    try:
        v = int(float(os.environ.get("IG_SECTION_MAX_SEC", "60")))
    except Exception:
        v = 60
    # Hard cap 60s — sitting on caption/create staring at empty UI is the bug.
    return max(15, min(v, 60))


def _section_begin(name, seconds=None):
    """Start a timed UI section. Nested helpers see _SECTION and dump fast."""
    global _SECTION
    if seconds is None:
        sec = _section_max_sec()
    else:
        sec = max(5, int(seconds))
    t0 = time.time()
    _SECTION = (name, t0, t0 + sec)
    print("[budget] %s start max=%ds" % (name, sec))
    return _SECTION


def _section_left():
    if not _SECTION:
        return 999.0
    return _SECTION[2] - time.time()


def _section_expired(need=0.4):
    """True when this section is out of time — caller must leave the screen."""
    if not _SECTION:
        return False
    name, t0, deadline = _SECTION
    left = deadline - time.time()
    if left < need:
        print("[budget] %s STOP after %.0fs (limit %ds)" % (
            name, time.time() - t0, int(deadline - t0)))
        return True
    return False


def dump(timeout=20, attempts=4):
    """Fresh UI dump. Never use during Reels playback — video never goes idle.

    Inside a timed section (create/composer) caption preview never goes idle, so
    default 20s×4 burned minutes. Cap those dumps at 5s×1 and reuse last XML.
    """
    global _LAST_XML
    last = ""
    attempts = max(1, int(attempts))
    timeout = float(timeout)
    if _SECTION is not None:
        timeout = min(timeout, 5.0)
        attempts = min(attempts, 1)
    for attempt in range(attempts):
        try:
            r = subprocess.run(
                ["adb", "-s", SERIAL, "shell", "uiautomator", "dump", "/sdcard/ui.xml"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            print("[dump] timeout %.0fs (attempt %d) — UI not idle" % (
                timeout, attempt + 1))
            continue
        except Exception as e:
            print("[dump] err %s" % e)
            time.sleep(0.4)
            continue
        msg = ((r.stdout or "") + " " + (r.stderr or "")).lower()
        last = adb("shell", "cat", "/sdcard/ui.xml")
        if "error" in msg or "null root" in msg:
            print("[dump] retry %d: %s" % (attempt + 1, msg.strip()[:90]))
            time.sleep(0.45)
            continue
        if last and "<node" in last:
            _LAST_XML = last
            return last
        time.sleep(0.35)
    return last or _LAST_XML

def nodes(xml):
    return re.findall(r'<node[^>]+>', xml)

def attr(n, key):
    m = re.search(r'%s="([^"]*)"' % re.escape(key), n)
    return m.group(1) if m else ""

def bounds_center(n):
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m: return None, None
    return (int(m.group(1)) + int(m.group(3))) // 2, \
           (int(m.group(2)) + int(m.group(4))) // 2

def bounds_wh(n):
    """Width, height of node bounds — used to demote full-bleed share containers."""
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m:
        return 0, 0
    return max(0, int(m.group(3)) - int(m.group(1))), \
           max(0, int(m.group(4)) - int(m.group(2)))

def _human_on():
    """Human-like jitter/pauses (IG_HUMAN=0 to disable)."""
    return os.environ.get("IG_HUMAN", "1").strip().lower() not in ("0", "false", "off", "no")


def _human_float(name, default):
    try:
        return float(os.environ.get(name, str(default)))
    except Exception:
        return float(default)


def _human_jitter_px():
    try:
        return max(0, int(os.environ.get("IG_HUMAN_JITTER", "4")))
    except Exception:
        return 4


def human_pause(lo=0.2, hi=0.5):
    """Variable delay — faster when IG_HUMAN=0."""
    if _human_on():
        lo = _human_float("IG_HUMAN_PAUSE_LO", lo)
        hi = _human_float("IG_HUMAN_PAUSE_HI", max(hi, lo + 0.05))
        time.sleep(random.uniform(lo, hi))
    else:
        time.sleep(lo * 0.55)


def human_think():
    """Pause between major steps (gallery pick → sticker → share)."""
    if _human_on():
        time.sleep(random.uniform(
            _human_float("IG_HUMAN_THINK_LO", 0.55),
            _human_float("IG_HUMAN_THINK_HI", 1.25),
        ))


def human_swipe(x1, y1, x2, y2, duration_ms=280):
    """Swipe with slight coordinate/duration variance."""
    if _human_on():
        j = _human_jitter_px()
        x1 = max(0, x1 + random.randint(-j, j))
        y1 = max(0, y1 + random.randint(-j // 2, j // 2))
        x2 = max(0, x2 + random.randint(-j, j))
        y2 = max(0, y2 + random.randint(-j // 2, j // 2))
        duration_ms += random.randint(-35, 55)
    adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2),
        str(max(120, duration_ms)))
    human_pause(0.3, 0.65)


def tap(x, y):
    j = _human_jitter_px() if _human_on() else 0
    if j:
        x = max(0, x + random.randint(-j, j))
        y = max(0, y + random.randint(-j, j))
        time.sleep(random.uniform(0.04, 0.11))
    adb("shell", "input", "tap", str(x), str(y))
    if _human_on():
        time.sleep(random.uniform(0.10, 0.26))


def tapn(n, label=""):
    x, y = bounds_center(n)
    if x is None: print("[tapn-err] no bounds:", label); return False
    print("[tap]", label, "@ %d,%d" % (x, y))
    _sw_act(label or "tap")
    tap(x, y)
    if label and os.environ.get("IG_STEP_WATCH_TAPS", "0").strip().lower() in (
            "1", "true", "yes", "on"):
        _sw_step("tap_%s" % label.replace(" ", "_")[:40], note=label)
    return True


def tapn_cta(n, label=""):
    """Publish Next/Share. Prefer upper third INSIDE the chip.

    Old logic used y=t0-52 for tall bottom nodes (AdbIME strip). When keyboard
    is gone, Share is a short chip @~2696–2828 — t0-52 lands at ~2644 ABOVE the
    button (Nylah 2026-09-04 share_timeout: tapped_xy logged center but miss).
    """
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m:
        print("[tapn-err] no bounds:", label)
        return False
    l, t0, r, b0 = (int(m.group(i)) for i in range(1, 5))
    x = (l + r) // 2
    h = b0 - t0
    if h <= 220:
        # Real Share/Next chip — upper third inside bounds
        y = t0 + max(14, min(int(h * 0.32), h - 18))
    elif t0 >= 2000:
        # Tall node overlapping IME — aim just above the strip
        y = max(1800, t0 - 52)
    else:
        y = t0 + max(16, min(int(h * 0.18), max(0, h - 20)))
    print("[tap]", label, "@ %d,%d (cta h=%d top=%d)" % (x, y, h, t0))
    _sw_act(label or "tap")
    tap(x, y)
    return True


def _vision_tap(name, question="", tag=""):
    """Ask Gemini/Grok where `name` is on a live screencap, then tap. Soft-fail."""
    try:
        import ig_vision as vis
    except Exception:
        return False
    if not vis.enabled():
        return False
    hit = vis.find(SERIAL, name, question=question, tag=tag)
    if not hit:
        return False
    print("[vision-tap] %s @ %d,%d screen=%s" % (
        name, hit["x"], hit["y"], hit.get("screen") or ""))
    tap(hit["x"], hit["y"])
    time.sleep(0.8)
    return True

def edits(xml):
    return [n for n in nodes(xml) if "EditText" in attr(n, "class")]

def clear_field():
    # KEYCODE_CTRL_A is not a real single Android keyevent (input keyevent can't
    # send modifier+key combos) - it silently no-ops, so "select all" never
    # happened and a single DEL only removed one character, leaving stale digits
    # behind (2026-07-16: this is what caused the TOTP field to keep showing an
    # old leftover code no matter what we typed - real screenshot confirmed the
    # symptom before assuming a cause). Move to end, then DEL enough times to
    # clear any realistic field length regardless of Ctrl+A support.
    adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
    time.sleep(0.15)
    for _ in range(20):
        adb("shell", "input", "keyevent", "KEYCODE_DEL")
    time.sleep(0.3)

def type_text(text, human=True):
    """Type one char at a time via ADB.

    human=True (default): jittered inter-key delays so login/TOTP does not look like
    an instant dump. Client request 2026-08-12 — Meta often masks risk as
    "incorrect password"; bulk fill is a common bot signal on web, and even on
    device a flat 60ms cadence is unnatural. Caps at mild human speed so we
    do not blow login timeouts.
    """
    text = text or ""
    if human and text:
        time.sleep(random.uniform(0.18, 0.55))
    for i, ch in enumerate(text):
        if ch == " ":
            adb("shell", "input", "keyevent", "62")
        else:
            adb("shell", "input", "text", ch)
        if human:
            delay = random.uniform(0.08, 0.19)
            # Occasional hesitation (pause mid-string like a real typist)
            if i > 2 and random.random() < 0.09:
                delay += random.uniform(0.15, 0.40)
            time.sleep(delay)
        else:
            time.sleep(0.06)


def _type_caption_human(caption):
    """Type like a person, then stop. No sitting on the field afterwards."""
    caption = caption or ""
    if not caption:
        return
    human_pause(0.25, 0.55)
    ascii_ok = all(32 <= ord(c) < 127 for c in caption)
    if ascii_ok and len(caption) <= 72:
        type_text(caption, human=True)
    elif ascii_ok:
        type_text(caption[:24], human=True)
        adb_b64(caption[24:])
    else:
        adb_b64(caption)
    human_pause(0.12, 0.28)

def fg_pkg():
    # Method 1: dumpsys window windows (standard pre-Android-13)
    out = adb("shell", "dumpsys", "window", "windows")
    m = re.search(r'mCurrentFocus=Window\{[^}]*\s+([\w.]+)/', out)
    if m: return m.group(1)
    # Method 2: dumpsys window without subcommand (Android 13+)
    out = adb("shell", "dumpsys", "window")
    m = re.search(r'mCurrentFocus.*?(com\.instagram\.[a-z]+)', out)
    if m: return m.group(1)
    # Method 3: activity manager
    out = adb("shell", "dumpsys", "activity", "activities")
    m = re.search(r'mResumedActivity.*?(com\.instagram\.[a-z]+)', out)
    if m: return m.group(1)
    return ""

def wake_unlock():
    # After a reboot (or idle) the phone can be asleep/locked -> the screen reads as
    # all-black -> every detect_state() returns UNKNOWN. Wake + dismiss the lock first.
    # These devices have no PIN, so KEYCODE_MENU + a swipe-up clears the lock screen.
    out = adb("shell", "dumpsys", "power")
    if "mWakefulness=Awake" not in out:
        adb("shell", "input", "keyevent", "KEYCODE_WAKEUP")
        time.sleep(0.8)
    adb("shell", "input", "keyevent", "82")          # MENU -> dismiss simple lock
    adb("shell", "input", "swipe", "540", "1800", "540", "600")
    time.sleep(0.8)

def pkg_installed(pkg):
    out = adb("shell", "pm", "path", pkg)
    return bool(out.strip()) and "package:" in out

def force_stop(pkg):
    adb("shell", "am", "force-stop", pkg)


def _ui_blank(xml):
    """True when dump has no text and no clickables (black screen / uiautomator hang)."""
    if (text_block(xml) or "").strip():
        return False
    return not any(attr(n, "clickable") == "true" for n in nodes(xml))


def launch(pkg):
    """Open ONLY the requested IG package. Never treat a Threads login UI as success."""
    wake_unlock()
    if not pkg_installed(pkg):
        print("[FAIL] package not installed on this phone: %s" % pkg)
        return False
    for attempt in range(4):
        if attempt > 0:
            # Stuck/zombie process (andrprh APP_WONT_OPEN 2026-07-29) — kill then retry.
            print("[launch] force-stop + retry %s" % pkg.split(".")[-1])
            force_stop(pkg)
            time.sleep(1.2)
        adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(3)
        for _ in range(6):
            fg = fg_pkg()
            blob = (fg or "").lower()
            if "gms" in blob or "assistedsignin" in blob or "credentials" in blob:
                print("[launch] Google passkey/sign-in overlay — Back")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(0.7)
                continue
            break
        fg = fg_pkg()
        print("[launch] want=%s fg=%s (attempt %d)" % (pkg.split(".")[-1], fg, attempt + 1))
        if pkg in fg:
            return True
        # Accessibility dump packages (when dumpsys focus is flaky)
        xml = dump()
        if pkg in xml:
            # Require our package id in the tree — Threads also says "Log in to Instagram"
            print("[launch] UI tree contains target package")
            return True
        if "com.instagram.barcel" in (fg or "") or "barcelona" in (fg or ""):
            print("[launch] Threads clone is foreground — wrong app, retrying target IG pkg")
        # Explicit MAIN launch as last attempts (monkey sometimes no-ops)
        if attempt >= 2:
            adb("shell", "am", "start", "-a", "android.intent.action.MAIN",
                "-c", "android.intent.category.LAUNCHER", "-p", pkg)
            time.sleep(3)
            fg = fg_pkg()
            if pkg in fg or pkg in dump():
                print("[launch] am start recovered %s" % pkg.split(".")[-1])
                return True
        time.sleep(2)
    return False

def reveal_pwd():
    for n in nodes(dump()):
        d = attr(n, "content-desc").lower()
        t = attr(n, "text").lower()
        if "show password" in d or "show password" in t or \
           ("password" in d and "show" in d) or "reveal" in d:
            return tapn(n, "reveal-pwd")
    return False

def looks_filled(shown, value, idx):
    if value.lower() in shown.lower(): return True
    if idx == 1:
        if any(c in shown for c in "•●*‣"): return True
        return len(shown.strip()) > 0 and shown.strip().lower() != "password"
    return False

def set_field(idx, value, label):
    for attempt in range(4):
        es = edits(dump())
        if len(es) <= idx:
            # Often means login already advanced (fields gone) — not a hard fail.
            print("[wait] %s field not found (try %d)" % (label, attempt + 1))
            time.sleep(1.0); continue
        tapn(es[idx], label); time.sleep(1.0)
        clear_field(); type_text(value); time.sleep(0.8)
        if idx == 1: reveal_pwd(); time.sleep(0.6)
        es = edits(dump())
        shown = attr(es[idx], "text") if len(es) > idx else ""
        if looks_filled(shown, value, idx):
            print("[typed] %s ok" % label); return True
        print("[retry %d] %s (shown: '%s')" % (attempt + 1, label, shown[:14]))
        print("[warn] %s field gone/unfilled after retries (screen may have advanced)" % label)
    return False


def _login_submit_button_center(xml):
    """Best (x,y) for blue Log in / Sign in on the credential form.

    Prefer resource-id + label→clickable parent (Compose TextView often
    clickable=false). Never aim at bottom 'Create new account' band.
    """
    xml = xml or ""
    sw, sh = _screen_wh(xml)
    if sw < 200 or sh < 400:
        sw, sh = 1440, 2960
    y_lo, y_hi = int(sh * 0.22), int(sh * 0.78)

    id_hits = []
    label_hits = []  # (exact_rank, cx, cy, n)
    igds_mid = []

    for n in nodes(xml):
        b = _node_bounds(n)
        if not b:
            continue
        x1, y1, x2, y2 = b
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if cy < y_lo or cy > y_hi:
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        rid = attr(n, "resource-id").lower()
        blob = t + " " + d

        if any(k in rid for k in ("log_in_button", "login_button", "button_login",
                                    "/log_in", "login_btn")):
            id_hits.append((cx, cy, (x2 - x1) * (y2 - y1)))

        exact = t in ("log in", "sign in") or d in ("log in", "sign in")
        # Soft: button-ish only — never "Log in to Instagram" chrome / forgot link
        soft = (not exact) and "forgot" not in blob and "create" not in blob and \
            "already" not in blob and "log in to" not in blob and "log into" not in blob and (
                t.startswith("log in") or d.startswith("log in") or
                t.startswith("sign in") or d.startswith("sign in")
            )
        if exact or soft:
            label_hits.append((0 if exact else 1, cx, cy, n))

        if attr(n, "clickable") == "true" and "igds_button" in rid:
            area = (x2 - x1) * (y2 - y1)
            if area >= 8000:
                igds_mid.append((area, cx, cy))

    # Label text → smallest clickable ancestor containing its center
    clickables = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        b = _node_bounds(n)
        if b:
            clickables.append((n, b))

    for _rank, cx, cy, _n in sorted(label_hits, key=lambda r: r[0]):
        best = None
        for _cn, (x1, y1, x2, y2) in clickables:
            if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                continue
            area = (x2 - x1) * (y2 - y1)
            if area > sw * sh * 0.45:
                continue
            if y_lo <= (y1 + y2) // 2 <= y_hi and (best is None or area < best[0]):
                best = (area, (x1 + x2) // 2, (y1 + y2) // 2)
        if best:
            return best[1], best[2]
        return cx, cy

    if id_hits:
        id_hits.sort(key=lambda r: -r[2])
        return id_hits[0][0], id_hits[0][1]

    if igds_mid:
        igds_mid.sort(key=lambda r: -r[0])
        return igds_mid[0][1], igds_mid[0][2]

    # Geometry: below password EditText (AkemiNoguchi918 dark form)
    es = edits(xml)
    if len(es) >= 2:
        b = _node_bounds(es[1])
        if b:
            x1, y1, x2, y2 = b
            return (x1 + x2) // 2, min(y2 + int(sh * 0.06), y_hi)
    return None, None


def _tap_login_submit(xml, label="login-btn"):
    """Strong tap on Log in — same Compose/Samsung harden as notif Next."""
    xml = xml or dump()
    cx, cy = _login_submit_button_center(xml)
    if cx is not None:
        _adb_tap_xy(cx, cy, label)
        time.sleep(0.12)
        _adb_tap_xy(cx, cy + 8, label + "b")
        return True
    # Mid-form coord only when credential chrome is clearly present
    tb = text_block(xml)
    if "password" in tb and any(p in tb for p in (
            "mobile number", "phone number", "email", "username", "log in")):
        sw, sh = _screen_wh(xml)
        _adb_tap_xy(sw // 2, int(sh * 0.48), label + "-coord")
        return True
    if tap_exact(xml, "Log in", "LOG IN", "Log In", "Sign in", "Sign In",
                 label=label + "-exact"):
        return True
    return False


def _press_login_submit(max_tries=5, label="login-btn"):
    """After credentials: hide IME, re-dump, press Log in with retries.

    AkemiNoguchi918 2026-08-12: PNG showed Log in while one-shot dump/tap_first
    returned NO_LOGIN_BTN (race + Compose + dead first match).
    """
    for i in range(max_tries):
        xml = dump()
        st = detect_state(xml)
        if st != "LOGIN_SCREEN":
            print("[login] submit — already left LOGIN_SCREEN -> %s" % st)
            return True
        if i > 0 and len(edits(xml)) < 2:
            print("[login] submit — fields gone after tap (likely accepted)")
            return True
        print("[login] Log in try %d/%d" % (i + 1, max_tries))
        _hide_ime(xml)
        time.sleep(0.4)
        xml = dump()
        if not _tap_login_submit(xml, label="%s-%d" % (label, i + 1)):
            time.sleep(0.8)
            continue
        time.sleep(1.2)
        xml2 = dump()
        if detect_state(xml2) != "LOGIN_SCREEN" or len(edits(xml2)) < 2:
            return True
    print("[login] FAIL — Log in still not advancing after %d tries" % max_tries)
    return False


def totp(secret):
    key = base64.b32decode(secret.replace(" ", "").upper(), casefold=True)
    t = struct.pack(">Q", int(time.time()) // 30)
    h = hmac.new(key, t, hashlib.sha1).digest()
    o = h[-1] & 0xf
    code = struct.unpack(">I", h[o:o+4])[0] & 0x7fffffff
    return "%06d" % (code % 1000000)

def text_block(xml):
    raw = " ".join(attr(n, "text") + " " + attr(n, "content-desc") for n in nodes(xml)).lower()
    # Normalize so substring checks are robust: Instagram uses curly apostrophes
    # and non-breaking/thin spaces that silently break plain matches.
    raw = (raw.replace("’", "'").replace("‘", "'")
              .replace(" ", " ").replace(" ", " ").replace("​", ""))
    return re.sub(r"\s+", " ", raw)

def has_any(xml, *phrases):
    tb = text_block(xml)
    return any(p.lower() in tb for p in phrases)

def tap_first(xml, *phrases, label=""):
    """Tap first matching phrase. Skip dead nodes (no bounds) — try next match.

    AkemiNoguchi918 2026-08-12: returning False from the first hit aborted before
    a real 'Log in' sibling / later phrase could be tried.
    """
    for phrase in phrases:
        for n in nodes(xml):
            t = attr(n, "text").lower()
            d = attr(n, "content-desc").lower()
            if phrase.lower() in t or phrase.lower() in d:
                if tapn(n, label or phrase):
                    return True
    return False

def tap_exact(xml, *phrases, label=""):
    """Exact text/desc match only — avoids 'Continue' hitting 'CONTINUE CREATING ACCOUNT'
    (2026-07-23: that substring bug looped signup forever after LOG IN)."""
    want = {p.strip().lower() for p in phrases}
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t in want or d in want:
            return tapn(n, label or t or d)
    return False


def _tap_phrase(xml, *phrases, label="", prefer_clickable=True):
    """Tap by exact label, then contains. Prefer clickable nodes (coach-mark tips)."""
    want = [p.strip().lower() for p in phrases if p and p.strip()]
    if not want:
        return False
    cands = []
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        clk = attr(n, "clickable") == "true"
        hit = None
        for p in want:
            if t == p or d == p:
                hit = (0, p)  # exact
                break
            if len(p) >= 4 and (p in t or p in d):
                hit = (1, p)  # contains
                break
        if hit is None:
            continue
        x, y = bounds_center(n)
        if x is None:
            continue
        # rank: exact+clickable best
        rank = (hit[0], 0 if clk else 1, - (x * y))
        cands.append((rank, n, hit[1], clk))
    if not cands:
        return False
    cands.sort(key=lambda r: r[0])
    if prefer_clickable:
        for _rank, n, p, clk in cands:
            if clk:
                return tapn(n, label or p)
    _rank, n, p, _clk = cands[0]
    return tapn(n, label or p)

def tap_post_btn(xml):
    # The composer's REAL "Post" button. EXACT text/desc match so we never tap
    # "Post Options" (which contains the substring "Post"). Threads puts the
    # Post button bottom-right, so prefer the right-most exact match.
    best = None
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t in ("post", "share", "publish") or d in ("post", "share", "publish"):
            x, y = bounds_center(n)
            if x is not None and (best is None or x > best[0]):
                best = (x, n)
    if best:
        return tapn(best[1], "post-btn")
    return False

def adb_b64(text):
    # Type unicode/emoji/multiline text via ADBKeyBoard (one-time install + set as IME):
    #   adb install ADBKeyboard.apk
    #   adb shell ime enable com.android.adbkeyboard/.AdbIME
    #   adb shell ime set    com.android.adbkeyboard/.AdbIME
    # plain `input text` cannot type emojis or newlines, which her captions have.
    b = base64.b64encode(text.encode("utf-8")).decode("ascii")
    adb("shell", "am", "broadcast", "-a", "ADB_INPUT_B64", "--es", "msg", b)
    time.sleep(0.8)

# ---------------------------------------------------------------------------
# Proxy IP rotation  (IPRoyal residential via NekoBox)
# ---------------------------------------------------------------------------
# IPRoyal sticky-session creds. The country/session/lifetime modifiers go in
# the PASSWORD field (NOT the username): <base>_country-us_session-<TOKEN>_lifetime-24h.
# Each unique TOKEN resolves to its own US exit IP, so rotating TOKEN per account
# gives every login its own clean US IP -> defeats the IP-velocity flag.
PROXY_USER = "batbPp6Uzd8i9qHI"
PROXY_PASS = "71bfQNs2NQ9xVKvc"
# IPRoyal expects ISO codes; map friendly names so the operator can type "uk".
COUNTRY_ALIASES = {"usa": "us", "uk": "gb", "britain": "gb", "england": "gb",
                   "germany": "de", "canada": "ca", "australia": "au", "uae": "ae",
                   "france": "fr", "netherlands": "nl"}

# NekoBox UI coords (Note 8 / SM-N950F 1440x2960, discovered via probe.py)
NEKO_EDIT_BTN = (1056, 348)    # main list: profile Edit pencil  (desc 'Edit')
NEKO_CONNECT  = (720, 2756)    # main list: connect/disconnect toggle (desc 'Connect')
NEKO_PW_ROW   = (720, 1604)    # profile config: the Password row
NEKO_PW_FIELD = (720, 1403)    # password dialog: the EditText
NEKO_PW_OK    = (1236, 1553)   # password dialog: OK
NEKO_APPLY    = (1248, 168)    # profile config: Apply / save (desc 'Apply')

def _neko_fab(xml):
    """NekoBox's bottom connect/stop button. It MOVES between states, so locate
    it by content-desc, not a fixed coordinate. Returns (node, is_connected)."""
    for n in nodes(xml):
        if "ImageButton" not in attr(n, "class"):
            continue
        d = attr(n, "content-desc").strip().lower()
        if d == "stop":
            return n, True        # 'Stop' shows only while connected
        if d == "connect":
            return n, False
    return None, None

def _neko_connected(xml):
    n, conn = _neko_fab(xml)
    if conn is not None:
        return conn
    tb = text_block(xml)
    return ("connected, tap" in tb) or (" b/s" in tb)

def phone_exit_ip():
    """The device's REAL exit IP through whatever tunnel is up (LineageOS has
    curl). Returns (ip, country) or (None, None).

    Found 2026-07-03: any single free IP-lookup service rate-limits hard after
    enough calls in one session (ipinfo.io first, then ip-api.com too once we
    switched to it) - both fail SILENTLY (empty reply or a 429 body with no ip
    field), indistinguishable from a real dead tunnel and causing false
    ALL_IPS_MASKED failures on phones whose proxy was actually fine. Try a
    small chain of independent services so one service's rate limit doesn't
    read as a dead proxy."""
    services = [
        ("http://ip-api.com/json", r'"query":\s*"([^"]+)"', r'"countryCode":\s*"([^"]+)"'),
        ("https://ipapi.co/json/", r'"ip":\s*"([^"]+)"', r'"country_code":\s*"([^"]+)"'),
        ("https://ipinfo.io/json", r'"ip":\s*"([^"]+)"', r'"country":\s*"([^"]+)"'),
        ("https://ifconfig.co/json", r'"ip":\s*"([^"]+)"', r'"country_iso":\s*"([^"]+)"'),
        ("https://ipwho.is/", r'"ip":\s*"([^"]+)"', r'"country_code":\s*"([^"]+)"'),
        ("https://api.country.is/", r'"ip":\s*"([^"]+)"', r'"country":\s*"([^"]+)"'),
        ("https://freeipapi.com/api/json", r'"ipAddress":\s*"([^"]+)"', r'"countryCode":\s*"([^"]+)"'),
    ]
    # Found 2026-07-09/10: on a full-farm run ~15-18 phones call this at the same instant
    # and collectively rate-limit every service -> false ALL_IPS_MASKED (~47% of a burn)
    # on phones whose proxy was fine. Fix: each phone STARTS the chain at a different
    # service (offset by its serial) so the load spreads across 7 services (~2-3 phones
    # each) instead of all hammering one. Retry the rotated chain a few times.
    off = sum(ord(c) for c in (SERIAL or "x")) % len(services)
    chain = services[off:] + services[:off]
    for attempt in range(3):
        for url, ip_pat, cc_pat in chain:
            out = adb("shell", "curl", "-s", "--max-time", "12", url)
            ip = re.search(ip_pat, out or "")
            cc = re.search(cc_pat, out or "")
            if ip:
                return ip.group(1), (cc.group(1) if cc else None)
        time.sleep(1.0 + off * 0.2)
    return None, None

def _neko_back_to_main():
    # Back out of any open sub-screen/dialog until we're on the profile list.
    for _ in range(4):
        tb = text_block(dump())
        if "profile config" in tb or "password (optional)" in tb:
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1.0)
        else:
            return

def set_proxy_session(session, country="us", lifetime="24h"):
    """PC-SIDE proxy rotation. Replaces the old NekoBox-UI-tapping version, which was
    unreliable: it wrote the IPRoyal modifier password into NekoBox by tapping the
    on-screen form, and on phones whose NekoBox rendered at different coords the write
    silently missed -- so the phone kept a base (no-country) password and exited on a
    RANDOM country IP. Proven 2026-07-09: farm-side `_country-us` = US 100%, but the
    phone path returned random CA/RU/AR.

    Now the farm PC runs proxy_pool.py: one local SOCKS5 relay per phone that dials
    IPRoyal with the country/session/lifetime modifiers itself (reliable - no phone UI,
    no per-phone coords). Each phone's NekoBox just points at 127.0.0.1:1080 (bridged
    over USB by `adb reverse`) and stays connected. Rotating a phone's exit IP is now
    only: write a fresh session token to the relay's control file, then verify the exit
    IP from the phone. A token is sticky (same IP); a NEW token = a fresh IP on the next
    connection. Returns the verified exit IP if it matches `country`, else None so the
    caller rotates. Prereq: proxy_pool.py running + this phone's NekoBox pointed at the
    bridge (neko_localhost.py)."""
    import proxy_pool
    country = COUNTRY_ALIASES.get(country.strip().lower(), country.strip().lower())
    port = proxy_pool.port_for_serial(SERIAL)
    if port is None:
        print("[proxy] %s | no relay port (proxy_pool.py not running, or serial missing "
              "from device_ports.json)" % SERIAL)
        return None
    proxy_pool.set_session(port, session, country=country, lifetime=lifetime)
    print("[proxy] -> %s / session %s (PC relay 127.0.0.1:%d)" % (country, session, port))
    # keep the phone awake for the login that follows (no NekoBox UI is touched anymore)
    adb("shell", "settings", "put", "global", "stay_on_while_plugged_in", "3")
    if "mWakefulness=Awake" not in adb("shell", "dumpsys", "power"):
        adb("shell", "input", "keyevent", "KEYCODE_POWER")   # toggle - only if NOT already awake
        time.sleep(0.5)
    adb("shell", "wm", "dismiss-keyguard")
    # verify the real exit IP from the phone (fresh token => fresh upstream next connection)
    time.sleep(1.0)
    ip, cc = phone_exit_ip()
    if not ip:
        # A dead exit is often just a dropped USB reverse-tunnel (phone glitch/replug),
        # NOT a bad IP. Re-assert this phone's reverse and retry once before masking, so
        # an infra blip isn't silently logged as an IP mask (pre-burn audit finding).
        adb("reverse", "tcp:%d" % proxy_pool.PHONE_LOCAL_PORT, "tcp:%d" % port)
        time.sleep(1.0)
        ip, cc = phone_exit_ip()
    want = country.upper()
    # Any live exit IP is accepted (country mismatch is OK for this farm).
    # Only a missing exit = real tunnel/proxy problem.
    if not ip:
        ok = False
    else:
        ok = True
        if cc and cc.upper() != want:
            print("[proxy] exit country %s != want %s — accepted (any exit OK)"
                  % (cc, want))
        elif cc is None:
            print("[proxy] exit=%s but country-lookup unavailable - accepting IP"
                  % ip)
    print("[proxy] %s | exit=%s (%s) want=%s | session=%s"
          % ("OK" if ok else "FAIL", ip, cc, want, session))
    if not ok:
        # Loud ops cue — do NOT skip the phone forever; next run will retry.
        print("[FIX] %s: no exit IP — NekoBox ON + SOCKS 127.0.0.1:1080, "
              "USB plugged, proxy_pool running" % SERIAL)
    return ip if ok else None

# ---------------------------------------------------------------------------
# Post-login tip / permission dismiss (structural — not one Meta phrase at a time)
# ---------------------------------------------------------------------------
# IG cold login surfaces a long chain of optional sheets (nav tips, location,
# contacts, notifications, cookies, "set up on new device", …). Matching each
# phrase made the operator a dump-tester. Instead: if the screen has no login/
# 2FA/captcha chrome and exposes ONLY known-safe exact CTAs, treat as TIP_SHEET.

_SAFE_DISMISS = (
    "got it", "continue", "not now", "skip", "ok", "next", "done",
    "allow", "allow all", "allow all the time", "while using the app",
    "don't allow", "dont allow", "deny", "no thanks", "maybe later", "later",
    "agree and continue", "i agree", "accept", "accept all",
    "left side", "right side",  # reel camera toolbar tip (2026-08-03 dumps)
)

_TIP_BAN = (
    "continue creating account", "i already have a profile", "i already have an account",
    "join instagram", "log in to instagram", "log into instagram",
    "the password you entered", "enter the 6-digit", "enter the code from the image",
    "unable to log in", "suspended your account", "confirm you're human",
    "username, phone", "phone number, username",
)

# Reel create tips from farm dumps 2026-08-03 (misclassified as CREATE_CAMERA)
# Do NOT use bare "left side"/"right side" alone — too broad; dismiss still taps those CTAs.
_REEL_CREATE_TIP_PHRASES = (
    "save reels to device", "save reels to your phone", "automatically save your reels",
    "always start on front camera", "camera toolbar", "camera tools",
    "choose which side of the screen", "controls always start on front camera",
    "which side of the screen the camera tools",
    # 2026-08-03 siennasky58/shawnaoy234/arivra72/glowdream72: stuck UNKNOWN 32 steps
    "adjust preview size", "swipe up or down to adjust preview",
    # 2026-09-04 Jazleneguyu258 POST_TIMEOUT: sticker NUX covers Edit/Next
    "create a sticker", "turn part of any photo into a sticker",
    "sticker you can use in your reels", "into a sticker you can use",
)


def _node_bounds(n):
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 5))


def _clickable_exact_labels(xml):
    """[(label, node), ...] for safe dismiss CTAs.

    IG Compose tips often put label text on a non-clickable child TextView while the
    real tap target is parent `igds_media_button` (2026-08-03 preview-size Got it).
    """
    out = []
    seen = set()
    clickables = []
    for n in nodes(xml):
        if attr(n, "clickable") == "true":
            b = _node_bounds(n)
            if b:
                clickables.append((n, b))
        for raw in (attr(n, "text"), attr(n, "content-desc")):
            lab = raw.strip().lower()
            if lab not in _SAFE_DISMISS:
                continue
            if attr(n, "clickable") == "true":
                key = (lab, attr(n, "bounds"))
                if key not in seen:
                    seen.add(key)
                    out.append((lab, n))
                break
            # Non-clickable label — prefer smallest clickable ancestor by bounds
            cx, cy = bounds_center(n)
            if cx is None:
                break
            best = None
            for cn, (x1, y1, x2, y2) in clickables:
                if not (x1 <= cx <= x2 and y1 <= cy <= y2):
                    continue
                area = (x2 - x1) * (y2 - y1)
                if best is None or area < best[0]:
                    best = (area, cn)
            # Also: igds_media_button / Button near the label
            if best is None:
                for cn, _b in clickables:
                    rid = attr(cn, "resource-id").lower()
                    cls = attr(cn, "class").lower()
                    if "igds_media_button" in rid or cls.endswith("button"):
                        best = (0, cn)
                        break
            target = best[1] if best else n
            key = (lab, attr(target, "bounds"))
            if key not in seen:
                seen.add(key)
                out.append((lab, target))
            break
    return out


def _is_preview_size_tip(xml=None, tb=None):
    tb = (tb if tb is not None else text_block(xml or dump())).lower()
    return "adjust preview size" in tb or "swipe up or down to adjust preview" in tb


def dismiss_preview_size_tip(xml=None, label="preview-size-tip"):
    """Clear 'Swipe up or down to adjust preview size' / Got it overlay.

    Dump shape: Got it text is NOT clickable; parent igds_media_button is.
    """
    xml = xml or dump()
    if not _is_preview_size_tip(xml):
        return False
    print("[tip] preview-size coachmark — dismiss Got it")
    # Prefer mapped clickable parent from exact-label scan
    for lab, n in _clickable_exact_labels(xml):
        if lab == "got it":
            if tapn(n, "%s:%s" % (label, lab)):
                time.sleep(1.0)
                if not _is_preview_size_tip(dump()):
                    print("[tip] preview-size cleared")
                    return True
    if tap_exact(xml, "Got it", "OK", "Done", label="%s-exact" % label):
        time.sleep(1.0)
        if not _is_preview_size_tip(dump()):
            print("[tip] preview-size cleared via text tap")
            return True
    # Coord fallback on igds_media_button
    for n in nodes(xml):
        if "igds_media_button" in attr(n, "resource-id").lower():
            if tapn(n, "%s:media-btn" % label):
                time.sleep(1.0)
                if not _is_preview_size_tip(dump()):
                    return True
    if _tap_phrase(xml, "Got it", "OK", "Done", label="%s-phrase" % label,
                   prefer_clickable=False):
        time.sleep(1.0)
        return True
    return False


def _clickable_count(xml):
    return sum(1 for n in nodes(xml) if attr(n, "clickable") == "true")


def grant_media_permissions(pkg=None):
    """ADB-grant gallery perms so IG Recents works without 'Open settings' gate.

    emel29617 / bur_cu5439 2026-08-07 dumps: Create stuck on
    'Open settings, then tap permissions…' with primary_button Open settings.
    """
    pkg = pkg or CURRENT_PKG
    if not pkg:
        return
    for perm in (
            "android.permission.READ_EXTERNAL_STORAGE",
            "android.permission.WRITE_EXTERNAL_STORAGE",
            "android.permission.READ_MEDIA_IMAGES",
            "android.permission.READ_MEDIA_VIDEO",
            "android.permission.READ_MEDIA_VISUAL_USER_SELECTED",
    ):
        adb("shell", "pm", "grant", pkg, perm)
    print("[perm] granted media perms to %s" % pkg.split(".")[-1])


def _is_ig_media_settings_gate(xml=None):
    """IG in-app gate asking to Open settings for Photos permission."""
    xml = xml or dump()
    tb = text_block(xml)
    if "open settings" not in tb:
        return False
    return any(p in tb for p in (
        "permission", "photos", "media", "files and media", "storage",
        "tap permissions",
    ))


def _clear_ig_media_settings_gate(pkg=None):
    """Grant via adb + Back — do not dive into Android Settings UI."""
    if not _is_ig_media_settings_gate():
        return False
    print("[perm] IG media settings gate — pm grant + BACK (not Open settings)")
    grant_media_permissions(pkg)
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.2)
    # If still on gate, one more back
    if _is_ig_media_settings_gate():
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.0)
    return not _is_ig_media_settings_gate()


def dismiss_android_permission():
    """System permission dialogs only — exact CTAs. Never substring 'Allow' (2026-07-24
    mysticnova54: tap_first Allow @720,809 looped forever on a non-dialog control)."""
    for attempt in range(2):
        x2 = dump()
        if not is_android_permission_dialog(x2):
            return True
        tb = text_block(x2)
        if attempt == 0:
            print("[sys-perm] %s" % tb[:200])
        # Location is not required for gallery pick — Don't allow unblocks Create
        # (arzu16380 2026-08-11: Allow @720,1785 left the dialog up).
        if "location" in tb.lower():
            if tap_exact(x2, "While using the app", "Allow only this time",
                         "Only this time", label="sys-loc-while"):
                time.sleep(1.4)
                continue
            if tap_exact(x2, "Don't allow", "Dont allow", "Deny", label="sys-loc-deny"):
                time.sleep(1.4)
                continue
        if tap_exact(x2, "While using the app", "Allow all the time", "Allow all",
                     "Allow", "ALLOW", label="sys-allow"):
            time.sleep(1.5); continue
        # No exact Allow — try deny/back rather than substring spam
        if tap_exact(x2, "Don't allow", "Dont allow", "Deny", "No thanks", label="sys-deny"):
            time.sleep(1.5); continue
        print("[sys-perm] no exact Allow/Deny — BACK")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.2)
    return not is_android_permission_dialog(dump())


def is_android_permission_dialog(xml):
    """True only for real Android permission chrome (Allow + Don't allow / While using)."""
    labels = {lab for lab, _ in _clickable_exact_labels(xml)}
    if "while using the app" in labels or "allow all the time" in labels:
        return True
    has_allow = bool(labels & {"allow", "allow all", "allow all the time"})
    has_deny = bool(labels & {"don't allow", "dont allow", "deny"})
    return has_allow and has_deny


def dismiss_tip_sheet(xml=None, label="tip-dismiss", allow_next=True):
    """Tap the safest CTA on a tip/permission sheet (exact labels only).

    Never prefer Don't allow when Allow is available (breaks later gallery).
    During compose advance, set allow_next=False so we don't steal EDIT's Next.
    """
    xml = xml or dump()
    tb = text_block(xml)
    if _is_preview_size_tip(xml, tb):
        return dismiss_preview_size_tip(xml, label="%s-preview" % label)
    if _is_reel_create_tip_tb(tb):
        return _dismiss_reel_create_tip(xml, label=label)

    found = {lab: n for lab, n in _clickable_exact_labels(xml)}
    prefer_skip = (
        "not now", "skip", "maybe later", "later", "no thanks",
    )
    prefer_ack = ["got it", "ok", "done", "left side", "right side"]
    if allow_next:
        prefer_ack.append("next")
    prefer_ack += [
        "continue", "agree and continue", "i agree", "accept", "accept all",
    ]
    prefer_allow = (
        "while using the app", "allow all the time", "allow all", "allow",
    )
    prefer_deny = ("don't allow", "dont allow", "deny")
    for lab in list(prefer_skip) + prefer_ack + list(prefer_allow):
        if lab in found:
            return tapn(found[lab], "%s:%s" % (label, lab))
    if not any(a in found for a in prefer_allow):
        for lab in prefer_deny:
            if lab in found:
                return tapn(found[lab], "%s:%s" % (label, lab))
    # Last resort: exact Got it text (may be non-clickable child)
    if tap_exact(xml, "Got it", "OK", "Done", label="%s-gotit" % label):
        return True
    return False


def _dismiss_reel_create_tip(xml=None, label="reel-tip"):
    """Clear Save-reels / camera-toolbar tips and VERIFY they are gone.

    Farm dumps 2026-08-03:
      - Save reels: only Back is useful (Got it absent)
      - Camera tools: Left side / Right side text; tap may not clear — Back fallback
    Returning True only means we attempted a dismiss action; caller must re-detect.
    """
    xml = xml or dump()
    tb = text_block(xml)
    if not _is_reel_create_tip_tb(tb):
        return False
    # Sticker NUX on reel edit (covers Next). Never tap Try it.
    if "create a sticker" in tb or "into a sticker" in tb:
        print("[tip] Create a sticker NUX - Not now")
        if tap_exact(xml, "Not now", "Not Now", label="%s-sticker-notnow" % label):
            time.sleep(0.9)
            return True
        if _tap_phrase(xml, "Not now", "Not Now", label="%s-sticker-phrase" % label):
            time.sleep(0.9)
            return True
        for n in nodes(xml):
            d = attr(n, "content-desc").strip().lower()
            if d in ("close", "dismiss") and attr(n, "clickable") == "true":
                if tapn(n, "%s-sticker-close" % label):
                    time.sleep(0.9)
                    return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        print("[tip] KEYCODE_BACK sticker NUX")
        time.sleep(0.9)
        return True
    save_tip = any(p in tb for p in (
        "save reels to device", "save reels to your phone", "automatically save your reels"))
    cam_tip = any(p in tb for p in (
        "camera tools", "camera toolbar", "always start on front camera",
        "choose which side of the screen", "controls always start on front camera"))

    def _still_tip():
        return _is_reel_create_tip_tb(text_block(dump()))

    def _tap_back(x):
        for n in nodes(x):
            if attr(n, "clickable") != "true":
                continue
            d = attr(n, "content-desc").strip().lower()
            rid = attr(n, "resource-id").lower()
            # Never tap title "Reels" — only real back
            if d == "back" or "action_bar_button_back" in rid:
                return tapn(n, "%s:back" % label)
            if d == "back to home" and save_tip:
                return tapn(n, "%s:back-home" % label)
        return False

    if cam_tip:
        if _tap_phrase(xml, "Left side", "Right side", label="%s-side" % label):
            time.sleep(1.0)
            if not _still_tip():
                print("[tip] camera-tools cleared after Left/Right")
                return True
            xml2 = dump()
            if _tap_phrase(xml2, "Got it", "OK", "Done", "Continue", "Not now",
                           label="%s-ack" % label):
                time.sleep(0.8)
                if not _still_tip():
                    return True
        # Side tap did not clear — Back (stay in create when possible)
        xml3 = dump()
        if _tap_back(xml3):
            time.sleep(1.0)
            if not _still_tip():
                print("[tip] camera-tools cleared via Back")
                return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        print("[tip] KEYCODE_BACK camera-tools")
        time.sleep(1.0)
        return True

    if save_tip:
        if _tap_phrase(xml, "Got it", "OK", "Not now", "Skip", "Continue",
                       label="%s-save-ack" % label):
            time.sleep(0.8)
            if not _still_tip():
                return True
        if _tap_back(xml):
            time.sleep(1.0)
            if not _still_tip():
                print("[tip] save-reels cleared via Back")
                return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        print("[tip] KEYCODE_BACK save-reels")
        time.sleep(1.0)
        return True

    # Other reel tip phrases
    if _tap_phrase(xml, "Got it", "OK", "Not now", "Skip", "Continue",
                   "Left side", "Right side", label=label):
        time.sleep(0.8)
        return True
    if _tap_back(xml):
        time.sleep(1.0)
        return True
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    print("[tip] KEYCODE_BACK reel tip")
    time.sleep(1.0)
    return True


def is_tip_sheet(xml, tb=None, nedits=None):
    """Sparse post-login tip/permission UI with a known-safe exact CTA."""
    tb = text_block(xml) if tb is None else tb
    if nedits is None:
        nedits = len(edits(xml))
    if any(p in tb for p in _REEL_CREATE_TIP_PHRASES):
        return True
    if _is_preview_size_tip(xml, tb):
        return True
    if nedits >= 2:
        return False
    if nedits >= 1 and "password" in tb:
        return False
    if any(b in tb for b in _TIP_BAN):
        return False
    # Never steal create/edit/picker chrome (2026-07-24: EDIT was TIP_SHEET → Next chaos)
    if any(p in tb for p in (
            "your story", "suggested for you", "what do you want to share",
            "write a caption", "add a caption", "recents", "gallery", "camera roll",
            "new post", "filter", "trim", "edit photo", "tag people", "add location",
            "multi-select", "select multiple",
            "modern text style", "classic text style", "text color", "stickers")):
        # Exception: tip overlay copy can include "reels" without being gallery
        if not any(p in tb for p in _REEL_CREATE_TIP_PHRASES):
            return False
    if _clickable_count(xml) > 10:
        return False
    return bool(_clickable_exact_labels(xml))


def _is_reel_create_tip_tb(tb):
    tb = (tb or "").lower()
    return any(p in tb for p in _REEL_CREATE_TIP_PHRASES)


def _is_android_share_sheet(xml=None, tb=None):
    """System resolver: Share → Insta09xx + Just once / Always (SEND bypass dumps)."""
    tb = text_block(xml) if tb is None else (tb or "")
    if "just once" not in tb and "only once" not in tb:
        return False
    if "always" not in tb:
        return False
    # Prefer share/open-with chrome; avoid random IG screens that say Always
    return ("share" in tb or "open with" in tb or "complete action using" in tb
            or "insta" in tb)


def _handle_android_share_sheet(xml=None, pkg=None):
    """Tap Just once / Always. Verify share sheet is gone; BACK if stuck."""
    xml = xml or dump()
    tb = text_block(xml)
    if not _is_android_share_sheet(xml, tb):
        return False
    pkg = pkg or CURRENT_PKG or ""
    short = (pkg.split(".")[-1] if pkg else "") or ""
    _rt_log("share_sheet_open", snippet=tb, pkg_short=short)

    # Dumps: often only Just once / Always are clickable — try those first
    if tap_exact(xml, "Just once", "Only once", "JUST ONCE", label="share-once") or \
       _tap_phrase(xml, "Just once", "Only once", label="share-once"):
        time.sleep(2.5)
        xml2 = dump()
        if not _is_android_share_sheet(xml2):
            print("[share] Just once cleared sheet")
            _rt_log("share_sheet_cleared", via="just_once")
            return True
        # Second sheet: "Share with Insta09xx"
        if tap_exact(xml2, "Just once", "Only once", label="share-once-2") or \
           _tap_phrase(xml2, "Just once", "Only once", label="share-once-2"):
            time.sleep(2.5)
            if not _is_android_share_sheet(dump()):
                _rt_log("share_sheet_cleared", via="just_once_2")
                return True

    xml = dump()
    if short:
        for n in nodes(xml):
            blob = (attr(n, "text") + " " + attr(n, "content-desc")).lower()
            if short.lower() in blob or (pkg and pkg.lower() in blob):
                if attr(n, "clickable") == "true":
                    tapn(n, "share-target:%s" % short[:12])
                    time.sleep(0.8)
                    break
    if _tap_phrase(dump(), "Reels", "Reel", "Feed", "Instagram", label="share-row"):
        time.sleep(0.8)
    xml = dump()
    if tap_exact(xml, "Just once", "Only once", label="share-once-3") or \
       tap_exact(xml, "Always", "ALWAYS", label="share-always"):
        time.sleep(2.5)
        gone = not _is_android_share_sheet(dump())
        _rt_log("share_sheet_cleared" if gone else "share_sheet_stuck", via="fallback")
        if gone:
            return True

    print("[share] FAIL stuck on Android share — BACK")
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.0)
    _rt_log("share_sheet_abort")
    return False


def dismiss_media_permission():
    """Back-compat name used by gallery picker."""
    dismiss_android_permission()


def _ensure_postable_image(path):
    """IG gallery Next often stalls on webp. Convert to PNG when needed."""
    low = path.lower()
    if low.endswith((".png", ".jpg", ".jpeg")):
        return path
    if not low.endswith(".webp"):
        return path
    out = path.rsplit(".", 1)[0] + ".png"
    # 1) Pillow
    try:
        from PIL import Image
        Image.open(path).convert("RGB").save(out, "PNG")
        print("[img] converted webp -> %s (PIL)" % out)
        return out
    except Exception as e:
        print("[img] PIL convert failed: %s" % type(e).__name__)
    # 2) ffmpeg (common on farm PCs)
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", path, out],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
            print("[img] converted webp -> %s (ffmpeg)" % out)
            return out
        print("[img] ffmpeg convert failed rc=%s" % r.returncode)
    except Exception as e:
        print("[img] ffmpeg not usable: %s" % type(e).__name__)
    print("[img] WARN: posting webp as-is — Next may stall; add a .png/.jpg to Drive")
    return path


def _make_gallery_remote_name(local_path, prefix="ig"):
    """On-device basename — delegates to ig_media_names (shared with Drive download)."""
    if mn is not None:
        return mn.remote_basename(local_path, prefix=prefix, serial=SERIAL or "")
    raw_base = os.path.basename(local_path or "")
    stem, ext = os.path.splitext(raw_base)
    safe = re.sub(r"[^\w.\-]+", "_", stem).strip("_") or "media"
    ext = (ext or ".jpg").lower()
    if ext == ".jpeg":
        ext = ".jpg"
    tag = "%d" % int(time.time() * 1000)
    ser = re.sub(r"[^\w]+", "", (SERIAL or "")[-6:]) or "dev"
    return "%s_%s_%s_%s%s" % (prefix, ser, tag, safe[:40], ext)


def push_image(image_path):
    """Push image into gallery with a safe unique name + verify + MediaStore scan.

    Pictures primary + DCIM/Camera mirror (same pattern as reel Movies/DCIM).
    """
    global CURRENT_REMOTE_IMG, CURRENT_REMOTE_STEM
    image_path = _ensure_postable_image(os.path.abspath(image_path))
    remote_name = _make_gallery_remote_name(image_path, prefix="ig")
    remotes = [
        "/sdcard/Pictures/" + remote_name,
        "/sdcard/DCIM/Camera/" + remote_name,
    ]
    print("[img] push %s -> %s (+ DCIM)  [was %s]"
          % (image_path, remotes[0], os.path.basename(image_path)))
    for remote in remotes:
        adb("shell", "mkdir", "-p", os.path.dirname(remote))
        adb("push", image_path, remote)
        _media_scan_file(remote)
    adb("shell", "am", "broadcast", "-a",
        "android.intent.action.MEDIA_MOUNTED",
        "-d", "file:///sdcard/Pictures")
    CURRENT_REMOTE_IMG = remotes[0]
    CURRENT_REMOTE_STEM = os.path.splitext(remote_name)[0].lower()
    if not _remote_file_ok(remotes[0]):
        adb("push", image_path, remotes[0])
        _media_scan_file(remotes[0])
        time.sleep(1.0)
        if not _remote_file_ok(remotes[0]):
            print("[img] FAIL push verify — cannot pick this image")
            CURRENT_REMOTE_IMG = None
            CURRENT_REMOTE_STEM = None
            return None
    _remote_file_ok(remotes[1])  # best-effort mirror
    time.sleep(2.5)  # MediaStore settle before Create/picker
    print("[img] ready stem=%s remote=%s" % (CURRENT_REMOTE_STEM, CURRENT_REMOTE_IMG))
    return remotes[0]


def _remote_file_ok(remote=None):
    """True if pushed file exists on device with non-trivial size (purpose: pick THIS file)."""
    remote = remote or CURRENT_REMOTE_IMG
    if not remote:
        return False
    # ls -l: look for size field; also try stat -c %s
    out = adb("shell", "ls", "-l", remote)
    if out and remote.split("/")[-1] in out and "No such file" not in out:
        # rough size: skip zero-length
        m = re.search(r"\s(\d+)\s+\d{4}-\d{2}-\d{2}", out) or re.search(r"\s(\d+)\s+[A-Z][a-z]{2}", out)
        if m and int(m.group(1)) < 1000:
            print("[media] WARN remote tiny/empty: %s (%s)" % (remote, out.strip()[:80]))
            return False
        if "No such" not in out:
            print("[media] on-device OK: %s" % out.strip()[:100])
            return True
    sz = adb("shell", "stat", "-c", "%s", remote).strip()
    if sz.isdigit() and int(sz) >= 1000:
        print("[media] on-device OK size=%s %s" % (sz, remote))
        return True
    print("[media] FAIL not on device: %s (%s)" % (remote, (out or sz or "")[:80]))
    return False


def _media_scan_file(remote):
    if not remote:
        return
    # Safe names only (no spaces) — still quote-free for am broadcast -d
    adb("shell", "am", "broadcast", "-a",
        "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
        "-d", "file://" + remote)


def push_media(path):
    """Push image or video into gallery (Reels / Stories video / feed photo).

    Purpose: file must exist on device AND become pickable in IG Recents.
    Unique safe remote name per push (no spaces/parens).
    """
    global CURRENT_REMOTE_IMG, CURRENT_REMOTE_STEM
    path = os.path.abspath(path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".mp4", ".mov", ".webm", ".mkv"):
        remote_name = _make_gallery_remote_name(path, prefix="reel")
        remotes = [
            "/sdcard/Movies/" + remote_name,
            "/sdcard/DCIM/Camera/" + remote_name,
        ]
        print("[vid] push %s -> %s (+ DCIM)  [was %s]"
              % (path, remotes[0], os.path.basename(path)))
        for remote in remotes:
            adb("shell", "mkdir", "-p", os.path.dirname(remote))
            adb("push", path, remote)
            _media_scan_file(remote)
        adb("shell", "am", "broadcast", "-a",
            "android.intent.action.MEDIA_MOUNTED",
            "-d", "file:///sdcard/Movies")
        CURRENT_REMOTE_IMG = remotes[0]
        CURRENT_REMOTE_STEM = os.path.splitext(remote_name)[0].lower()
        # Verify on-device — without this, pick/SEND is theater
        if not _remote_file_ok(remotes[0]):
            # Retry once to Movies only
            adb("push", path, remotes[0])
            _media_scan_file(remotes[0])
            time.sleep(1.0)
            if not _remote_file_ok(remotes[0]):
                print("[vid] FAIL push verify — cannot post this reel")
                CURRENT_REMOTE_IMG = None
                CURRENT_REMOTE_STEM = None
                return None
        # Also confirm DCIM copy (best-effort)
        _remote_file_ok(remotes[1])
        time.sleep(3.0)  # MediaStore settle before open create
        print("[vid] ready stem=%s remote=%s" % (CURRENT_REMOTE_STEM, CURRENT_REMOTE_IMG))
        return remotes[0]
    return push_image(path)


def cleanup_pushed_media():
    """Delete on-phone gallery copies after the job. Session/login stays.

    Drive originals are not touched here. Call after do_post returns so Recents
    does not keep growing (wrong-tile risk + phone disk + no extra CDN later).
    """
    global CURRENT_REMOTE_IMG, CURRENT_REMOTE_STEM
    paths = []
    cur = CURRENT_REMOTE_IMG or ""
    if cur:
        paths.append(cur)
        if "/Movies/" in cur:
            paths.append(cur.replace("/Movies/", "/DCIM/Camera/"))
        if "/Pictures/" in cur:
            paths.append(cur.replace("/Pictures/", "/DCIM/Camera/"))
        if "/DCIM/Camera/" in cur:
            paths.append(cur.replace("/DCIM/Camera/", "/Movies/"))
            paths.append(cur.replace("/DCIM/Camera/", "/Pictures/"))
    seen = set()
    n = 0
    for remote in paths:
        if not remote or remote in seen:
            continue
        seen.add(remote)
        try:
            adb("shell", "rm", "-f", remote)
            n += 1
        except Exception:
            pass
        try:
            _media_scan_file(remote)
        except Exception:
            pass
    if n:
        print("[media] cleaned %d on-phone cop(ies) stem=%s"
              % (n, CURRENT_REMOTE_STEM or "?"))
    CURRENT_REMOTE_IMG = None
    CURRENT_REMOTE_STEM = None
    return n


def _remote_is_video(path=None):
    p = (path or CURRENT_REMOTE_IMG or "").lower()
    return p.endswith((".mp4", ".mov", ".webm", ".mkv", ".m4v"))


def _media_mime(path=None):
    p = (path or CURRENT_REMOTE_IMG or "").lower()
    if p.endswith(".mp4") or p.endswith(".m4v"):
        return "video/mp4"
    if p.endswith(".mov"):
        return "video/quicktime"
    if p.endswith(".webm"):
        return "video/webm"
    if p.endswith(".mkv"):
        return "video/x-matroska"
    if p.endswith(".png"):
        return "image/png"
    if p.endswith(".webp"):
        return "image/webp"
    if p.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "image/*"


def _desc_is_selected(d):
    """True only for a truly selected gallery tile.

    CRITICAL (2026-08-03 dumps): content-desc is 'Unselected Video thumbnail…'.
    The substring 'selected' appears inside 'unselected' — never use bare
    ``'selected' in d``. Prefer startswith / word form, exclude unselected.
    """
    d = (d or "").lower().strip()
    if not d or "unselected" in d:
        return False
    if d.startswith("selected"):
        return True
    if "selected video" in d or "selected photo" in d or "selected image" in d:
        return True
    # Word-boundary selected (does not match inside unselected)
    return bool(re.search(r"\bselected\b", d))


def _desc_is_video_tile(d):
    d = (d or "").lower()
    return ("video thumbnail" in d or "video," in d or
            ("video" in d and "thumbnail" in d) or
            d.startswith("video ") or "unselected video" in d or
            "selected video" in d)


def _desc_is_photo_tile(d):
    d = (d or "").lower()
    return ("photo thumbnail" in d or "photo taken" in d or
            "unselected photo" in d or "selected photo" in d or
            ("image" in d and "video" not in d))


def _gallery_trace_summary(xml=None):
    """Compact picker facts for reel JSONL (tiles / Next / selection)."""
    xml = xml or dump()
    unsel_v = sel_v = unsel_p = sel_p = 0
    next_n = 0
    next_en = 0
    sample = []
    for n in nodes(xml):
        d = attr(n, "content-desc")
        dl = d.lower()
        if attr(n, "clickable") == "true" and ("thumbnail" in dl or "video" in dl or "photo" in dl):
            if _desc_is_video_tile(dl):
                if _desc_is_selected(dl):
                    sel_v += 1
                else:
                    unsel_v += 1
            elif _desc_is_photo_tile(dl):
                if _desc_is_selected(dl):
                    sel_p += 1
                else:
                    unsel_p += 1
            if len(sample) < 4 and d:
                sample.append(d[:70])
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        cd = attr(n, "content-desc").strip().lower()
        if "next" in rid or t == "next" or cd == "next":
            next_n += 1
            if attr(n, "enabled").lower() in ("", "true"):
                next_en += 1
    tb = text_block(xml)
    return {
        "unsel_video": unsel_v,
        "sel_video": sel_v,
        "unsel_photo": unsel_p,
        "sel_photo": sel_p,
        "next_btns": next_n,
        "next_enabled": next_en,
        "has_video_preview": "video preview" in tb,
        "has_photo_preview": "photo preview" in tb,
        "tiles": sample,
    }


def _video_really_selected(xml=None):
    """Reel may advance only after a real video selection (not Unselected*)."""
    xml = xml or dump()
    tb = text_block(xml)
    if "video preview" in tb:
        return True
    for n in nodes(xml):
        d = attr(n, "content-desc").lower()
        if _desc_is_selected(d) and _desc_is_video_tile(d):
            return True
    return False


def _picker_has_next(xml=None):
    return bool(_next_button_nodes(xml or dump()))


def _reel_pick_succeeded(xml=None):
    """Selection worked if Selected* OR Next appeared OR video preview.

    IG sometimes shows Next before content-desc flips Unselected→Selected.
    """
    xml = xml or dump()
    if _video_really_selected(xml):
        return True, "selected_desc"
    if _picker_has_next(xml):
        return True, "next_appeared"
    if "video preview" in text_block(xml):
        return True, "video_preview"
    return False, ""


def _parse_bounds(n):
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 5))


def _list_gallery_video_tiles(xml=None, only_unselected=True):
    """Return [(score, y, x, node, desc, bounds), ...] — prefer OUR pushed video.

    Purpose: pick the file we just pushed (stem match / newest), not a random old clip.
    """
    xml = xml or dump()
    base = os.path.basename(CURRENT_REMOTE_IMG or "").lower()
    stem = (CURRENT_REMOTE_STEM or (base.rsplit(".", 1)[0] if base else "")).lower()
    # IG content-desc: "Unselected Video thumbnail created on August 4, 2026 …"
    today_phrases = ()
    try:
        import datetime as _dt
        today = _dt.date.today()
        mon = today.strftime("%B").lower()
        mon3 = today.strftime("%b").lower()
        day = str(today.day)  # no leading zero (matches IG)
        year = str(today.year)
        today_phrases = (
            "%s %s, %s" % (mon, day, year),
            "%s %s, %s" % (mon3, day, year),
            "%s-%02d-%02d" % (int(year), today.month, today.day),
        )
    except Exception:
        today_phrases = ()

    out = []
    seen_bounds = set()
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        if not _desc_is_video_tile(d):
            continue
        if only_unselected and _desc_is_selected(d):
            continue
        if not only_unselected and not _desc_is_selected(d):
            continue
        b = _parse_bounds(n)
        if not b:
            continue
        if b in seen_bounds:
            continue
        seen_bounds.add(b)
        x1, y1, x2, y2 = b
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        score = 0
        if "second" in d:
            score = 400
        elif "minute" in d:
            score = 300
        elif "hour" in d:
            score = 200
        elif "today" in d:
            score = 350
        elif "day" in d or "yesterday" in d:
            score = 100
        for phrase in today_phrases:
            if phrase and phrase in d:
                score += 600
                break
        if stem and len(stem) >= 6 and stem in d:
            score += 800  # strong: desc mentions our filename
        if stem and "reel_" in stem and "reel_" in d:
            score += 200
        if "unselected video" in d:
            score += 50
        area = max(0, x2 - x1) * max(0, y2 - y1)
        score += min(area // 5000, 80)
        # Prefer top-left among equals (newest often first)
        out.append((score, -cy, -cx, n, d, b))
    out.sort(reverse=True)
    return out


def _tap_video_tile_strategy(node, bounds, strategy):
    """Tap tile using strategy 0=center, 1=checkmark TR, 2=upper-third, 3=double-center."""
    x1, y1, x2, y2 = bounds
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    w, h = max(1, x2 - x1), max(1, y2 - y1)
    if strategy == 0:
        print("[tap] video-center @ %d,%d" % (cx, cy))
        tap(cx, cy)
    elif strategy == 1:
        tx, ty = x2 - max(30, w // 5), y1 + max(30, h // 5)
        print("[tap] video-checkmark @ %d,%d" % (tx, ty))
        tap(tx, ty)
    elif strategy == 2:
        tx, ty = cx, y1 + h // 3
        print("[tap] video-upper @ %d,%d" % (tx, ty))
        tap(tx, ty)
    else:
        print("[tap] video-double-center @ %d,%d" % (cx, cy))
        tap(cx, cy)
        time.sleep(0.35)
        tap(cx, cy)
    return True


def pick_reel_video(max_attempts=10):
    """Multi-attempt Reel video select — must succeed for LOGGED_IN/POST_DONE.

    Prefer OUR pushed stem; wait for gallery index; never claim success without
    selected / Next / video preview proof.
    """
    dismiss_media_permission()
    # Gate: our file must be on device
    if CURRENT_REMOTE_IMG and not _remote_file_ok(CURRENT_REMOTE_IMG):
        print("[reel-pick] FAIL — pushed video missing on device")
        _rt_log("reel_pick_done", ok=False, reason="remote_missing")
        return False

    if not _wait_for_gallery_video(max_rounds=3):
        print("[reel-pick] FAIL — no video tile after wait/folder/scan")
        _rt_log("reel_pick_done", ok=False, reason="no_tiles_after_wait",
                **_gallery_trace_summary())
        return False

    xml = dump()
    g0 = _gallery_trace_summary(xml)
    _rt_log("reel_pick_start", stem=CURRENT_REMOTE_STEM or "", remote=CURRENT_REMOTE_IMG or "",
            **g0)

    ok, why = _reel_pick_succeeded(xml)
    if ok:
        print("[reel-pick] already ready (%s)" % why)
        _rt_log("reel_pick_done", ok=True, reason=why, attempts=0, **g0)
        return True

    tiles = _list_gallery_video_tiles(xml, only_unselected=True)
    if not tiles:
        print("[reel-pick] no unselected video tiles")
        _rt_log("reel_pick_done", ok=False, reason="no_tiles", **g0)
        return False

    print("[reel-pick] %d video tile(s) stem=%s" % (len(tiles), CURRENT_REMOTE_STEM or "?"))
    for i, t in enumerate(tiles[:5]):
        print("   [%d] score=%s bounds=%s desc=%s" % (i, t[0], t[5], t[4][:55]))

    for attempt in range(max_attempts):
        tiles = _list_gallery_video_tiles(dump(), only_unselected=True)
        if not tiles:
            ok, why = _reel_pick_succeeded(dump())
            if ok:
                _rt_log("reel_pick_done", ok=True, reason=why, attempts=attempt + 1)
                return True
            print("[reel-pick] tiles disappeared mid-attempt")
            break

        tile_i = attempt % len(tiles)
        strategy = (attempt // max(1, len(tiles))) % 4
        _score, _ny, _nx, node, desc, bounds = tiles[tile_i]
        print("[reel-pick] attempt %d/%d tile=%d strat=%d score=%s desc=%s"
              % (attempt + 1, max_attempts, tile_i, strategy, _score, desc[:50]))
        _rt_log("reel_pick_try", attempt=attempt + 1, tile=tile_i, strategy=strategy,
                score=_score, desc=desc[:80], bounds=list(bounds))

        _tap_video_tile_strategy(node, bounds, strategy)
        time.sleep(1.8)

        xml2 = dump()
        g2 = _gallery_trace_summary(xml2)
        ok, why = _reel_pick_succeeded(xml2)
        _rt_log("reel_pick_check", attempt=attempt + 1, ok=ok, reason=why, **g2)
        if ok:
            print("[reel-pick] SUCCESS via %s (attempt %d)" % (why, attempt + 1))
            _rt_log("reel_pick_done", ok=True, reason=why, attempts=attempt + 1, **g2)
            return True

        if g2.get("has_photo_preview") and not g2.get("has_video_preview"):
            print("[reel-pick] photo preview — back out of bad pick")
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.8)

    gF = _gallery_trace_summary(dump())
    print("[reel-pick] FAIL after %d attempts %s" % (max_attempts, gF))
    _rt_log("reel_pick_done", ok=False, reason="exhausted", attempts=max_attempts, **gF)
    return False

def pick_newest_photo(prefer_video=None):
    """Select gallery tile. Reels → pick_reel_video. Photos → wait for MediaStore then tap."""
    if prefer_video is None:
        prefer_video = _remote_is_video()
    if prefer_video:
        return pick_reel_video(max_attempts=8)

    dismiss_media_permission()
    if _is_ig_media_settings_gate():
        _clear_ig_media_settings_gate(CURRENT_PKG)
    base = os.path.basename(CURRENT_REMOTE_IMG or "").lower()
    stem = (CURRENT_REMOTE_STEM or (base.rsplit(".", 1)[0] if base else "")).lower()

    def _score_photo_tile(n, d):
        is_photo = _desc_is_photo_tile(d)
        is_video = _desc_is_video_tile(d)
        if not is_photo and not is_video:
            if not (base and (base in d or stem and stem in d)):
                if not (stem and any(p in d for p in stem.split("_") if len(p) >= 6)):
                    return None
        score = 0
        if "second" in d: score = 400
        elif "minute" in d: score = 300
        elif "hour" in d: score = 200
        elif "day" in d or "yesterday" in d: score = 100
        if is_photo: score += 250
        if base and base in d: score += 600
        if stem and stem in d: score += 500
        # Prefer our ig_ push token in desc when MediaStore exposes filename
        if "ig_" in d or (stem and stem[:12] in d):
            score += 200
        x, y = bounds_center(n)
        if x is None:
            return None
        return (score, -y, -x)

    for attempt in range(6):
        if attempt > 0:
            if CURRENT_REMOTE_IMG:
                _media_scan_file(CURRENT_REMOTE_IMG)
                dcim = CURRENT_REMOTE_IMG.replace("/Pictures/", "/DCIM/Camera/")
                if dcim != CURRENT_REMOTE_IMG:
                    _media_scan_file(dcim)
            time.sleep(1.5)
        xml = dump()
        tb = text_block(xml)
        if _is_ig_media_settings_gate(xml):
            _clear_ig_media_settings_gate(CURRENT_PKG)
            continue
        gsum = _gallery_trace_summary(xml)
        _rt_log("pick_scan", prefer_video=False, remote=CURRENT_REMOTE_IMG or "",
                stem=stem, attempt=attempt + 1, **gsum)

        if "photo preview" in tb or "video preview" in tb:
            print("[img] preview already showing — treat as selected")
            return True
        for n in nodes(xml):
            d = attr(n, "content-desc").lower()
            if _desc_is_selected(d) and attr(n, "clickable") == "true":
                if _desc_is_photo_tile(d) or _desc_is_video_tile(d):
                    print("[img] already selected: %s" % d[:60])
                    return True

        newest = None
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            d = attr(n, "content-desc").lower()
            if _desc_is_selected(d):
                continue
            key = _score_photo_tile(n, d)
            if key is None:
                continue
            if newest is None or key > newest[0]:
                newest = (key, n, d)
        if newest and newest[0][0] > 0:
            tapn(newest[1], "pick-newest:%s" % newest[2][:40])
            time.sleep(1.5)
            print("[img] media tapped (attempt %d)" % (attempt + 1))
            return True
        print("[img] no photo thumbnail yet (attempt %d/6) tiles=%s"
              % (attempt + 1, gsum.get("tiles")))

    print("[img] FAIL: no photo thumbnail in picker after wait/rescan")
    if rt is not None:
        _rt_fail("photo_pick_fail", remote=CURRENT_REMOTE_IMG or "", stem=stem,
                 **_gallery_trace_summary())
    return False


def _create_dest_tabs(xml=None):
    """Bottom POST / STORY / REEL / LIVE chips. Skip full-bleed containers.

    Layout (Nomix): POST, then STORY one slot to the right (Tijana 2026-08-30).
    """
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    tabs = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        x, y = bounds_center(n)
        if x is None or y is None or sh <= 0:
            continue
        if y < sh * 0.80:
            continue
        w, h = bounds_wh(n)
        if w > sw * 0.42 or h > 280:
            continue
        kind = None
        if rid.endswith("cam_dest_feed") or t == "post":
            kind = "feed"
        elif rid.endswith("cam_dest_story") or t == "story":
            kind = "story"
        elif rid.endswith("cam_dest_clips") or t == "reel":
            kind = "reel"
        elif rid.endswith("cam_dest_live") or t == "live":
            kind = "live"
        if not kind:
            continue
        sel = attr(n, "selected").lower() == "true" or attr(n, "checked").lower() == "true"
        tabs.append({"kind": kind, "x": x, "y": y, "n": n, "sel": sel, "w": w})
    tabs.sort(key=lambda r: r["x"])
    return tabs


def _ensure_create_dest(xml=None, dest="feed", force=False):
    """Ensure create destination: feed (POST) | story | reel. Tap once per run."""
    dest = (dest or "feed").lower()
    if dest == "carousel":
        dest = "feed"
    xml = xml or dump()
    tabs = _create_dest_tabs(xml)
    posts = [t for t in tabs if t["kind"] == "feed"]
    stories = [t for t in tabs if t["kind"] == "story"]
    reels = [t for t in tabs if t["kind"] == "reel"]
    post_x = posts[0]["x"] if posts else None

    want_n = None
    want_sel = False
    if dest == "feed" and posts:
        posts.sort(key=lambda t: t["w"])
        want_n, want_sel = posts[0]["n"], posts[0]["sel"]
    elif dest == "reel" and reels:
        reels.sort(key=lambda t: t["w"])
        want_n, want_sel = reels[0]["n"], reels[0]["sel"]
    elif dest == "story":
        # Must be the chip immediately to the RIGHT of POST — never POST itself.
        right_stories = [t for t in stories if post_x is None or t["x"] > post_x + 50]
        if right_stories:
            right_stories.sort(key=lambda t: t["x"])
            want_n, want_sel = right_stories[0]["n"], right_stories[0]["sel"]
        elif post_x is not None:
            right = [t for t in tabs if t["x"] > post_x + 50]
            if right:
                right.sort(key=lambda t: t["x"])
                want_n, want_sel = right[0]["n"], right[0]["sel"]
                print("[picker] STORY tab = next chip right of POST @ %d → %d"
                      % (post_x, right[0]["x"]))
        elif stories:
            stories.sort(key=lambda t: t["x"])
            want_n, want_sel = stories[0]["n"], stories[0]["sel"]

    label = {"feed": "POST", "story": "STORY", "reel": "REEL"}.get(dest, "POST")

    if want_sel and not force:
        print("[picker] %s dest already selected" % label)
        return True
    if not force and getattr(_ensure_create_dest, "_done", None) == dest:
        return True
    if want_n:
        x, y = bounds_center(want_n)
        print("[picker] dest tabs: %s" % ", ".join(
            "%s@%d%s" % (t["kind"], t["x"], "*" if t["sel"] else "") for t in tabs))
        ok = tapn(want_n, "create-dest-%s" % label)
        _ensure_create_dest._done = dest
        time.sleep(0.9)
        return ok
    ok = tap_exact(xml, label, label.title(), label.lower(), label="create-dest-%s" % label)
    if not ok and dest == "story":
        ok = _vision_tap(
            "STORY tab",
            question="Create picker bottom row is POST, STORY, REEL, LIVE. "
                     "STORY is one tab to the right of POST. Tap STORY, not POST.",
            tag="dest_story")
    if ok:
        _ensure_create_dest._done = dest
        time.sleep(0.9)
    return ok


def _ensure_create_feed_dest(xml=None, force=False):
    """Back-compat wrapper → POST/feed dest."""
    return _ensure_create_dest(xml, dest="feed", force=force)


def _is_media_preview_overlay(xml=None):
    """Full-screen photo Preview (Back + Preview) — not caption. nila/aylin 2026-08-11."""
    xml = xml or dump()
    tb = text_block(xml).lower()
    if "preview" not in tb or "back" not in tb:
        return False
    if any(p in tb for p in (
            "add a caption", "write a caption", "tag people", "add location",
            "recents", "select multiple", "photo thumbnail")):
        return False
    return True


def _dismiss_media_preview(xml=None, label="media-preview"):
    """Leave full-screen Preview back to caption/picker."""
    xml = xml or dump()
    if not _is_media_preview_overlay(xml):
        return False
    print("[pub] media Preview overlay — Back to composer")
    if tap_exact(xml, "Back", "Close", "X", label="%s-back" % label):
        time.sleep(0.9)
        return True
    for n in nodes(xml):
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        rid = attr(n, "resource-id").lower()
        x, y = bounds_center(n)
        if y is not None and y < 400 and (
                d in ("back", "close", "navigate up") or t in ("back", "close")
                or "back" in rid):
            if tapn(n, "%s-chrome" % label):
                time.sleep(0.9)
                return True
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.9)
    return True


def _multi_select_node(xml=None):
    """Gallery Select / Select multiple control (toolbar, not a tile)."""
    xml = xml or dump()
    _sw, sh = _screen_wh(xml)
    best = None
    for n in nodes(xml):
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        rid = attr(n, "resource-id").lower()
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        blob = " ".join([d, t, rid])
        if any(p in blob for p in (
                "select multiple", "multi_select", "multi-select",
                "gallery_select", "select_multiple")):
            return n
        # Visible label is often just "Select" next to overlapping-squares
        if t in ("select", "select multiple") or d in ("select", "select multiple"):
            if 0.48 * sh <= y <= 0.72 * sh and x > (_sw or 1080) * 0.55:
                return n
        if "select" in rid and "multi" in rid:
            return n
        if best is None and t == "select" and y < int(sh * 0.75):
            best = n
    return best


def _multi_select_is_on(xml=None):
    """True when carousel multi-select is actually armed (not single-select pre-pick).

    Single-select opens with one 'Selected Photo thumbnail' — that is NOT multi mode
    (handeisik346 / arzu16380 2026-08-12 log dumps).
    """
    xml = xml or dump()
    n = _multi_select_node(xml)
    if n is not None:
        if attr(n, "selected").lower() == "true" or attr(n, "checked").lower() == "true":
            return True
        d = attr(n, "content-desc").lower()
        t = attr(n, "text").strip().lower()
        if "deselect" in d or t in ("deselect",):
            return True
    return _count_carousel_selected(xml, require_numbered=True) >= 2


def _count_carousel_selected(xml=None, require_numbered=False):
    """Count gallery tiles that look selected (numbers / 'selected N')."""
    xml = xml or dump()
    nsel = 0
    seen = set()
    for node in nodes(xml):
        d = attr(node, "content-desc").strip().lower()
        t = attr(node, "text").strip().lower()
        if not d and not t:
            continue
        x, y = bounds_center(node)
        if x is None:
            continue
        key = (int(x) // 40, int(y) // 40)
        if key in seen:
            continue
        numbered = bool(re.search(r"selected\s+\d", d) or re.match(r"^\d+$", t))
        if require_numbered and not numbered:
            continue
        if numbered or (d.startswith("selected") and not d.startswith("unselected")):
            if "photo" in d or "image" in d or "video" in d or "thumbnail" in d \
               or t.isdigit() or numbered:
                seen.add(key)
                nsel += 1
    return nsel


def _carousel_picker_tiles(xml=None):
    """Grid thumbnails for carousel pick — dedupe container/thumbnail pairs."""
    xml = xml or dump()
    _sw, sh = _screen_wh(xml)
    by_pos = {}
    for node in nodes(xml):
        d = attr(node, "content-desc").lower()
        t = attr(node, "text").strip().lower()
        rid = attr(node, "resource-id").lower()
        if "camera" in d and "thumbnail" not in d:
            continue
        if t == "camera" or d.strip() == "camera":
            continue
        if "photo thumbnail" not in d and "image" not in d and "video" not in d \
           and "thumbnail" not in d:
            continue
        x, y = bounds_center(node)
        if x is None or y is None or y < int(sh * 0.42):
            continue
        key = (int(x) // 30, int(y) // 30)
        score = 0
        if "gallery_grid_item_thumbnail" in rid or "grid_item_thumbnail" in rid:
            score += 2
        if d.startswith("unselected") or " unselected " in (" %s " % d):
            score += 1
        if re.search(r"selected\s+\d", d):
            score += 1
        prev = by_pos.get(key)
        if prev is None or score > prev[0]:
            by_pos[key] = (score, y, x, node, d)
    return [(y, x, node, d) for _s, y, x, node, d
            in sorted(by_pos.values(), key=lambda r: (r[1], r[2]))]


def _enable_multi_select(xml=None):
    """Turn on gallery multi-select for carousels. One tap only (toggle)."""
    xml = xml or dump()
    if _multi_select_is_on(xml):
        print("[carousel] multi-select already on")
        return True
    n = _multi_select_node(xml)
    if n is None:
        print("[carousel] no Select / multi-select control in picker")
        return False
    tapn(n, "multi-select")
    time.sleep(1.0)
    xml2 = dump()
    on = _multi_select_is_on(xml2)
    print("[carousel] multi-select %s" % ("ON" if on else "FAIL after tap"))
    return on


def pick_n_photos(n=2):
    """Select n gallery tiles for a real carousel. Taps ≠ selected (arzu/nila 2026-08-11)."""
    need = max(2, int(n or 2))
    dismiss_media_permission()
    if not _enable_multi_select():
        print("[carousel] FAIL: Select not armed — would post a single image")
        return False
    tapped = 0
    for attempt in range(need + 4):
        xml = dump()
        nsel = _count_carousel_selected(xml)
        if nsel >= need:
            print("[carousel] tapped=%d counted_selected=%d need=%d ok=True"
                  % (tapped, nsel, need))
            return True
        tiles = _carousel_picker_tiles(xml)
        picked = False
        for y, x, node, d in tiles:
            if re.search(r"selected\s+\d", d):
                continue
            if d.startswith("selected") and "unselected" not in d:
                continue
            if not (d.startswith("unselected") or " unselected " in (" %s " % d)
                    or (not d.startswith("selected") and "thumbnail" in d)):
                continue
            if tapn(node, "carousel-tile"):
                tapped += 1
                picked = True
                time.sleep(0.55)
                break
        if not picked:
            break
    nsel = _count_carousel_selected()
    ok = nsel >= need
    print("[carousel] tapped=%d counted_selected=%d need=%d ok=%s"
          % (tapped, nsel, need, ok))
    if not ok:
        print("[carousel] FAIL: not a multi-select (Select off or 1 tile)")
        return False
    return True


def _next_button_nodes(xml):
    """All candidate Next controls with enabled flag."""
    out = []
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if "next" not in rid and t != "next" and d != "next":
            continue
        if attr(n, "clickable") != "true" and "next_button" not in rid:
            continue
        x, y = bounds_center(n)
        en = attr(n, "enabled").lower()
        out.append((x or 0, y or 0, n, rid, en))
    out.sort(key=lambda r: (-r[0], r[1]))  # rightmost first
    return out


def _tap_picker_next(xml=None, allow_coord=True):
    """Tap picker Next; prefer enabled next_button*; dump enabled state if stuck.

    allow_coord=False for reels: blind top-right tap opens camera-tools tip
    (2026-08-03 traces after pick → tip loop).
    """
    xml = xml or dump()
    cands = _next_button_nodes(xml)
    if not getattr(_tap_picker_next, "_logged", False):
        _tap_picker_next._logged = True
        for x, y, _n, rid, en in cands:
            print("[picker] Next cand x=%d y=%d enabled=%s id=%s"
                  % (x, y, en or "?", rid[-40:]))
    # Prefer enabled=true
    for x, y, n, rid, en in cands:
        if en in ("", "true"):  # empty often means true on older Android
            if tapn(n, "next:%s" % (rid[-20:] or "btn")):
                return True
    # Try all next_* including disabled (sometimes still works)
    for x, y, n, rid, en in cands:
        if tapn(n, "next-force:%s" % (rid[-20:] or "btn")):
            return True
    if not allow_coord:
        print("[picker] no Next control — skip coord (reel-safe)")
        return False
    tap(1349, 84)
    print("[picker] coord Next @ 1349,84")
    return True


def _bypass_picker_via_send():
    """Skip stuck gallery Next — SEND the pushed media into IG composer.

    Only SEND if our remote file is still on device (purpose: THIS video).
    """
    pkg = CURRENT_PKG or fg_pkg()
    remote = CURRENT_REMOTE_IMG
    if not pkg or not remote:
        return False
    if not _remote_file_ok(remote):
        print("[picker] SEND aborted — remote missing")
        return False
    mime = _media_mime(remote)
    print("[picker] bypass via ACTION_SEND %s (%s)" % (remote, mime))
    adb("shell", "am", "start",
        "-a", "android.intent.action.SEND",
        "-t", mime,
        "--eu", "android.intent.extra.STREAM", "file://" + remote,
        "-p", pkg)
    time.sleep(3.5)
    xml = dump()
    tb = text_block(xml)
    if _is_android_share_sheet(xml, tb):
        print("[picker] SEND opened Android share sheet — resolve Just once")
        _rt_log("send_share_sheet", snippet=tb)
        if not _handle_android_share_sheet(xml, pkg=pkg):
            return False
        time.sleep(2.0)
        xml = dump()
        tb = text_block(xml)
    st = detect_state(xml)
    print("[picker] after SEND → %s" % st)
    if _is_android_share_sheet(xml, tb):
        return False
    if st in ("EDIT_SCREEN", "CAPTION_SCREEN", "CREATE_CHOOSER"):
        return True
    if st == "CREATE_PICKER":
        return in_caption_screen(xml)
    if in_caption_screen(xml) or _reel_at_caption(xml):
        return True
    if _tap_chooser_post_exact(xml) or tap_exact(
            xml, "Share", "Feed", "Reel", "Reels", "Post", label="send-share"):
        time.sleep(2.5)
        xml2 = dump()
        if _is_android_share_sheet(xml2):
            return _handle_android_share_sheet(xml2, pkg=pkg)
        return detect_state(xml2) in ("EDIT_SCREEN", "CAPTION_SCREEN", "CREATE_CHOOSER") \
            or in_caption_screen(xml2) or _reel_at_caption(xml2)
    return False


def _gallery_grid_visible(xml=None):
    """True Recents/grid tiles — not camera with a Gallery chip."""
    xml = xml or dump()
    tb = text_block(xml).lower()
    xl = xml.lower()
    if "gallery_grid_item" in xl or "gallery_folder_menu" in xl:
        return True
    if any(p in tb for p in (
            "unselected photo", "unselected video", "photo thumbnail",
            "video thumbnail", "select multiple")):
        return True
    if "add to story" in tb and "recents" in tb:
        return True
    return False


def _open_gallery_from_camera(xml=None):
    """Reel/Story create often opens camera — switch to gallery/library."""
    xml = xml or dump()
    if _gallery_grid_visible(xml):
        return False
    tb = text_block(xml)
    # Camera chrome still showing (Story/Reel shutter). "Gallery" CTA + "Shutter
    # selected." must NOT count as already-on-gallery (dorothhds129 2026-08-30).
    cam_chrome = any(p in tb for p in (
        "shutter", "story settings", "boomerang", "hold to record",
        "switch to back camera", "switch to front camera", "create mode button"))
    # Already on gallery grid — real tiles / Recents+Next, not camera chrome.
    if not cam_chrome and (
            any(p in tb for p in ("photo thumbnail", "video thumbnail",
                                    "unselected photo", "unselected video",
                                    "select multiple")) or
            (any(p in tb for p in ("recents", "camera roll", "library")) and
             "next" in tb)):
        return False
    def _opened():
        time.sleep(1.6)
        return _gallery_grid_visible()

    # Prefer gallery_preview_button / content-desc Gallery over vague rid hits
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").lower()
        t = attr(n, "text").strip().lower()
        if rid.endswith("gallery_preview_button") or d == "gallery" or t == "gallery":
            if tapn(n, "open-gallery-preview"):
                return _opened()
    if tap_exact(xml, "Gallery", "Library", "Recents", "Camera roll",
                 "Add from gallery", "ADD FROM GALLERY", "Photo library",
                 label="open-gallery-from-cam"):
        return _opened()
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        if "draft" in d or "draft" in t or "draft" in rid:
            continue
        if any(k in d or k in t or k in rid for k in (
                "gallery", "library", "recents", "media_thumbnail",
                "gallery_button", "gallery_picker", "add from gallery",
                "gallery_thumbnail", "cam_dest_gallery")):
            if tapn(n, "open-gallery-icon"):
                return _opened()
    # Bottom-left gallery chip (common on Reel camera)
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        if y > 2200 and x < 400 and any(k in d for k in ("gallery", "photo", "video", "thumbnail")):
            if tapn(n, "open-gallery-chip"):
                return _opened()
    if _vision_tap(
            "Gallery",
            question="Instagram story/reel CAMERA (not Recents grid). "
                     "Tap the small gallery thumbnail at the bottom-left to open Recents. "
                     "Not the shutter, not POST/STORY/REEL tabs.",
            tag="open_gallery"):
        return _opened()
    return False


def _is_reel_camera_tb(tb):
    """Reel camera chrome from text_block (not gallery picker, not tip sheets)."""
    tb = (tb or "").lower()
    # Tip sheets from 2026-08-03 dumps — NOT camera
    if _is_reel_create_tip_tb(tb):
        return False
    if any(p in tb for p in ("recents", "camera roll")) and (
            "next" in tb or "photo thumbnail" in tb or "video thumbnail" in tb):
        return False
    if "photo thumbnail" in tb or "video thumbnail" in tb:
        return False
    # Real camera chrome only
    cam_hints = ("hold to record", "reel camera", "clips camera",
                 "create mode", "tap to record", "hold for video")
    if any(p in tb for p in cam_hints):
        return True
    if "shutter" in tb or ("record" in tb and "reel" in tb and "save reels" not in tb):
        return True
    return False


def _is_reel_camera(xml=None):
    return _is_reel_camera_tb(text_block(xml or dump()))


def _gallery_has_video_tile(xml=None):
    xml = xml or dump()
    for n in nodes(xml):
        d = attr(n, "content-desc").lower()
        if "video" in d and ("thumbnail" in d or "selected" in d or "unselected" in d):
            return True
        if d.startswith("video ") or "video," in d:
            return True
    return False


def _wait_for_gallery_video(max_rounds=6):
    """Folder switch + rescan until OUR video (or any video tile) is pickable.

    Purpose gate for LOGGED_IN/POST_DONE: do not advance until gallery shows video.
    Prefers tiles matching CURRENT_REMOTE_STEM / today's date when scoring later.
    """
    # Gallery MediaStore lag often needs >55s create budget (Jazlene 2026-09-04)
    if _section_left() is not None and _section_left() < 45:
        _section_begin("create", 70)
        print("[picker] gallery wait — create budget reset 70s")
    for i in range(max_rounds):
        if _section_expired(need=2):
            print("[picker] create budget — stop gallery wait")
            return _gallery_has_video_tile(dump())
        xml = dump()
        tiles = _list_gallery_video_tiles(xml, only_unselected=True)
        if tiles:
            top = tiles[0]
            print("[picker] video ready round=%d score=%s desc=%s"
                  % (i + 1, top[0], top[4][:60]))
            _rt_log("gallery_video_ready", round=i + 1, score=top[0],
                    desc=top[4][:80], stem=CURRENT_REMOTE_STEM or "")
            return True
        if _gallery_has_video_tile(xml):
            # Selected already or desc shape we didn't list — still OK
            print("[picker] video tile present (round %d)" % (i + 1))
            return True
        print("[picker] waiting for OUR video (round %d/%d) stem=%s"
              % (i + 1, max_rounds, CURRENT_REMOTE_STEM or "?"))
        _switch_gallery_folder_for_video(xml)
        time.sleep(0.6)
        if CURRENT_REMOTE_IMG:
            _media_scan_file(CURRENT_REMOTE_IMG)
            dcim = CURRENT_REMOTE_IMG.replace("/Movies/", "/DCIM/Camera/")
            if dcim != CURRENT_REMOTE_IMG:
                _media_scan_file(dcim)
        # Pull gallery open again if still on camera
        if _is_reel_camera(dump()) or detect_state(dump()) == "CREATE_CAMERA":
            _open_gallery_from_camera(dump())
        time.sleep(1.0)
    ok = _gallery_has_video_tile(dump())
    _rt_log("gallery_video_wait_done", ok=ok, stem=CURRENT_REMOTE_STEM or "",
            **_gallery_trace_summary())
    return ok


def _switch_gallery_folder_for_video(xml=None):
    """Recents often shows photos only — open folder menu and pick Movies/Videos."""
    xml = xml or dump()
    # Prefer rid (never bottom-left Gallery camera chip — Jazlene 2026-09-04
    # tap_exact('Gallery') hit open-gallery-preview @102,2795).
    opened = False
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "gallery_folder" not in rid or attr(n, "clickable") != "true":
            continue
        y = bounds_center(n)[1]
        if y is not None and y > 2200:
            continue
        if tapn(n, "gallery-folder-rid"):
            opened = True
            break
    if not opened:
        # Top-bar Recents / Camera roll only — ban bare "Gallery" (camera chip)
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            x, y = bounds_center(n)
            if y is None or y > 500:
                continue
            t = attr(n, "text").strip().lower()
            d = attr(n, "content-desc").strip().lower()
            if t in ("recents", "camera roll", "library") or d in (
                    "recents", "camera roll", "library"):
                if tapn(n, "gallery-folder-menu"):
                    opened = True
                    break
    if not opened:
        return False
    time.sleep(1.2)
    xml2 = dump()
    if tap_exact(xml2, "Movies", "Videos", "Video", "Camera", "Downloads",
                 "All", label="gallery-folder-pick"):
        time.sleep(1.5)
        return True
    for n in nodes(xml2):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        blob = t + " " + d
        if any(k in blob for k in ("movie", "video", "download", "camera", "all media", "dcim")):
            if tapn(n, "gallery-folder-alt"):
                time.sleep(1.5)
                return True
    return False


def _xml_packages(xml):
    return {attr(n, "package") for n in nodes(xml) if attr(n, "package")}


def _is_sys_settings_xml(xml=None):
    """True when dump is Android Settings (not IG) — e.g. App notifications.

    marina2784700 2026-08-12: landed on 'All Insta0902 notifications' toggle
    (com.android.settings) → UNKNOWN_STUCK. Not an IG UI bug — recover + relaunch.
    """
    xml = xml or dump()
    pkgs = _xml_packages(xml)
    if any("instagram" in (p or "") for p in pkgs):
        return False
    if any((p or "").startswith("com.android.settings") for p in pkgs):
        return True
    if any("settings" in (p or "") and "android" in (p or "") for p in pkgs):
        return True
    tb = text_block(xml)
    # App-notification channel screen (clone label Insta#### / Instagram)
    if re.search(r"all\s+\S+\s+notifications", tb) and "instagram" not in tb:
        return True
    if "app notifications" in tb or "notification categories" in tb:
        return True
    if "all insta" in tb and "notification" in tb:
        return True
    return False


def _is_wrong_app_xml(xml=None):
    """True when dump is NekoBox / proxy / Settings UI with no Instagram package."""
    xml = xml or dump()
    if _is_sys_settings_xml(xml):
        return True
    pkgs = _xml_packages(xml)
    if any("instagram" in (p or "") for p in pkgs):
        return False
    tb = text_block(xml)
    if "nekobox" in tb or any("moe.nb4a" in (p or "") for p in pkgs):
        return True
    if any(x in (p or "") for p in pkgs for x in ("v2ray", "clash", "proxy")):
        return True
    return False


def _recover_from_sys_settings(pkg=None, label="sys-settings"):
    """Leave Android Settings and bring IG clone back. Never tap notification toggles."""
    pkg = pkg or CURRENT_PKG or ""
    print("[%s] Android Settings / not-IG — BACK + relaunch %s"
          % (label, (pkg or "?").split(".")[-1]))
    for _ in range(3):
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.7)
        xml = dump()
        if not _is_sys_settings_xml(xml) and not _is_wrong_app_xml(xml):
            if pkg and (pkg in xml or "instagram" in text_block(xml)):
                return True
            break
    adb("shell", "input", "keyevent", "KEYCODE_HOME")
    time.sleep(0.5)
    if pkg:
        return bool(launch(pkg))
    return False


# ---------------------------------------------------------------------------
# Notification gate — APPEND-ONLY case registry (A, B, C… never replace)
# ---------------------------------------------------------------------------
# Farm rule: new dump = new case row. Old cases stay. Match all that hit; OR.
# Same CTA for known cases: blue "Next". Add case C below when a 3rd copy appears.


def _notif_case_a_stay_up_to_date(tb):
    """Case A — ayar_ii68 / nozuk_ii43 morning XML."""
    return (
        "turn on notifications to stay up to date" in tb
        or "choose to turn on notifications or skip" in tb
        or ("turn on notifications" in tb and "skip this step" in tb)
    )


def _notif_case_b_find_out_right_away(tb):
    """Case B — afternoon / andrprd loop screenshot."""
    return (
        "find out right away when people follow" in tb
        or ("turn on notifications" in tb and "like and comment on your posts" in tb)
        or ("turn on notifications" in tb and "find out right away" in tb)
    )


# Append new (id, matcher) tuples only. Do not delete or rewrite prior rows.
_NOTIF_GATE_CASES = (
    ("A_stay_up_to_date", _notif_case_a_stay_up_to_date),
    ("B_find_out_right_away", _notif_case_b_find_out_right_away),
    # ("C_…", _notif_case_c_…),  # next dump goes here
)


def _notif_gate_hits(tb=""):
    """Return list of matching case ids (0..N). Never short-circuit after first hit."""
    tb = (tb or "").lower()
    if not tb:
        return []
    return [cid for cid, match in _NOTIF_GATE_CASES if match(tb)]


def _notif_gate_variant(tb=""):
    """Classify notif gate. '' if none; one id; or A+B / A+B+C when several match."""
    tb = (tb or "").lower()
    hits = _notif_gate_hits(tb)
    if hits:
        return "+".join(hits)
    # Same blue-Next gate, copy not yet catalogued — still press Next; then promote to case C+
    if "turn on notifications" in tb and "next" in tb and \
       "log in" not in tb and "password" not in tb:
        return "generic_turn_on_notifications"
    return ""


def _is_notif_intro_tb(tb=""):
    """True if any registered notif case (or generic same CTA) is up."""
    return bool(_notif_gate_variant(tb))


def _notif_next_button_center(xml):
    """Best (x,y) for the blue Next CTA from dump, or None."""
    xml = xml or ""
    next_centers = []
    igds = []
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t == "next" or d == "next":
            xy = bounds_center(n)
            if xy[0] is not None:
                next_centers.append((xy[0], xy[1], n))
        if attr(n, "clickable") == "true" and "igds_button" in attr(n, "resource-id").lower():
            b = _node_bounds(n)
            if b and b[1] >= 1800:
                igds.append((n, b))
    for cx, cy, _n in next_centers:
        for cn, (x1, y1, x2, y2) in igds:
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                return (x1 + x2) // 2, (y1 + y2) // 2
    if igds:
        _cn, (x1, y1, x2, y2) = max(
            igds, key=lambda it: (it[1][2] - it[1][0]) * (it[1][3] - it[1][1]))
        return (x1 + x2) // 2, (y1 + y2) // 2
    if next_centers:
        return next_centers[0][0], next_centers[0][1]
    return None, None


def _adb_tap_xy(x, y, label=""):
    """Tap with touchscreen + plain input (Samsung/Compose often ignores one)."""
    x, y = int(x), int(y)
    print("[tap] %s @ %d,%d" % (label or "xy", x, y))
    adb("shell", "input", "touchscreen", "tap", str(x), str(y))
    time.sleep(0.15)
    adb("shell", "input", "tap", str(x), str(y))
    return True


def _tap_notif_next(xml, label="notif-next"):
    """Press blue Next — same for variant A and B."""
    xml = xml or dump()
    cx, cy = _notif_next_button_center(xml)
    if cx is not None:
        _adb_tap_xy(cx, cy, label + "-btn")
        time.sleep(0.2)
        _adb_tap_xy(cx, cy + 8, label + "-btn2")
    else:
        tap_exact(xml, "Next", label=label + "-exact")

    sw, sh = _screen_wh(xml)
    if sw < 200 or sh < 400:
        sw, sh = 1440, 2960
    for y_frac in (0.935, 0.922, 0.948, 0.910):
        _adb_tap_xy(sw // 2, int(sh * y_frac), label + "-coord")
        time.sleep(0.2)
    return True


def _dismiss_notif_followup(xml=None, label="notif-follow"):
    """After Next leaves intro: Skip / Not now / Don't allow only — never Turn on."""
    xml = xml or dump()
    if dismiss_android_permission():
        time.sleep(0.8)
        return True
    if tap_exact(xml, "Not now", "Not Now", "Skip", "Maybe later", "Later",
                 "Don't allow", "Dont allow", "No thanks", "No, thanks",
                 "Skip this step", label="%s-skip" % label):
        return True
    if tap_first(xml, "Not now", "Not Now", "Skip", "Don't allow", "Dont allow",
                 "Skip this step", label="%s-skip-first" % label):
        return True
    return False


def _dismiss_notif_prompt(xml=None, label="skip-notif"):
    """Press Next on notif gate (A or B). True only if gate text is actually gone."""
    xml = xml or dump()
    tb = text_block(xml).lower()
    variant = _notif_gate_variant(tb)

    if not variant:
        if _dismiss_notif_followup(xml, label=label):
            return not _is_notif_intro_tb(text_block(dump()))
        return detect_state(dump()) != "NOTIF_PROMPT"

    print("[notif] gate variant=%s — press Next (A and B both supported)" % variant)
    for attempt in range(5):
        cur = dump() if attempt else xml
        tb_cur = text_block(cur).lower()
        v_cur = _notif_gate_variant(tb_cur)
        if not v_cur:
            print("[notif] gate cleared (was %s)" % variant)
            _dismiss_notif_followup(cur, label="%s-after" % label)
            return True
        print("[notif] Next try %d/5 variant=%s" % (attempt + 1, v_cur))
        _tap_notif_next(cur, label="%s-n%d" % (label, attempt + 1))
        time.sleep(1.2)

    still = _notif_gate_variant(text_block(dump()))
    if still:
        print("[notif] FAIL — still on gate variant=%s after Next taps" % still)
        return False
    _dismiss_notif_followup(dump(), label="%s-after" % label)
    return True


def _clear_notif_gate(max_tries=4, label="notif-gate"):
    """Clear notif gate A or B; False if still present (caller fail-fast)."""
    for i in range(max_tries):
        xml = dump()
        v = _notif_gate_variant(text_block(xml))
        if not v and detect_state(xml) != "NOTIF_PROMPT":
            return True
        print("[notif] gate clear try %d/%d variant=%s" % (i + 1, max_tries, v or "?"))
        _dismiss_notif_prompt(xml, label="%s-%d" % (label, i + 1))
        time.sleep(0.8)
    stuck_v = _notif_gate_variant(text_block(dump()))
    stuck = bool(stuck_v) or detect_state(dump()) == "NOTIF_PROMPT"
    if stuck:
        print("[notif] STUCK variant=%s — refusing to spin forever" % (stuck_v or "NOTIF_PROMPT"))
    return not stuck


def _ensure_ig_foreground(pkg=None, hard=False):
    """Bring IG clone to front — create_timeout dumps showed NekoBox focused."""
    pkg = pkg or CURRENT_PKG or ""
    for attempt in range(4 if hard else 2):
        fg = fg_pkg() or ""
        xml = dump()
        if pkg and (fg == pkg or pkg in xml):
            if not _is_wrong_app_xml(xml):
                return True
        if "instagram" in fg and (not pkg or fg == pkg or pkg in fg):
            if not _is_wrong_app_xml(xml):
                return True
        bad = _is_wrong_app_xml(xml) or (
            fg and "instagram" not in fg and
            any(x in fg for x in ("moe.nb4a", "nekobox", "v2ray", "clash",
                                    "settings", "launcher")))
        if bad or (fg and "instagram" not in fg):
            print("[fg] not Instagram (fg=%s attempt=%d) — relaunch %s"
                  % (fg or "?", attempt + 1, (pkg or "?")[-20:]))
            if _is_sys_settings_xml(xml):
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(0.5)
            adb("shell", "input", "keyevent", "KEYCODE_HOME")
            time.sleep(0.6)
        if pkg:
            launch(pkg)
            time.sleep(2.2)
    fg = fg_pkg() or ""
    ok = bool(pkg and (fg == pkg or "instagram" in fg)) and not _is_wrong_app_xml(dump())
    if not ok:
        print("[fg] FAIL still not on Instagram (fg=%s)" % (fg or "?"))
    return ok


def _reel_edit_ready(xml=None):
    """True when on reel edit/cover/audio chrome (advance with Next/OK).

    CRITICAL (2026-08-04 velorastar7): bare "new reel" is ALSO the gallery title on
    CREATE_PICKER — never treat picker (recents/thumbnails) as EDIT or we abort-stale
    forever and never pick (LOGGED_IN/POST_TIMEOUT with unsel_video=4).
    Caption screen also shows "Edit cover" chip — that is NOT edit-ready.
    """
    xml = xml or dump()
    tb = text_block(xml)
    # Gallery / picker chrome — not edit
    if any(p in tb for p in (
            "recents", "gallery", "camera roll", "select multiple",
            "photo thumbnail", "video thumbnail", "unselected video",
            "unselected photo", "selected video", "selected photo")):
        return False
    # Caption composer (may mention edit cover as a row)
    if any(p in tb for p in (
            "write a caption", "add a caption", "tag people", "also share",
            "hashtags", "poll prompt")):
        return False
    if detect_state(xml) == "CREATE_PICKER":
        return False
    if _is_edit_cover_screen(xml):
        return False  # cover picker → Done, not Next-as-publish
    # Real edit signals (avoid lone "new reel" which is the picker title)
    if any(p in tb for p in (
            "add audio", "add music", "edit video",
            "trim", "volume", "sequence", "remix", "use audio", "use original",
            "continue without", "suggested audio", "add ai label",
            "double tap for tools")):
        return True
    if "new reel" in tb and any(p in tb for p in ("next", "ok", "done", "save draft")) \
       and "thumbnail" not in tb and "recents" not in tb:
        return True
    return False


def _dismiss_reel_overlays(xml=None):
    """Skip audio/music/cover prompts that block Next → caption."""
    xml = xml or dump()
    if _is_preview_size_tip(xml):
        return dismiss_preview_size_tip(xml, label="reel-overlay-preview")
    if tap_exact(xml,
                 "Not now", "Skip", "Continue without audio",
                 "Continue without music", "Use original audio",
                 "Use original", "No music", "Dismiss", "Got it",
                 label="reel-overlay-skip"):
        time.sleep(1.2)
        return True
    tb = text_block(xml)
    if "add audio" in tb:
        if tap_exact(xml, "Next", "OK", "Done", "Continue", label="reel-audio-next"):
            time.sleep(1.2)
            return True
        for n in nodes(xml):
            rid = attr(n, "resource-id").lower()
            if "sticky_toast" in rid or "camera_sticky_toast" in rid:
                if tapn(n, "dismiss-audio-toast"):
                    time.sleep(0.8)
                    return True
        # bode54373 2026-08-03: toast-only "Add audio" with no Next — tap toast text
        for n in nodes(xml):
            if "add audio" in attr(n, "text").strip().lower():
                if tapn(n, "dismiss-add-audio-text"):
                    time.sleep(0.8)
                    return True
    return False

def attach_image(image_path):
    """IG: push to gallery; caller is expected to already be on the create picker."""
    remote = push_image(image_path)
    if not remote:
        print("[img] attach_image: push failed")
        return False
    return pick_newest_photo()

# ---------------------------------------------------------------------------
# State detection
# ---------------------------------------------------------------------------

def _is_follow_suggestions_tb(tb=""):
    """Post-login follow-nudge screens (cecilia33481 2026-08-12).

    Classic: 'Follow people' + Next/Follow all.
    New: 'Follow 5 or more people' + Skip (no Next) — old detector missed this
    because 'follow people' is not a substring of 'follow 5 or more people'.
    """
    tb = (tb or "").lower()
    if "follow 5 or more people" in tb or "follow 5 people" in tb:
        return True
    if "following isn't required" in tb or "following isnt required" in tb:
        return True
    if any(p in tb for p in ("follow people", "suggested for you",
                             "people you might know")) and \
       any(p in tb for p in ("follow all", "next", "continue", "skip")):
        return True
    return False


def _is_action_limit_tb(tb=""):
    """Meta velocity / action-block dialog (suzukii20648 2026-08-12 Fleet Screens).

    Copy: 'Try again later' + 'We limit how often you can do certain things…'
    + 'Tell us if you think we made a mistake.' Not the same as verify-empty
    POST_BLOCKED (lagging 0 posts). Do not treat bare 'try again later' alone.
    """
    tb = (tb or "").lower()
    if "we limit how often you can do certain things" in tb:
        return True
    if "protect our community" in tb and "try again later" in tb:
        return True
    if "tell us if you think we made a mistake" in tb and (
            "try again later" in tb or "limit how often" in tb
            or "action blocked" in tb):
        return True
    if "action blocked" in tb:
        return True
    if "we restrict certain activity" in tb or "we restricted certain activity" in tb:
        return True
    return False


def _dismiss_action_limit(xml=None, label="action-limit"):
    """Close rate-limit dialog. OK / Dismiss only — never 'Tell us' (opens appeal form)."""
    xml = xml or dump()
    if tap_exact(xml, "OK", "Ok", label="%s-ok" % label):
        time.sleep(0.8)
        return True
    if tap_exact(xml, "Dismiss", label="%s-dismiss" % label):
        time.sleep(0.8)
        return True
    print("[action-limit] no OK — BACK (never Tell us)")
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.8)
    return True


def fail_action_limit(note=""):
    """Fail-fast on Meta ACTION_LIMIT. Distinct from verify-empty POST_BLOCKED."""
    xml = dump()
    print("[FAIL] Meta ACTION_LIMIT / rate protect — cool account; not a code bug")
    _dismiss_action_limit(xml)
    return emit_fail("POST_RATE_LIMIT", note=note or "action_limit")


def _check_action_limit(xml=None, note=""):
    """If rate-limit dialog visible → POST_RATE_LIMIT code, else None."""
    xml = xml or dump()
    tb = text_block(xml)
    if detect_state(xml) == "ACTION_LIMIT" or _is_action_limit_tb(tb):
        return fail_action_limit(note=note)
    return None


def _dismiss_follow_suggestions(xml=None, label="skip-follow"):
    """Leave follow-nudge. Skip only — never tap Follow (would follow 5 accounts)."""
    xml = xml or dump()
    if tap_exact(xml, "Skip", "Not now", "Not Now", "Maybe later",
                 label="%s-skip" % label):
        return True
    if tap_first(xml, "Skip", "Not now", "Not Now", label="%s-skip-first" % label):
        return True
    if tap_exact(xml, "Next", "Continue", "Done", label="%s-next" % label):
        return True
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    print("[follow] no Skip — BACK (never Follow)")
    return True


def detect_state(xml):
    tb = text_block(xml)
    nedits = len(edits(xml))

    # Wrong app (NekoBox) / Android Settings — before any IG heuristics
    if _is_sys_settings_xml(xml):
        return "SYS_SETTINGS"
    if _is_wrong_app_xml(xml):
        return "WRONG_APP"

    # Android share / open-with (ACTION_SEND dumps 2026-08-03)
    if _is_android_share_sheet(xml, tb):
        return "ANDROID_SHARE"

    # Android autofill "Save password to Google?" — overlays everything, dismiss first
    if "save password to google" in tb or ("save password" in tb and "google" in tb):
        return "GOOGLE_SAVE"

    # Android Credential Manager / passkey sheet (blocks cold login on farm clones)
    if "use passkey" in tb or "passkey from a different device" in tb or \
       ("sign in another way" in tb and "passkey" in tb) or \
       ("sign in another way" in tb and "cancel" in tb and nedits == 0 and
        "log in" not in tb and "password" not in tb):
        return "PASSKEY_PROMPT"

    # Meta action / velocity limit (suzukii20648 2026-08-12) — before FEED heuristics
    if _is_action_limit_tb(tb):
        return "ACTION_LIMIT"

    # "Unable to log in / An unexpected error occurred" — a Meta-side error dialog
    if "unable to log in" in tb and "unexpected error" in tb:
        return "LOGIN_ERROR_DIALOG"

    # Cold start after pm clear: Instagram opens CREATE-ACCOUNT / mobile signup,
    # not the login form. Proven 2026-07-23 on androii:
    #   (1) "what's your mobile number?" + "I already have an account"
    #   (2) confirm sheet: "Already have an account?" + CONTINUE / LOG IN
    #   (3) NEW welcome: "Join Instagram" + "I already have a profile" (not "account")
    # Must divert to login before CONTACT_VERIFY / ADD_PHONE heuristics fire.
    if "continue creating account" in tb and "log in" in tb:
        return "SIGNUP_GATE"
    if "i already have a profile" in tb or "i already have an account" in tb or \
       ("what's your mobile number" in tb and "sign up with email" in tb) or \
       ("join instagram" in tb and "get started" in tb) or \
       ("create a new account" in tb and "log in" in tb):
        return "SIGNUP_GATE"

    # Inline login error (genuinely wrong creds, missing acct, or IP/velocity flag)
    # glowdream2 2026-07-26: "we can't find an account with …" (not "couldn't")
    if any(p in tb for p in ["the password you entered is incorrect",
                               "incorrect password", "password was incorrect",
                               "we couldn't find your account",
                               "couldn't find an account",
                               "can't find an account",
                               "cant find an account",
                               "we can't find an account",
                               "try another mobile number or email",
                               "the username you entered doesn't",
                               "sorry, your password was incorrect"]):
        return "LOGIN_ERROR"

    # Dead / orphaned login — "Recover your account" + device-match gate.
    # Braylonutton52 2026-07-28: tip-sheet preferred Continue → "Try another device"
    # UNKNOWN_STUCK. Fail-fast as ACCOUNT_NOT_FOUND (not recoverable on farm clones).
    if "login info is no longer connected" in tb or \
       "try another device to continue" in tb or \
       ("can't match the device" in tb and "account you're trying to recover" in tb) or \
       ("recover your account" in tb and "no longer connected" in tb):
        return "ACCOUNT_NOT_FOUND"

    # 2FA / CAPTCHA / human BEFORE LOGIN_SCREEN — leftover "username, phone…" text in
    # the tree after submit made angelpetal98 type username into "Enter the code"
    # (2026-07-24) while detect still said LOGIN_SCREEN.

    # 2FA method chooser
    if "choose a way to confirm" in tb or "available confirmation methods" in tb or \
       "how do you want" in tb or \
       ("authentication app" in tb and "backup code" in tb) or \
       ("authentication app" in tb and "notification on another device" in tb):
        return "2FA_CHOOSE"

    # 2FA push approval
    if "check your notifications" in tb or "waiting for approval" in tb or \
       "approve this login" in tb:
        return "2FA_PUSH"

    # Image CAPTCHA (more specific than bare "enter the code")
    if "enter the code from the image" in tb or "can't read this text" in tb or \
       "cant read this text" in tb or "hear this code" in tb or \
       "get a new code" in tb or \
       ("confirm you're human" in tb and nedits >= 1 and "authentication" not in tb):
        return "CAPTCHA"

    # Phone/email VERIFICATION (mandatory)
    if (("mobile number" in tb or "phone number" in tb or "your email" in tb
            or "confirm your email" in tb)
        and ("send code" in tb or "send you a code" in tb or "via sms" in tb
             or "via whatsapp" in tb or "we sent" in tb or "code we sent" in tb
             or "enter the confirmation code" in tb or "confirm this" in tb)
        and "skip" not in tb):
        return "CONTACT_VERIFY"

    # 2FA code entry (authenticator)
    if nedits >= 1 and any(p in tb for p in ["enter the 6-digit code",
                               "enter the code", "6-digit code", "two-factor",
                               "security code", "authentication app", "enter code"]):
        return "2FA_SCREEN"

    # Wrong TOTP dialog — dismiss and stay in 2FA (not a tip sheet)
    if "please check the security code" in tb or \
       ("security code" in tb and "try again" in tb and "ok" in tb):
        return "2FA_BAD_CODE"

    # Login form (username + password) — after challenge screens only
    if any(p in tb for p in ["log in to instagram", "log into instagram",
                               "username, phone number or email",
                               "phone number, username, or email",
                               "phone number or email",
                               "username, email or mobile number",
                               "email or mobile number",
                               "mobile number or email"]):
        return "LOGIN_SCREEN"
    if nedits >= 2 and ("log in" in tb or "sign up" in tb) and "password" in tb:
        return "LOGIN_SCREEN"

    if _is_follow_suggestions_tb(tb):
        return "FOLLOW_SUGGESTIONS"

    if any(p in tb for p in ["add your birthday", "enter your birthday"]):
        return "BIRTHDAY"

    # Optional "add a phone number" nudge — skippable
    if any(p in tb for p in ["add your phone", "add a phone", "phone number",
                               "mobile number", "enter your mobile number"]) and \
       "skip" in tb:
        return "ADD_PHONE"

    if any(p in tb for p in ["turn on notifications", "allow notifications",
                               "notifications from instagram",
                               "get notifications", "turn on push",
                               "find out right away when people follow",
                               "stay up to date"]):
        return "NOTIF_PROMPT"

    if any(p in tb for p in ["save your login", "save login info",
                               "remember password", "save password"]):
        return "SAVE_INFO"

    # Post-login email security nudge (naz.li3070 2026-08-12 — only Add new contact info)
    if "email may not be secure" in tb or \
       ("secure your account" in tb and "add new contact info" in tb):
        return "EMAIL_SECURITY"

    # Android system permission dialog — structural only (Allow + Don't allow pair).
    # Broad text match false-fired on Meta tips and looped tap_first Allow (2026-07-24).
    if is_android_permission_dialog(xml):
        return "SYS_PERMISSION"

    # --- Create / compose states BEFORE tip-sheet (tip heuristics steal EDIT) ---
    # Caption: IG often uses a clickable TextView ("Add a caption…") with 0 EditTexts
    # until tapped (2026-07-24 mysticnova54 UNKNOWN dump had Share + Write a caption).
    # After typing, placeholder text may vanish — keep tag/location/also-share / share_button
    # (ernvra26 2026-08-04: typed caption → false EDIT_SCREEN → top Next).
    if any(p in tb for p in ["write a caption", "add a caption", "caption…", "caption...",
                               "tag people", "add location", "also share on", "also share to",
                               "also share"]):
        return "CAPTION_SCREEN"
    _has_share_rid = any("share_button" in attr(n, "resource-id").lower()
                         for n in nodes(xml))
    if _has_share_rid and "new reel" in tb and "recents" not in tb and \
       "thumbnail" not in tb:
        return "CAPTION_SCREEN"

    if any(p in tb for p in ["recents", "gallery", "camera roll", "library"]) and \
       any(p in tb for p in ["next", "multi-select", "select multiple", "selected",
                               "photo thumbnail", "video thumbnail", "video,",
                               "cam_dest_clips", "unselected photo", "unselected video"]):
        return "CREATE_PICKER"

    # Gallery with POST/STORY/REEL dest tabs (dustin dump) even without "next" yet
    if any(p in tb for p in ("recents", "gallery", "camera roll")) and \
       ("cam_dest" in tb or (tb.count("post") + tb.count("story") + tb.count("reel") >= 2
        and "thumbnail" in tb)):
        return "CREATE_PICKER"

    # Reel create tips BEFORE camera (2026-08-03 dumps: Save reels / camera toolbar)
    if _is_reel_create_tip_tb(tb):
        return "TIP_SHEET"

    # Reel/story camera (no gallery grid yet) — not tip copy about "front camera"
    if _is_reel_camera_tb(tb):
        return "CREATE_CAMERA"

    # Real edit chrome — never gallery / caption (title "New reel" + Next alone ≠ edit)
    if any(p in tb for p in ["new post", "new reel", "edit", "filter", "trim",
                               "edit photo", "edit video", "edit cover", "add audio",
                               "add music"]) and \
       ("next" in tb or "ok" in tb or "done" in tb) and nedits == 0 and \
       "what do you want to share" not in tb and \
       "add a caption" not in tb and "write a caption" not in tb and \
       "tag people" not in tb and "add location" not in tb and \
       "also share" not in tb and \
       "recents" not in tb and "thumbnail" not in tb and \
       "unselected video" not in tb and "unselected photo" not in tb and \
       "select multiple" not in tb:
        return "EDIT_SCREEN"

    # Reel edit toast-only chrome (bode54373 2026-08-03: only "Add audio", no Next yet)
    if any(p in tb for p in ("add audio", "edit cover", "add music", "edit video",
                               "suggested audio", "continue without")) and \
       "recents" not in tb and "photo thumbnail" not in tb and "video thumbnail" not in tb and \
       "unselected video" not in tb and "unselected photo" not in tb and \
       "what do you want to share" not in tb:
        return "EDIT_SCREEN"

    # IG create chooser sheet — explicit sheet copy only (not feed chrome)
    if "what do you want to share" in tb or \
       ("create new" in tb and any(p in tb for p in ["post", "story", "reel"])) or \
       (tb.count("post") + tb.count("story") + tb.count("reel") >= 3
        and "new post" in tb and "your story" not in tb):
        if "gallery" in tb or "recents" in tb or "camera" in tb:
            return "CREATE_PICKER"
        return "CREATE_CHOOSER"

    # Contacts / find friends soft prompts (skippable)
    if any(p in tb for p in [
            "connect to contacts", "sync your contacts", "find friends",
            "see who you know", "access your contacts", "upload contacts",
            "follow facebook friends", "facebook friends on instagram"]) and \
       any(p in tb for p in ["not now", "skip", "continue", "allow", "next"]):
        return "TIP_SHEET"

    # Story compose canvas / text tool before tip heuristics
    if _is_story_text_tool(xml, tb):
        return "EDIT_SCREEN"
    if _is_story_editor_chrome(xml, tb):
        return "EDIT_SCREEN"

    # Structural tip sheet — after create/edit so we don't steal compose UI
    if is_tip_sheet(xml, tb, nedits):
        return "TIP_SHEET"

    # Search/Explore tab — BEFORE generic FEED (bottom nav fools FEED heuristic)
    if _is_search_explore_tab(xml, tb):
        return "SEARCH_TAB"

    # Logged-in IG feed MUST be before vague create heuristics on home chrome
    if any(p in tb for p in ["your story", "suggested for you"]) or \
       (any(p in tb for p in ["home", "reels"]) and
        any(p in tb for p in ["profile", "create", "shop", "direct", "new post"])):
        if ("log in" not in tb or "password" not in tb) and \
           "what do you want to share" not in tb and \
           "create new" not in tb:
            return "FEED"

    if any(p in tb for p in ["write a caption", "add a caption", "caption…", "caption...",
                               "tag people", "add location", "also share on", "share"]):
        if nedits >= 1 or "add a caption" in tb or "write a caption" in tb:
            return "CAPTION_SCREEN"

    # Welcome / create-vs-login landing (no password fields yet)
    if nedits < 2 and "password" not in tb and \
       any(p in tb for p in ["welcome to instagram", "create new account",
                               "create an account", "sign up for instagram",
                               "join instagram"]) and \
       any(p in tb for p in ["log in", "already have an account",
                               "already have a profile"]):
        return "LOGIN_LANDING"

    # Soft "Confirm you're human to use your account" checkpoint
    if "confirm you're human" in tb or "confirm you are human" in tb or \
       ("confirm you" in tb and "to use your account" in tb):
        return "HUMAN_CHECK"

    if "suspended your account" in tb or "we suspended" in tb or \
       ("suspended" in tb and "appeal" in tb) or \
       ("account" in tb and "permanently disable" in tb):
        return "ACCOUNT_SUSPENDED"

    if any(p in tb for p in ["suspicious login", "verify your account",
                               "we detected an unusual", "confirm your identity",
                               "this account may have been",
                               "enter security code", "we need to confirm",
                               "upload a photo", "take a video selfie",
                               "confirm your phone"]):
        return "CHALLENGE"

    # Lone loading spinner — longer patience budget (handover Jul 20 fix)
    if tb.strip() in ("loading...", "loading…") or tb.strip().startswith("loading"):
        return "LOADING"

    return "UNKNOWN"

# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------

def do_login(pkg, username, password, tfa_secret):
    global CURRENT_PKG, CURRENT_USER
    CURRENT_PKG = pkg
    CURRENT_USER = (username or "").strip()
    print("[login] %s on %s" % (username, pkg.split(".")[-1]))
    do_login._meta_err_n = 0
    do_login._passkey_n = 0

    if not launch(pkg):
        print("[FAIL] app wont open"); return emit_fail("APP_WONT_OPEN")

    time.sleep(2)

    human_taps = 0
    saw_login = False   # did we ever reach the credential entry screen this run?
    unknown_run = 0     # consecutive UNKNOWN steps (reset on any recognized state)
    loading_run = 0      # consecutive LOADING steps - separate, longer budget than UNKNOWN
    signup_hits = 0      # SIGNUP_GATE / LOGIN_LANDING visits; bail if looping without login
    thin_login_run = 0   # LOGIN_SCREEN with <2 EditTexts (post-submit hang / false detect)
    login_submitted = False
    tip_hits = 0         # consecutive TIP_SHEET/ONBOARD after login (yuna94162 Got-it loop)
    tip_last_tb = ""
    notif_hits = 0       # consecutive NOTIF_PROMPT — fail-fast (2026-08-12 spin)
    blank_recovers = 0   # empty UI dumps → force-stop + relaunch (elinagrace / remington)
    LOADING_BAIL_AT = 40 # ~80s of patience for network congestion, vs 24s for true UNKNOWN
    # angelpetal98 2026-07-24: 70+ steps of "<2 fields -> wait" until LOGIN_TIMEOUT
    THIN_LOGIN_DUMP_AT = 6
    THIN_LOGIN_RETAP_AT = 8
    THIN_LOGIN_BAIL_AT = 14  # ~28s — fail-fast instead of burning MAX_STEPS
    TIP_SHEET_BAIL_AT = 8    # same tip text / Got it thrash → bail (2026-07-29 yuna94162)
    NOTIF_BAIL_AT = 6        # Next not advancing → TIP_STUCK (don't burn 90 steps)
    BLANK_RECOVER_MAX = 2
    do_login._onboard_dump = 0
    for step in range(MAX_STEPS):
        # Never drift onto Threads mid-IG-login (BACK from Join screen did this 2026-07-23).
        cur = fg_pkg()
        if cur and "barcel" in cur and pkg not in cur:
            print("[guard] Threads fg=%s during IG login -> re-launch %s"
                  % (cur.split(".")[-1], pkg.split(".")[-1]))
            if not launch(pkg):
                print("[FAIL] cannot re-open IG after Threads steal"); return emit_fail("APP_WONT_OPEN")
            time.sleep(1.5)

        xml = dump()
        state = detect_state(xml)
        print("[step %02d] %s | fg=%s" % (step, state, fg_pkg().split(".")[-1]))
        _sw_step("login", state=state, note="login step %d" % step,
                 expected="FEED or credential entry")

        if state == "LOADING":
            loading_run += 1
            if loading_run >= LOADING_BAIL_AT:
                print("[FAIL] LOADING_STUCK (%d loading screens in a row, network never resolved)"
                      % loading_run)
                return emit_fail("UNKNOWN_STUCK")   # same downstream handling as before (retriable IP mask)
            time.sleep(STEP_PAUSE); continue
        loading_run = 0

        if state in ("SYS_SETTINGS", "WRONG_APP"):
            if not _recover_from_sys_settings(pkg, label="login-%s" % state.lower()):
                print("[FAIL] cannot leave %s back to IG" % state)
                return emit_fail("APP_WONT_OPEN")
            time.sleep(1.5)
            continue

        if state == "UNKNOWN":
            unknown_run += 1
            # Blank / empty dump (2026-07-29 elinagrace87, remington.morrow): wake +
            # force-stop + relaunch before UNKNOWN_STUCK burns the slot.
            if (_ui_blank(xml) or not (fg_pkg() or "").strip()) and \
               blank_recovers < BLANK_RECOVER_MAX:
                blank_recovers += 1
                print("[guard] blank/empty UI -> wake + force-stop + relaunch (%d/%d)"
                      % (blank_recovers, BLANK_RECOVER_MAX))
                wake_unlock()
                force_stop(pkg)
                time.sleep(1.0)
                if launch(pkg):
                    unknown_run = 0
                    time.sleep(2.0)
                    continue
            # Settings / wrong-app that slipped past detect (package empty in dump)
            if _is_sys_settings_xml(xml) or _is_wrong_app_xml(xml) or \
               "settings" in (fg_pkg() or "").lower():
                print("[guard] UNKNOWN but Settings/wrong-app — recover")
                if _recover_from_sys_settings(pkg, label="login-unknown-settings"):
                    unknown_run = 0
                    time.sleep(1.5)
                    continue
            # After SAVE_INFO, IG sometimes drops to Android home (glowwish78 2026-07-28:
            # Super Proxy / Surfshark / Play Store launcher) — re-launch target clone.
            if saw_login and unknown_run <= 3:
                tb0 = text_block(xml).lower()
                if ("play store" in tb0 or "super proxy" in tb0) and (
                        "messaging" in tb0 or "contacts" in tb0 or "camera" in tb0):
                    print("[guard] Android home after login -> re-launch %s"
                          % pkg.split(".")[-1])
                    if launch(pkg):
                        time.sleep(2.0)
                        unknown_run = 0
                        continue
            # DIAGNOSE-and-bail only. We deliberately do NOT auto-tap an unrecognized
            # screen: detect_state already has safe handlers (ONBOARD_CARD, NOTIF_PROMPT,
            # SAVE_INFO, ADD_PHONE, ...), so anything still UNKNOWN escaped all of them.
            # tap_first matches substrings, and labels like "Allow"/"Continue" can opt
            # the account into something irreversible on a screen we can't see. The fix
            # for an UNKNOWN screen is to READ this dump and add one line to detect_state.
            # (A blank black-screen render hang also lands here -> we bail fast instead
            # of burning all 90 steps, and the caller relaunches the clone.)
            if unknown_run == UNKNOWN_DUMP_AT:
                print("[unknown] %d in a row - dumping screen for inspection:" % unknown_run)
                print("[unknown] text: %s" % text_block(xml)[:600])
                print("[unknown] clickable nodes:")
                for n in nodes(xml):
                    if attr(n, "clickable") != "true":
                        continue
                    cx, cy = bounds_center(n)
                    print("   %s,%s %-12s text='%s' desc='%s'"
                          % (cx, cy, attr(n, "class").split(".")[-1],
                             attr(n, "text"), attr(n, "content-desc")))
            # A fresh IP can't fix a UI/detector gap, so bail FAST (not all 90 steps).
            # UNKNOWN_STUCK is absent from run_device.py IP_MASK => caller stops
            # rotating and keeps the IP.
            if unknown_run >= UNKNOWN_BAIL_AT:
                print("[FAIL] UNKNOWN_STUCK (%d unknown screens in a row, no progress)"
                      % unknown_run)
                return emit_fail("UNKNOWN_STUCK")
            time.sleep(STEP_PAUSE); continue
        else:
            unknown_run = 0

        if state == "LOGIN_SCREEN":
            saw_login = True

        if state in ("FEED", "CREATE_PICKER", "CAPTION_SCREEN"):
            # Drain any trailing tip sheets still stacked on top of feed chrome
            _drain_post_login_tips(max_steps=8)
            print("[OK] at feed/create"); return "LOGGED_IN"

        if state == "GOOGLE_SAVE":
            # Button label varies: "Not now" first time, "Never" after.
            # "Never" stops Google offering to save for this app at all = best.
            xml = dump()
            if not tap_first(xml, "Never", "Not now", "Not Now", "No thanks",
                             "No, thanks", label="dismiss-google-save"):
                print("[google-save] no dismiss btn -> BACK")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(2); continue

        if state == "PASSKEY_PROMPT":
            # Credential Manager sheet. "Sign in another way" often isn't a real
            # button hit; Cancel (content-desc) is. Bail if sheet won't leave.
            pk_n = getattr(do_login, "_passkey_n", 0) + 1
            do_login._passkey_n = pk_n
            if pk_n > 6:
                print("[FAIL] PASSKEY_PROMPT stuck x%d" % pk_n)
                return emit_fail("PASSKEY_STUCK")
            xml = dump()
            tapped = False
            for n in nodes(xml):
                if attr(n, "clickable") != "true":
                    continue
                d = (attr(n, "content-desc") or "").strip().lower()
                t = (attr(n, "text") or "").strip().lower()
                if d == "cancel" or t == "cancel":
                    cx, cy = bounds_center(n)
                    if cx and cy:
                        print("[tap] passkey-cancel @ %d,%d" % (cx, cy))
                        adb("shell", "input", "tap", str(cx), str(cy))
                        tapped = True
                        break
            if not tapped:
                if not tap_first(xml, "Sign in another way", "Use password",
                                 "More options", label="passkey-password-path"):
                    print("[passkey] no cancel -> BACK")
                    adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(2); continue

        if state == "LOGIN_ERROR_DIALOG":
            # Meta "Unable to log in / unexpected error" — fail immediately.
            # Do not dismiss+retry (burns time; not fixed by same sticky IP).
            snip = " | ".join(
                t for t in (
                    attr(n, "text").strip() for n in nodes(xml)
                    if attr(n, "text").strip()
                ) if t
            )[:240]
            print("[login-error-dialog] %s" % (snip or "(no text)"))
            print("[FAIL] Meta 'Unable to log in' (no retry; not an IP rotate)")
            return emit_fail("LOGIN_META_ERROR")

        if state == "LOGIN_ERROR":
            tb = text_block(xml)
            if any(p in tb for p in ["can't find an account", "cant find an account",
                                       "couldn't find an account",
                                       "we can't find an account",
                                       "we couldn't find your account"]):
                print("[FAIL] account not found in Instagram (bad username / deleted)")
                return emit_fail("ACCOUNT_NOT_FOUND")
            print("[FAIL] login rejected by Instagram (bad creds or IP flag)")
            return emit_fail("LOGIN_REJECTED")

        if state == "ACCOUNT_NOT_FOUND":
            # Detected from recover / device-match screens (see detect_state).
            print("[FAIL] account not found / recover dead-end (orphaned login)")
            return emit_fail("ACCOUNT_NOT_FOUND")

        if state == "SIGNUP_GATE":
            # Same role as Threads THREADS_LANDING: leave create-account → login.
            # 2026-07-23 Join screen copy: "I already have a profile" (not "account").
            signup_hits += 1
            # Bail even after a brief LOGIN_SCREEN flash (saw_login) — otherwise
            # glowaura/velvetwish burn 30+ steps oscillating SIGNUP_GATE (2026-07-28).
            if signup_hits > 8:
                print("[FAIL] SIGNUP_LOOP (stuck leaving create-account)")
                return emit_fail("SIGNUP_LOOP")
            xml = dump()
            tapped = tap_exact(xml, "I already have a profile",
                               "LOG IN", "Log in", "Log In",
                               label="signup-have-profile")
            if not tapped:
                tapped = tap_first(xml, "I already have a profile",
                                   "I already have an account",
                                   "Already have an account",
                                   label="already-have-account")
            if not tapped:
                print("[signup-gate] no login divert btn (NOT pressing BACK — avoids Threads)")
            time.sleep(2.5); continue

        if state == "LOGIN_LANDING":
            signup_hits += 1
            if signup_hits > 8:
                print("[FAIL] SIGNUP_LOOP (login landing won't advance)")
                return emit_fail("SIGNUP_LOOP")
            xml = dump()
            tapped = tap_exact(xml, "I already have a profile",
                               "LOG IN", "Log in", "Log In",
                               label="landing-have-profile")
            if not tapped:
                tapped = tap_first(xml, "I already have a profile",
                                   "I already have an account",
                                   "Already have an account",
                                   label="landing-already")
            if not tapped:
                print("[login-landing] no Log in / have-profile (NOT pressing BACK)")
            time.sleep(2.5); continue

        if state == "LOGIN_SCREEN":
            # If fields aren't found, the screen has likely already moved on
            # (login submitted / a dialog appeared) — wait briefly, then fail-fast.
            # Placeholder text can still match LOGIN_SCREEN with 0–1 EditTexts.
            xml = dump()
            es0 = edits(xml)
            if len(es0) < 2:
                thin_login_run += 1
                tb_thin = text_block(xml)
                # Meta dialog: bad/missing username → "CREATE NEW ACCOUNT" + OK
                # (9U8VwuD579 2026-07-28). Dismiss and fail-fast — not a submit wait.
                if "create new account" in tb_thin and (
                        "enter your username" in tb_thin or "to log in" in tb_thin):
                    print("[login] thin dialog = bad username (CREATE NEW ACCOUNT)")
                    tap_exact(xml, "OK", "Ok", label="thin-bad-user-ok")
                    print("[FAIL] account not found in Instagram (bad username / deleted)")
                    return emit_fail("ACCOUNT_NOT_FOUND")
                print("[login] LOGIN_SCREEN but <2 fields -> wait "
                      "(likely already submitting) [%d/%d]"
                      % (thin_login_run, THIN_LOGIN_BAIL_AT))
                if thin_login_run == THIN_LOGIN_DUMP_AT:
                    print("[login] thin-login dump: %s" % tb_thin[:400])
                    for n in nodes(xml):
                        if attr(n, "clickable") != "true":
                            continue
                        cx, cy = bounds_center(n)
                        print("   %s,%s text='%s' desc='%s'"
                              % (cx, cy, attr(n, "text")[:40],
                                 attr(n, "content-desc")[:40]))
                if thin_login_run == THIN_LOGIN_RETAP_AT and login_submitted:
                    # Re-press in case first Log in was ignored
                    _press_login_submit(max_tries=2, label="login-btn-retap")
                if thin_login_run >= THIN_LOGIN_BAIL_AT:
                    print("[FAIL] LOGIN_STUCK (LOGIN_SCREEN <2 fields x%d after submit=%s)"
                          % (thin_login_run, login_submitted))
                    return emit_fail("LOGIN_STUCK")
                time.sleep(2.0); continue
            thin_login_run = 0
            if not set_field(0, username, "username"):
                st2 = detect_state(dump())
                if st2 != "LOGIN_SCREEN":
                    print("[login] username skip — now %s" % st2)
                time.sleep(1); continue
            if not set_field(1, password, "password"):
                st2 = detect_state(dump())
                if st2 != "LOGIN_SCREEN":
                    print("[login] password skip — now %s" % st2)
                time.sleep(1); continue
            if not _press_login_submit(max_tries=5, label="login-btn"):
                print("[FAIL] no login button"); return emit_fail("NO_LOGIN_BTN")
            login_submitted = True
            time.sleep(5); continue

        if state == "2FA_PUSH":
            # Account defaults to push-approval; we can't approve on another device.
            # Switch to the authenticator-code method.
            xml = dump()
            if not tap_first(xml, "Try another way", "Try Another Way",
                             "another way", label="try-another-way"):
                print("[wait] push 2FA, 'try another way' not visible yet")
            time.sleep(3); continue

        if state == "2FA_CHOOSE":
            xml = dump()
            tapped = tap_first(xml, "Get a code from your authentication app",
                               "Authentication app", "authenticator",
                               "Use your authentication app", "code generator",
                               label="choose-auth-app")
            if tapped:
                time.sleep(1.5)
                xml2 = dump()
                tap_first(xml2, "Continue", "Next", "Confirm", label="choose-continue")
            time.sleep(3); continue

        if state == "2FA_SCREEN":
            code = totp(tfa_secret)
            print("[totp] %s" % code)
            if not set_field(0, code, "totp"):
                st2 = detect_state(dump())
                if st2 != "2FA_SCREEN":
                    print("[2fa] field gone -> now %s" % st2)
                time.sleep(1); continue
            xml = dump()
            tap_first(xml, "Confirm", "Continue", "Next", "Verify", label="confirm-2fa")
            time.sleep(3); continue

        if state == "2FA_BAD_CODE":
            xml = dump()
            tap_exact(xml, "OK", "Ok", "Try Again", label="2fa-bad-ok")
            time.sleep(1.5); continue

        if state == "FOLLOW_SUGGESTIONS":
            xml = dump()
            _dismiss_follow_suggestions(xml, label="skip-follow")
            time.sleep(2); continue

        if state == "BIRTHDAY":
            xml = dump()
            tap_first(xml, "Skip", "Next", "Continue", label="skip-bday")
            time.sleep(2); continue

        if state == "ADD_PHONE":
            xml = dump()
            tap_first(xml, "Skip", "Not now", label="skip-phone")
            time.sleep(2); continue

        if state == "NOTIF_PROMPT":
            notif_hits += 1
            xml = dump()
            v = _notif_gate_variant(text_block(xml))
            print("[notif] login NOTIF_PROMPT hit=%d/%d variant=%s — press Next"
                  % (notif_hits, NOTIF_BAIL_AT, v or "?"))
            cleared = _dismiss_notif_prompt(xml, label="skip-notif")
            if not cleared:
                cleared = _clear_notif_gate(max_tries=2, label="login-notif")
            if cleared:
                notif_hits = 0
            elif notif_hits >= NOTIF_BAIL_AT:
                print("[FAIL] NOTIF_PROMPT Next not advancing after %d hits variant=%s"
                      % (notif_hits, v or "?"))
                return emit_fail("TIP_STUCK", note="notif_next_no_advance:%s" % (v or "?"))
            time.sleep(1.0)
            if _is_sys_settings_xml() or _is_wrong_app_xml():
                _recover_from_sys_settings(pkg, label="notif-leak-settings")
            else:
                dismiss_android_permission()
            continue
        notif_hits = 0

        if state == "SAVE_INFO":
            xml = dump()
            if not tap_exact(xml, "Not now", "Not Now", label="skip-save"):
                dismiss_tip_sheet(xml, label="skip-save-tip")
            time.sleep(2); continue

        if state == "EMAIL_SECURITY":
            xml = dump()
            # Never tap Add new contact info — Back only (naz.li3070 2026-08-12)
            if not tap_first(xml, "Not now", "Not Now", "Skip", "Later",
                             label="skip-email-secure"):
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                print("[email-security] no skip btn -> BACK")
            time.sleep(2); continue

        if state == "SYS_PERMISSION":
            dismiss_android_permission()
            time.sleep(1.5); continue

        if state in ("TIP_SHEET", "ONBOARD_CARD"):
            xml = dump()
            if not saw_login:
                # Pre-login: never Get started / BACK (BACK → Threads). Prefer have-profile.
                if do_login._onboard_dump < 1:
                    do_login._onboard_dump = 1
                    print("[tip-prelogin] %s" % text_block(xml)[:320])
                if tap_exact(xml, "I already have a profile",
                             "LOG IN", "Log in", "Log In",
                             "Allow", "Accept", "Accept all", "OK", "Got it",
                             "Agree and continue", "I agree", "Not now", "Skip",
                             label="tip-prelogin"):
                    time.sleep(2); continue
                if tap_first(xml, "I already have a profile",
                             "I already have an account", label="tip-have-profile"):
                    time.sleep(2); continue
                print("[tip-prelogin] no safe btn — wait (no BACK, no Get started)")
                time.sleep(2); continue
            # Post-login: structural dismiss (exact CTAs only)
            tb_tip = text_block(xml)
            # Same tip text repeating = CTA not advancing (Got it loop on feed tip).
            if tip_last_tb and tb_tip[:120] == tip_last_tb[:120]:
                tip_hits += 1
            else:
                tip_hits = 1
                tip_last_tb = tb_tip
            if tip_hits >= TIP_SHEET_BAIL_AT:
                print("[FAIL] TIP_STUCK (same tip x%d — e.g. Got it not dismissing)"
                      % tip_hits)
                # Last try: BACK then re-launch so FEED can be detected next caller's retry
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(1)
                launch(pkg)
                return emit_fail("TIP_STUCK")
            if do_login._onboard_dump < 3:
                do_login._onboard_dump += 1
                print("[tip] %s" % tb_tip[:240])
            if not dismiss_tip_sheet(xml, label="tip-sheet"):
                print("[tip] no safe CTA — wait (no BACK)")
            time.sleep(1.8)
            dismiss_android_permission()
            continue
        else:
            tip_hits = 0
            tip_last_tb = ""

        if state == "HUMAN_CHECK":
            human_taps += 1
            if human_taps > 2:
                print("[FAIL] human-check won't clear -> account flagged")
                return emit_fail("HUMAN_BLOCKED")
            xml = dump()
            tap_first(xml, "Continue", "OK", "Next", label="human-check-continue")
            time.sleep(3); continue

        if state == "ACTION_LIMIT":
            print("[FAIL] Meta ACTION_LIMIT during login — cool account")
            return fail_action_limit(note="login")

        if state == "CAPTCHA":
            if not saw_login:
                print("[FAIL] captcha before login -> ACCOUNT challenged (IP won't help)")
                return emit_fail("ACCOUNT_CHALLENGED")
            print("[FAIL] image captcha at cold login -> IP not trusted, needs fresh IP")
            return emit_fail("CAPTCHA")

        if state == "CONTACT_VERIFY":
            if not saw_login:
                print("[FAIL] contact-verify before login -> ACCOUNT gated")
                return emit_fail("ACCOUNT_CHALLENGED")
            print("[FAIL] phone/email verification demanded -> needs fresh IP")
            return emit_fail("CONTACT_VERIFY")

        if state == "ACCOUNT_SUSPENDED":
            print("[FAIL] account suspended -> dead on arrival (day-one burn, not IP)")
            return emit_fail("ACCOUNT_SUSPENDED")

        if state == "CHALLENGE":
            print("[FAIL] account challenged/flagged"); return emit_fail("CHALLENGE")

        time.sleep(STEP_PAUSE)

    print("[FAIL] LOGIN_TIMEOUT"); return emit_fail("LOGIN_TIMEOUT")

# ---------------------------------------------------------------------------
# Post flow (Instagram feed IMAGE — not Threads)
# ---------------------------------------------------------------------------

def _drain_post_login_tips(max_steps=14):
    """Clear tip/permission cascade before create. Exact CTAs only."""
    sys_perm_hits = 0
    for i in range(max_steps):
        xml = dump()
        st = detect_state(xml)
        print("[drain %02d] %s" % (i, st))
        if st in ("CREATE_PICKER", "CREATE_CHOOSER", "CAPTION_SCREEN", "EDIT_SCREEN"):
            return True
        if st in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE",
                  "ACTION_LIMIT"):
            # galabelle4 2026-07-27: drain looped on CAPTCHA until create POST_TIMEOUT
            # suzukii20648 2026-08-12: ACTION_LIMIT must not burn create timeout
            print("[drain] blocked by %s — stop (need fresh IP / cool phone)" % st)
            return False
        if st == "SYS_PERMISSION":
            sys_perm_hits += 1
            ok = dismiss_android_permission()
            if not ok or sys_perm_hits >= 3:
                print("[drain] SYS_PERMISSION stuck — continue anyway")
                # Escape false-positive / undismissable dialog
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(1.0)
                if sys_perm_hits >= 3:
                    break
            time.sleep(1.0); continue
        if st in ("SYS_SETTINGS", "WRONG_APP"):
            _recover_from_sys_settings(CURRENT_PKG, label="drain-%s" % st.lower())
            time.sleep(1.5)
            continue
        if st in ("TIP_SHEET", "ONBOARD_CARD", "NOTIF_PROMPT", "SAVE_INFO",
                  "FOLLOW_SUGGESTIONS", "ADD_PHONE", "GOOGLE_SAVE", "PASSKEY_PROMPT"):
            if st == "SAVE_INFO":
                tap_exact(xml, "Not now", "Not Now", label="drain-save")
            elif st == "GOOGLE_SAVE":
                tap_first(xml, "Never", "Not now", "Not Now", label="drain-google")
            elif st == "PASSKEY_PROMPT":
                tapped = False
                for n in nodes(xml):
                    if attr(n, "clickable") != "true":
                        continue
                    d = (attr(n, "content-desc") or "").strip().lower()
                    t = (attr(n, "text") or "").strip().lower()
                    if d == "cancel" or t == "cancel":
                        cx, cy = bounds_center(n)
                        if cx and cy:
                            print("[tap] drain-passkey-cancel @ %d,%d" % (cx, cy))
                            adb("shell", "input", "tap", str(cx), str(cy))
                            tapped = True
                            break
                if not tapped:
                    if not tap_first(xml, "Sign in another way", "Use password",
                                     "Cancel", label="drain-passkey"):
                        adb("shell", "input", "keyevent", "KEYCODE_BACK")
            elif st == "FOLLOW_SUGGESTIONS":
                _dismiss_follow_suggestions(xml, label="drain-follow")
            elif st == "NOTIF_PROMPT":
                _dismiss_notif_prompt(xml, label="drain-notif")
                if _is_sys_settings_xml() or _is_wrong_app_xml():
                    _recover_from_sys_settings(CURRENT_PKG, label="drain-notif-settings")
            else:
                dismiss_tip_sheet(xml, label="drain-tip")
            time.sleep(1.5)
            continue
        if st == "LOADING":
            time.sleep(2); continue
        # Tip overlays often sit ON the feed (tree still says FEED / your story).
        found = {lab for lab, _ in _clickable_exact_labels(xml)}
        tb = text_block(xml)
        tip_copy = any(p in tb for p in (
            "got it", "location services", "set up on new device", "simplified our navigation",
            "swipe to easily", "access your location", "discover places near you",
            "turn on notifications", "see who you know", "connect to contacts",
            "cookies", "community guidelines", "how we'll use this information",
            "follow 5 or more people", "following isn't required",
        ))
        overlay = found & {"got it", "continue", "not now", "skip", "ok"}
        if ("got it" in found) or (tip_copy and overlay) or \
           (overlay and _clickable_count(xml) <= 18 and st != "LOGIN_SCREEN"):
            if st not in ("LOGIN_SCREEN", "2FA_SCREEN", "2FA_CHOOSE", "SIGNUP_GATE",
                          "LOGIN_LANDING", "CAPTCHA"):
                print("[drain] tip CTA on %s → %s" % (st, sorted(overlay or found)))
                dismiss_tip_sheet(xml, label="drain-overlay")
                time.sleep(1.5)
                continue
        if st == "FEED":
            return True
        if is_tip_sheet(xml):
            print("[drain] unclassified tip-sheet → dismiss")
            dismiss_tip_sheet(xml, label="drain-struct")
            time.sleep(1.5); continue
        return st == "FEED"
    return detect_state(dump()) in ("FEED", "CREATE_PICKER", "CREATE_CHOOSER", "UNKNOWN")


def tap_share_btn(xml):
    """Exact Share/Post on the caption screen — prefer right-most exact match.

    Never the bottom dest tab 'POST' (y near nav) — that is create dest, not Share
    (dorothhds129 2026-08-30: share-btn @ 518,2797 switched to Post instead of Story).
    """
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    best = None
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        rid = attr(n, "resource-id").lower()
        if t in ("share", "post") or d in ("share", "post"):
            if "cam_dest_" in rid:
                continue
            x, y = bounds_center(n)
            if x is None or y is None:
                continue
            if sh and y > sh * 0.78:
                continue
            if best is None or x > best[0]:
                best = (x, n)
    if best:
        return tapn(best[1], "share-btn")
    return False


ADB_IME = "com.android.adbkeyboard/.AdbIME"


def _adb_ime_off():
    """AdbIME {ON} bar sits on the footer and eats Next/Share taps.

    Nylah 2026-09-02 POST_TIMEOUT: visible CTA was blue Next; XML share_button
    @ 1068,2762 hit the ADB Keyboard strip instead of publishing.
    """
    adb("shell", "ime", "disable", ADB_IME)
    adb("shell", "input", "keyevent", "KEYCODE_ESCAPE")
    time.sleep(0.3)


def _tap_caption_footer_next(xml=None):
    """Tap the blue caption footer Next (text). Allow y through ~0.97*sh.

    0.93 cutoff skipped the real Next @1068,2762 on Note 8 (Nylah prove).
    Skip far-right nav (clips_right / Profile ~1287).
    """
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    floor = int(sh * 0.68)
    ceil = int(sh * 0.98)
    right_nav = int(sw * 0.88) if sw else 1200
    best = None
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t != "next" and d != "next":
            continue
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        rid = attr(n, "resource-id").lower()
        if "clips_right_action" in rid or "cam_dest_" in rid:
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < floor or y > ceil:
            continue
        if x >= right_nav:
            continue
        w, h = bounds_wh(n)
        area = (w or 0)
        if best is None or area > best[0]:
            best = (area, n, x, y)
    if not best:
        return False
    return tapn_cta(best[1], "pub-footer-next")


def _hide_ime(xml=None):
    """Dismiss soft keyboard so bottom Share/Next is tappable.

    mahnoormagic2 2026-08-05: old tap(720,380) hit Reel caption **Edit cover**.
    kadriye/kub.ra 2026-08-10: tap(40,1500) on New post hit Add audio / Tag people
    → sheets over Share → POST_TIMEOUT with Share still visible.
    On caption chrome, unfocus on media-preview left only.
    """
    adb("shell", "input", "keyevent", "KEYCODE_ESCAPE")
    time.sleep(0.15)
    xml = xml if xml is not None else dump()
    tb = text_block(xml).lower()
    sw, sh = _screen_wh(xml)
    if any(p in tb for p in (
            "add a caption", "write a caption", "also share on", "tag people",
            "add location", "new post")):
        # Media preview / header left — never mid-list rows
        tap(56, max(260, min(520, int(sh * 0.18))))
        time.sleep(0.3)
        return
    # Generic unfocus (login / other): mid-left, avoid bottom CTAs
    tap(40, min(1500, int(sh * 0.48)))
    time.sleep(0.35)


def _is_edit_cover_screen(xml=None):
    """Reel 'Edit cover' picker — Done returns to caption (not true Edit video)."""
    xml = xml or dump()
    tb = text_block(xml)
    if any(p in tb for p in (
            "select a cover image", "add from camera roll", "crop profile image",
            "edit cover")) and any(p in tb for p in ("done", "add text")):
        # Caption chrome may say "edit cover" as a chip — require cover-picker copy
        if "write a caption" in tb or "tag people" in tb or "also share" in tb:
            return False
        return True
    if "select a cover image" in tb or (
            "add from camera roll" in tb and "edit cover" in tb):
        return True
    return False


def _dismiss_edit_cover(xml=None, label="edit-cover"):
    """Leave Edit cover via Done (back to caption). Never Keep exploring gallery."""
    xml = xml or dump()
    if not _is_edit_cover_screen(xml):
        return False
    if tap_exact(xml, "Done", "done", label="%s-done" % label):
        print("[pub] dismissed Edit cover via Done")
        time.sleep(1.2)
        return True
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t == "done" or d == "done":
            if tapn(n, "%s-done-node" % label):
                print("[pub] dismissed Edit cover via Done node")
                time.sleep(1.2)
                return True
    # Back once — may return to caption
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    print("[pub] Edit cover BACK")
    time.sleep(1.0)
    return True


def _still_in_composer(xml=None, fmt="reel"):
    """True while still inside create/edit/caption (NOT a successful publish)."""
    xml = xml or dump()
    fmt = (fmt or "reel").lower()
    st = detect_state(xml)
    if st in ("CAPTION_SCREEN", "EDIT_SCREEN", "CREATE_PICKER", "CREATE_CAMERA",
              "CREATE_CHOOSER"):
        return True
    if in_caption_screen(xml):
        return True
    tb = text_block(xml)
    if _is_giphy_overlay(xml, tb) or _is_story_to_story_nux(xml, tb):
        return True
    if fmt in ("feed", "carousel", "reel") and _is_story_editor_chrome(xml, tb):
        return True
    if any(p in tb for p in ("save draft", "write a caption", "add a caption")):
        return True
    if fmt == "reel":
        if _is_edit_cover_screen(xml):
            return True
        if _reel_at_caption(xml) or _reel_edit_ready(xml):
            return True
        if any(p in tb for p in ("edit cover", "add ai label")):
            return True
        if "new reel" in tb and ("next" in tb or "share" in tb):
            return True
    if fmt == "story":
        # Story editor chrome (not profile "Your story" alone on feed)
        if any(p in tb for p in ("your story", "add to your story", "share to story")) and \
           any(p in tb for p in ("stickers", "sticker", "done", "create", "aa ", "text",
                                   "download", "save draft", "link")):
            return True
        if "add to story" in tb and st not in ("FEED",):
            return True
    if fmt in ("feed", "carousel"):
        if "new post" in tb and ("next" in tb or "share" in tb or "filter" in tb):
            return True
    return False


def _still_in_reel_composer(xml=None):
    return _still_in_composer(xml, fmt="reel")


def _publish_succeeded(xml=None, fmt="reel"):
    """Strict: toast / left composer. EDIT or caption after CTA ≠ success.

    nila/aylin 2026-08-11: landing on Profile via bottom coord (1287,2819) is NOT
    a share — 'edit profile' used to false-succeed then verify saw 0 posts.
    """
    xml = xml or dump()
    fmt = (fmt or "reel").lower()
    tb = text_block(xml)
    tbl = tb.lower()
    if any(p in tbl for p in ("sharing", "uploading", "posting", "processing",
                               "your reel has been shared", "your post has been shared",
                               "reel has been shared", "post has been shared",
                               "story has been shared", "shared to your story")):
        return True
    st = detect_state(xml)
    if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE"):
        return False
    if _still_in_composer(xml, fmt=fmt):
        return False
    if st == "FEED":
        return True
    # Own profile only counts if a post is already visible (not empty after Profile-tab)
    if "edit profile" in tbl or "share profile" in tbl:
        if fmt == "story":
            # Profile "Your story" alone is NOT proof we just shared.
            print("[pub] on profile after story share — defer to verify_story_live")
            return False
        if re.search(r"[1-9]\d*\s*posts?\b", tbl):
            return True
        print("[pub] on profile but 0 posts — not a share success")
        return False
    if "suggested for you" in tbl and "edit profile" not in tbl:
        return True
    # Story: only toast / FEED above — never soft-match profile "Your story".
    return False


def _reel_publish_succeeded(xml=None):
    return _publish_succeeded(xml, fmt="reel")


def _is_true_reel_edit(xml=None):
    """Post-capture Edit video tools — not caption/share/cover picker.

    lunanest11 2026-08-04: true Edit video needs bottom Next → caption.
    mahnoormagic2 2026-08-05: Edit cover is NOT true edit — Done only.
    """
    xml = xml or dump()
    tb = text_block(xml)
    if _is_edit_cover_screen(xml):
        return False
    if any(p in tb for p in (
            "write a caption", "add a caption", "tag people", "add location",
            "also share", "hashtags", "poll prompt")):
        return False
    if _is_reel_clips_editor_tb(tb, xml):
        return True
    if any(p in tb for p in (
            "edit video", "double tap for tools", "voiceover", "add audio",
            "add sticker", "change video speed", "reel preview playing",
            "suggested audio", "continue without")):
        return True
    # Structural: post-capture clips chrome without caption copy
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if any(k in rid for k in (
                "clips_action_bar", "clips_post_capture", "clips_stacked_timeline",
                "post_capture_button")):
            return True
    return False


def _is_reel_clips_editor_tb(tb, xml=None):
    """Reel post-capture editor (Edit video + bottom Next) — NOT story editor."""
    tb = (tb or "").lower()
    if any(p in tb for p in (
            "edit video", "double tap for tools", "reel preview",
            "clips_stacked_timeline", "voiceover", "add audio")):
        return True
    if xml:
        for n in nodes(xml):
            rid = attr(n, "resource-id").lower()
            if "clips_post_capture" in rid or "clips_right_action_button" in rid:
                return True
    return False


def _is_also_share_sheet(tb, st=None):
    """True for post-share 'Also share' / story chooser — NOT caption 'Also share on…'.

    queenalidf764 / dreamyiqra165 2026-08-05 dumps: caption has share_button labeled
    Next PLUS text 'also share on…'. Old check `"also share" in tb` stole every
    publish round → publish_miss → POST_TIMEOUT (reel AND feed).
    """
    tb = (tb or "").lower()
    if st == "CREATE_CHOOSER":
        return True
    if "share to your story" in tb:
        return True
    # Caption toggle — never treat as sheet
    if "also share on" in tb or "write a caption" in tb or "add a caption" in tb:
        return False
    if "also share to" in tb or "also share this" in tb:
        return True
    # Bare "also share" only when not caption chrome
    if "also share" in tb and "tag people" not in tb and "add location" not in tb:
        return True
    return False


def _publish_build_id():
    """Printed once so logs prove Windows deployed the right ig_loop."""
    return _PUBLISH_BUILD


def _is_audio_picker_overlay(xml=None):
    """True only for the real music/track bottom sheet — not caption 'Add audio' row.

    songulertas826 2026-08-08: caption music suggestions + leftover nodes made
    detector flip true after Share → Back loop forever → POST_TIMEOUT.
    Require select-track text OR (2+ track rows AND sheet chrome).
    """
    xml = xml or dump()
    tb = text_block(xml)
    if "select track" in tb:
        return True
    track_n = 0
    has_dimmer = False
    has_music_search = False
    has_audio_bar = False
    has_bottom_sheet = False
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "track_container" in rid or "select_button_tap_target" in rid:
            track_n += 1
        if "audio_bar" in rid:
            has_audio_bar = True
        if "background_dimmer" in rid and attr(n, "clickable") == "true":
            has_dimmer = True
        if "row_search_edit_text" in rid:
            has_music_search = True
        if "layout_container_bottom_sheet" in rid:
            has_bottom_sheet = True
    sheet_chrome = has_dimmer or has_music_search or has_bottom_sheet
    # Real picker: several track rows + sheet chrome (not a single leftover node)
    if track_n >= 2 and sheet_chrome:
        return True
    if has_audio_bar and has_dimmer and (track_n >= 1 or "select track" in tb):
        return True
    return False


def _is_tag_people_sheet(xml=None):
    """Tag-people overlay (deryaozgur335): no Share CTA until Done."""
    xml = xml or dump()
    tb = text_block(xml)
    if "tap to tag people" in tb or "tap photo to tag" in tb:
        return True
    if "invite collaborators" in tb and "tag people" in tb:
        return True
    if "search for a user" in tb and "tag people" in tb:
        return True
    return False


def _dismiss_tag_people(xml=None):
    """Close tag-people sheet via Done (or Back)."""
    xml = xml or dump()
    if not _is_tag_people_sheet(xml):
        return False
    print("[pub] tag people sheet — dismiss before Share")
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        rid = attr(n, "resource-id").lower()
        x, y = bounds_center(n)
        if t == "done" or d == "done" or (y is not None and y < 400 and t == "done"):
            if tapn(n, "tag-people-done"):
                time.sleep(1.0)
                return True
        if "action_bar_button_action" in rid and t in ("done", "ok"):
            if tapn(n, "tag-people-done"):
                time.sleep(1.0)
                return True
    if tap_exact(xml, "Done", "Close", "Cancel", label="tag-people-done"):
        time.sleep(1.0)
        return True
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    print("[pub] KEYCODE_BACK tag people")
    time.sleep(1.0)
    return True


def _is_clips_nux_sheet(xml=None):
    """About Reels / clips NUX sheet covering caption Share.

    kub.ra6655: 'learn more about reels' / clips_nux_sheet ids.
    rumeysayildirim81 2026-08-10: title 'About Reels' + blue Share | Cancel —
    old detector missed it → POST_TIMEOUT.
    """
    xml = xml or dump()
    tb = text_block(xml).lower()
    if "learn more about reels" in tb:
        return True
    if "about reels" in tb:
        return True
    # Body copy unique to this sheet
    if "people can remix or use your reel" in tb or \
       ("original audio" in tb and "reels" in tb and "cancel" in tb):
        return True
    # First-reel empty-account sheet (Dalia/Nylah 2026-09-02: composer Share
    # did nothing until this NUX Share was tapped — Chasity hit it, they didn't).
    if "create your first reel" in tb and ("share" in tb or "cancel" in tb):
        return True
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "clips_nux_sheet" in rid:
            return True
    return False


def _dismiss_clips_nux(xml=None):
    """Clear About-Reels NUX.

    rumeysa 2026-08-10: sheet Share IS the publish confirm — tap Share first.
    Older learn-more sheets: Cancel then use caption Share.
    Returns True if the sheet was handled (Share or Cancel).
    Sets _dismiss_clips_nux.shared=True when NUX Share was tapped (likely published).
    """
    xml = xml or dump()
    _dismiss_clips_nux.shared = False
    if not _is_clips_nux_sheet(xml):
        return False
    print("[pub] About Reels / clips NUX — handle before caption Share")
    # 1) Prefer NUX Share (completes publish on About Reels confirm sheet)
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        clk = attr(n, "clickable") == "true"
        if "clips_nux_sheet_share" in rid:
            if tapn(n, "clips-nux-share"):
                print("[pub] About Reels — tapped NUX Share (publish)")
                _dismiss_clips_nux.shared = True
                time.sleep(2.5)
                return True
        if clk and (t == "share" or d == "share"):
            x, y = bounds_center(n)
            # Prefer bottom-sheet Share (not tiny labels)
            if y is not None and y >= 1600:
                if tapn(n, "clips-nux-share-text"):
                    print("[pub] About Reels — tapped Share text (publish)")
                    _dismiss_clips_nux.shared = True
                    time.sleep(2.5)
                    return True
    if tap_exact(xml, "Share", label="clips-nux-share-exact"):
        # Only if still on NUX (avoid random Share)
        print("[pub] About Reels — tapped Share exact")
        _dismiss_clips_nux.shared = True
        time.sleep(2.5)
        return True
    # 2) Cancel to clear sheet so caption Share becomes usable
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if "clips_nux_sheet_cancel" in rid or t == "cancel" or d == "cancel":
            if tapn(n, "clips-nux-cancel"):
                print("[pub] About Reels — Cancel (clear sheet)")
                time.sleep(1.0)
                return True
    if tap_exact(xml, "Cancel", "Not now", "Close", label="clips-nux-cancel"):
        print("[pub] About Reels — Cancel exact")
        time.sleep(1.0)
        return True
    return False


def _poll_reel_nux_after_share(fmt="reel", waits=3, pause=0.4):
    """After caption Share, peek for About Reels NUX — do not sit on New reel.

    If dump is still write-a-caption with no NUX sheet, return immediately.
    Old waits=10 + dump timeout=8 burned ~10min on Nylah while Next missed.
    """
    fmt = (fmt or "reel").lower()
    for i in range(max(1, int(waits))):
        xml = dump(timeout=5, attempts=1)
        if _publish_succeeded(xml, fmt=fmt) or not _still_in_composer(xml, fmt=fmt):
            print("[pub] left composer during NUX wait i=%d" % i)
            return True
        nux = _is_clips_nux_sheet(xml)
        if _is_caption_composer(xml) and not nux:
            print("[pub] still caption, no NUX sheet — skip wait")
            return False
        if nux:
            if _dismiss_clips_nux(xml) and getattr(_dismiss_clips_nux, "shared", False):
                time.sleep(1.2)
                xml_ok = dump(timeout=5, attempts=1)
                if _publish_succeeded(xml_ok, fmt=fmt) or \
                   not _still_in_composer(xml_ok, fmt=fmt):
                    print("[pub] SUCCESS via About Reels NUX poll i=%d" % i)
                    return True
                print("[pub] NUX Share tapped — still in composer i=%d" % i)
            else:
                print("[pub] NUX sheet seen but Share miss i=%d" % i)
        time.sleep(pause)
    print("[pub] NUX wait exhausted still in composer")
    return False


def _is_story_to_story_nux(xml=None, tb=None):
    """Case A: 'Introducing story-to-story sharing' + OK (obsessedsnipe 2026-08-17).

    Distinct from HUMAN_CHECK ('Confirm you're human' + Continue). Never treat
    that Continue as this OK.
    """
    tb = (tb if tb is not None else text_block(xml or dump())).lower()
    if "confirm you're human" in tb or "confirm you are human" in tb:
        return False
    if "story-to-story" in tb or "story to story" in tb:
        return True
    if "share your public stories" in tb and "ok" in tb:
        return True
    return False


def _dismiss_story_to_story_nux(xml=None, label="story-nux"):
    """Tap OK on story-to-story intro. Never 'View settings'."""
    xml = xml or dump()
    tb = text_block(xml)
    if not _is_story_to_story_nux(xml, tb):
        return False
    print("[pub] story-to-story NUX variant=A — tap OK (not View settings)")
    if tap_exact(xml, "OK", "Ok", label="%s-ok" % label):
        time.sleep(0.9)
        return True
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t == "ok" or d == "ok":
            if tapn(n, "%s-ok-node" % label):
                time.sleep(0.9)
                return True
    return False


def _is_giphy_overlay(xml=None, tb=None):
    """Caption GIF picker — Search GIPHY (ferventthrush 2026-08-17)."""
    tb = (tb if tb is not None else text_block(xml or dump())).lower()
    if "search giphy" in tb:
        return True
    if "giphy" in tb and "search" in tb and "write a caption" not in tb:
        return True
    return False


def _dismiss_giphy_overlay(xml=None, label="giphy"):
    """Close GIPHY with Back. Never tap a GIF as Share."""
    xml = xml or dump()
    tb = text_block(xml)
    if not _is_giphy_overlay(xml, tb):
        return False
    print("[pub] GIPHY overlay variant=A — Back (never tap a GIF)")
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.8)
    return True


def _story_text_tool_tb(tb):
    """Text-only Aa/sticker overlay heuristics (no dump, no asset-edit call)."""
    tb = (tb or "").lower()
    if any(p in tb for p in (
            "modern text style", "classic text style", "signature text style",
            "text color", "text emphasis", "stroke width tool",
            "click to view text fonts")):
        return True
    if "touch and hold to reposition" in tb or "use two fingers to rotate" in tb:
        return True
    return "done sticker" in tb and any(p in tb for p in (
        "text color", "text emphasis", "stroke width", "aa", "text tool"))


def _is_story_asset_edit_tb(tb):
    """Nomix asset-edit tray from text block only (no recursion)."""
    tb = (tb or "").lower()
    return (
        ("add more photos" in tb and any(p in tb for p in ("ratio", "overlay", "filter")))
        or ("selected photo" in tb and "overlay" in tb)
        or ("audio" in tb and "overlay" in tb and "ratio" in tb and "next" in tb)
    )


def _is_story_text_tool(xml=None, tb=None):
    """Story Aa / text compose OR sticker reposition overlay — not a tip sheet."""
    if tb is None:
        xml = xml or dump()
        tb = text_block(xml).lower()
    else:
        tb = tb.lower()
    # Nomix asset-edit tray (Audio/Text/Overlay/Ratio) also shows "Done" — not Aa.
    if _is_story_asset_edit_tb(tb):
        return False
    return _story_text_tool_tb(tb)


def _is_story_editor_chrome(xml=None, tb=None):
    """Story editor (not feed caption, not profile Your story, not camera shutter)."""
    xml = xml or dump()
    tb = (tb if tb is not None else text_block(xml)).lower()
    if _is_true_reel_edit(xml) or _is_reel_clips_editor_tb(tb, xml):
        return False
    if "edit profile" in tb or "write a caption" in tb or "add a caption" in tb:
        return False
    if _is_story_text_tool(None, tb):
        return False
    if any(p in tb for p in ("shutter", "story settings", "boomerang",
                               "hold to record", "recents", "photo thumbnail",
                               "unselected photo")):
        return False
    if _is_story_to_story_nux(None, tb):
        return True
    tools = any(p in tb for p in (
        "stickers", "sticker", "add sticker", "add music", "create mode",
        "download", "search giphy", "draw", "doodle", "text tool"))
    share = any(p in tb for p in (
        "add to your story", "share to your story", "your story", "share to story"))
    if tools and share:
        return True
    # Post-pick canvas often exposes Stickers + Done before Share label settles
    if tools and ("done" in tb or "next" in tb) and "gallery" not in tb:
        return True
    return bool(share and ("done" in tb or "stickers" in tb))


def _is_story_asset_edit(xml=None, tb=None):
    """Nomix story edit tray: Audio/Text/Overlay/Filter/Ratio + Next (not Aa overlay)."""
    if tb is None:
        xml = xml or dump()
        tb = text_block(xml).lower()
    else:
        tb = tb.lower()
    if _story_text_tool_tb(tb):
        return False
    return _is_story_asset_edit_tb(tb)


def _story_has_stickers_affordance(xml=None):
    """True if Stickers / Overlay / Your story toolbar is on story canvas (link-ready)."""
    xml = xml or dump()
    if _is_story_text_tool(xml):
        return False
    if _is_story_asset_edit(xml):
        return True
    for n in nodes(xml):
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        rid = attr(n, "resource-id").lower()
        if d in ("stickers", "add sticker", "sticker tray", "overlay") or \
           t in ("stickers", "sticker", "overlay"):
            return True
        if "sticker" in rid and "button" in rid:
            return True
        if d in ("your story", "add to your story", "share to story"):
            return True
    return _is_story_editor_chrome(xml)


def _caption_ok_button(xml=None):
    """Top-bar OK while caption field is focused (selinkorkmaz2981 landscape dump).

    Share/footer stays visible but publish is a no-op until OK exits edit mode.
    """
    xml = xml or dump()
    best = None
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        x, y = bounds_center(n)
        if x is None or y is None or y > 400:
            continue
        if t == "ok" or d == "ok" or (
                "next_button" in rid and t in ("ok", "done", "")):
            if t in ("ok", "done") or d in ("ok", "done"):
                if best is None or x > best[0]:
                    best = (x, y, n)
    return best[2] if best else None


def _caption_keyboard_up(xml=None):
    """Gboard/AdbIME covering footer Share (Nylah fail shot 2026-09-02 17:51)."""
    xml = xml or dump()
    for n in nodes(xml):
        cls = attr(n, "class")
        if "EditText" in cls and attr(n, "focused").lower() == "true":
            return True
        pkg = (attr(n, "package") or "").lower()
        if any(p in pkg for p in ("inputmethod", "adbkeyboard", "latin", "samsung.android.honeyboard")):
            _x, y = bounds_center(n)
            if y and y > 1500:
                return True
    return False


def _dismiss_caption_keyboard(xml=None):
    """OK unfocus + IME off + tap dead form chrome. Share is under the keyboard."""
    xml = xml or dump()
    ok_n = _caption_ok_button(xml)
    if ok_n is not None:
        tapn(ok_n, "caption-ok-unfocus")
        time.sleep(0.45)
    else:
        sw, sh = _screen_wh(xml)
        tap(int(sw * 0.93), int(sh * 0.06))
        print("[pub] caption OK coord (unfocus)")
        time.sleep(0.45)
    _adb_ime_off()
    sw, sh = _screen_wh(xml)
    # Left of form copy, not Edit cover / caption field / footer
    tap(int(sw * 0.07), int(sh * 0.40))
    time.sleep(0.4)


def _tap_share_chip(xml=None):
    """Blue Share/Next after keyboard is gone — not Gboard Enter @~2644."""
    xml = xml or dump()
    if _caption_keyboard_up(xml):
        print("[pub] skip Share chip — keyboard still up")
        return False
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    floor = int(sh * 0.58)
    ceil = int(sh * 0.98)  # Note8 Share @~2762; share chip ceil 0.98
    right_nav = int(sw * 0.95)  # Share sits right
    best = None
    # Prefer real share_button / share_footer_button (Nylah dump 2026-09-04)
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "container" in rid or "save_draft" in rid or "clips_nux" in rid:
            continue
        if "share_button" not in rid and "share_footer_button" not in rid:
            continue
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < floor or y > ceil:
            continue
        tt = attr(n, "text").strip().lower()
        dd = attr(n, "content-desc").strip().lower()
        if "also share" in tt or "also share" in dd:
            continue
        print("[pub] Share chip via rid=%s @ %d,%d" % (rid.split("/")[-1], x, y))
        return tapn_cta(n, "share-chip-rid")
    for n in nodes(xml):
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        rid = attr(n, "resource-id").lower()
        if "clips_right_action" in rid or "cam_dest_" in rid or "save_draft" in rid:
            continue
        labeled = t in ("share", "next", "post") or d in ("share", "next", "post")
        footer = "share_footer_button" in rid
        if not labeled and not footer:
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < floor or y > ceil or x >= right_nav:
            continue
        pri = 0
        if t == "share" or d == "share" or footer:
            pri = 0
        elif t == "next" or d == "next":
            pri = 1
        else:
            pri = 2
        w, _h = bounds_wh(n)
        if best is None or (pri, -(w or 0)) < (best[0], -best[1]):
            best = (pri, w or 0, n)
    if not best:
        return False
    return tapn_cta(best[2], "ok-then-share:chip")


def _vision_share_button():
    """Screencap Share/Next — use grok_key even if IG_VISION=0 (prove was blind)."""
    try:
        import ig_vision as vis
    except Exception:
        return False
    if not vis.api_key():
        return False
    old = os.environ.get("IG_VISION")
    os.environ["IG_VISION"] = "1"
    try:
        return _vision_tap(
            "Share",
            "Bottom blue Share or Next on the New reel caption screen. "
            "Not ADB Keyboard, not Profile tab, not OK.",
            tag="cap-share",
        )
    finally:
        if old is None:
            os.environ.pop("IG_VISION", None)
        else:
            os.environ["IG_VISION"] = old


def _note8_ok_then_share():
    """Hard coords — live dump Nylah 2026-09-04 Share screen (1440x2960).

    Keyboard covers Share. Must OK @1347,179 first, wait, then Share @1068,2738
    (upper third of share_button 744,2696–1392,2828). Never tap Share while IME up.
    """
    sw, sh = _wm_size()
    if sw < 1400 or sh < 2800:
        return False
    tap(1347, 179)
    print("[pub] Note8 OK @ 1347,179 (unfocus)")
    time.sleep(0.75)
    _adb_ime_off()
    time.sleep(0.4)
    tap(1068, 2762)
    print("[pub] Note8 Share @ 1068,2762")
    time.sleep(0.3)
    return True


def _caption_ok_then_share(xml=None):
    """OK to close keyboard, then Share. Share is under Gboard — never tap it first."""
    sw, sh = _wm_size()
    # Note 8 live coords (Nylah dump 2026-09-04): OK 1347,179 → Share 1068,2738
    if sw >= 1400 and sh >= 2800:
        _note8_ok_then_share()
        time.sleep(1.2)
        xml2 = dump(timeout=4, attempts=1)
        if xml2 and _caption_keyboard_up(xml2):
            print("[pub] keyboard still up - OK+Share again")
            tap(1347, 179)
            time.sleep(0.7)
            _adb_ime_off()
            time.sleep(0.35)
            tap(1068, 2762)
            print("[pub] Note8 Share 2nd @ 1068,2762")
            time.sleep(0.8)
            xml2 = dump(timeout=4, attempts=1)
        # Hard coords often miss; never claim True while caption still up.
        if xml2 and (_still_on_share_caption(xml2) or _is_caption_composer(xml2)
                     or _still_in_composer(xml2, fmt="reel")):
            print("[pub] Note8 still caption after coords - Share chip/rid")
            return bool(_tap_share_chip(xml2) or _tap_share_button_any(xml2, label="note8-share-any"))
        return True
    xml = xml or dump()
    for attempt in range(2):
        if _section_expired(need=2):
            print("[pub] composer budget — stop OK+Share")
            return False
        if _caption_keyboard_up(xml) or _caption_ok_button(xml) is not None:
            _dismiss_caption_keyboard(xml)
            xml = dump()
        if _caption_keyboard_up(xml):
            print("[pub] keyboard still up try=%d — OK again" % (attempt + 1))
            continue
        if _tap_share_chip(xml):
            return True
        if _section_left() >= 12 and _vision_share_button():
            return True
        xml = dump()
    print("[pub] Share not visible after unfocus — not tapping keyboard")
    return False


def _wm_size():
    """Physical screen size via wm — never uiautomator (safe during Reels playback)."""
    out = adb("shell", "wm", "size") or ""
    m = re.search(r"(\d+)\s*x\s*(\d+)", out.replace("\n", " "))
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1440, 2960


def _tap_ig_home_no_dump():
    """Leave Reels/Search without uiautomator. Home = leftmost bottom tab (~10%, 95%)."""
    sw, sh = _wm_size()
    x, y = int(sw * 0.10), int(sh * 0.95)
    tap(x, y)
    print("[warmup] Home (no dump) @ %d,%d" % (x, y))


def _screen_wh(xml=None):
    """Return (w, h) from root/first frame bounds; Note 8 portrait default."""
    xml = xml or dump()
    for n in nodes(xml):
        b = attr(n, "bounds")
        if not b:
            continue
        m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b)
        if not m:
            continue
        l, t0, r, b0 = map(int, m.groups())
        w, h = r - l, b0 - t0
        if w >= 720 and h >= 720:
            return w, h
    return 1440, 2960


def _dismiss_audio_picker(max_tries=5):
    """Close music/track bottom sheet (nergis: Back node tap is a no-op).

    Order: hide IME → tap dimmer (above sheet) → swipe-down → KEYCODE_BACK.
    Never only spam composer Back — that burned 8 publish rounds.
    """
    if not _is_audio_picker_overlay():
        return False
    print("[pub] audio/track picker open — dismiss before Share")
    _hide_ime()
    sw, sh = _screen_wh()
    for i in range(max_tries):
        xml = dump()
        if not _is_audio_picker_overlay(xml):
            print("[pub] audio picker cleared")
            return True

        # 1) Tap background dimmer in the clear band above the track list
        dimmer = None
        for n in nodes(xml):
            rid = attr(n, "resource-id").lower()
            if "background_dimmer" in rid and attr(n, "clickable") == "true":
                dimmer = n
                break
        if dimmer is not None:
            # Prefer upper third of screen (sheet usually occupies bottom)
            tap(sw // 2, max(120, sh // 5))
            print("[pub] audio dimmer tap @ %d,%d" % (sw // 2, max(120, sh // 5)))
            time.sleep(0.9)
            if not _is_audio_picker_overlay():
                print("[pub] audio picker cleared (dimmer)")
                return True

        # 2) Swipe sheet down
        if i <= 2:
            y1 = int(sh * 0.72)
            y0 = int(sh * 0.25)
            adb("shell", "input", "swipe", str(sw // 2), str(y1),
                str(sw // 2), str(y0), "350")
            print("[pub] audio sheet swipe-down")
            time.sleep(0.9)
            if not _is_audio_picker_overlay():
                print("[pub] audio picker cleared (swipe)")
                return True

        # 3) Hardware back (node Back often no-ops on this sheet)
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        print("[pub] KEYCODE_BACK audio picker try=%d" % (i + 1))
        time.sleep(0.9)
        if not _is_audio_picker_overlay():
            print("[pub] audio picker cleared (back)")
            return True

        # 4) Last: explicit close/cancel on sheet chrome
        xml = dump()
        if tap_exact(xml, "Close", "Cancel", "Done", "Not now",
                     label="audio-sheet-close"):
            time.sleep(0.8)
            if not _is_audio_picker_overlay():
                print("[pub] audio picker cleared (close)")
                return True

    print("[pub] WARN audio picker still open after dismiss tries")
    return not _is_audio_picker_overlay()


def _is_caption_composer(xml=None):
    """True when on caption/share chrome — even if detect_state says EDIT_SCREEN.

    ernvra26 / ingrameliel / aarya78415 2026-08-04: after typing, pre_share reported
    EDIT_SCREEN then pub-edit-next tapped TOP Next (y~179) → share_timeout.
    """
    xml = xml or dump()
    if _is_true_reel_edit(xml):
        return False
    st = detect_state(xml)
    if st == "CAPTION_SCREEN":
        return True
    if in_caption_screen(xml) or _reel_at_caption(xml):
        return True
    tb = text_block(xml)
    if any(p in tb for p in (
            "write a caption", "add a caption", "caption…", "caption...",
            "tag people", "add location", "also share on", "also share to",
            "also share", "hashtags", "poll prompt")):
        return True
    has_share_btn = False
    has_bottom_next = False
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        x, y = bounds_center(n)
        if "share_button" in rid or "share_footer_button" in rid:
            has_share_btn = True
        if y and y >= 1600 and (t == "next" or d == "next" or "clips_right_action" in rid
                                or "share_button" in rid or "share_footer" in rid):
            has_bottom_next = True
    if has_share_btn and "recents" not in tb:
        return True
    if "new reel" in tb and has_bottom_next and "recents" not in tb:
        return True
    return False


def _still_on_share_caption(xml=None):
    """True when New reel/post caption is still the page (Next/Share still the job)."""
    xml = xml or dump()
    tb = text_block(xml)
    if _is_draft_exit_sheet(tb):
        return False
    if any(p in tb for p in ("recents", "select multiple", "photo thumbnail",
                               "video thumbnail")):
        return False
    return _is_caption_composer(xml)


def _tap_top_left_back(xml, label="caption-face-back"):
    """Top-left Back / up-arrow only — never bottom nav."""
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    cands = []
    for n in nodes(xml):
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        if y > 320 or x > int(sw * 0.28):
            continue
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        rid = attr(n, "resource-id").lower()
        clk = attr(n, "clickable") == "true"
        if d in ("back", "close", "navigate up", "navigate back") or \
           t in ("back", "close"):
            cands.append((0, n))
        elif "back" in rid and ("action_bar" in rid or "navigation" in rid):
            cands.append((0, n))
        elif clk and x < 180 and y < 240:
            cands.append((1, n))
    cands.sort(key=lambda r: r[0])
    if cands:
        return tapn(cands[0][1], label)
    _adb_tap_xy(72, 156, label + "-coord")
    return True


def _dismiss_caption_face_overlay_once(xml=None):
    """Case B only (WakanaArakawa35 2026-08-12): transparent face over caption.

    Caller must have already proven Next/Share tap did not advance.
    One top-left Back, then must still be on caption. Never the default path.
    """
    xml = xml or dump()
    if not _still_on_share_caption(xml):
        return False
    print("[pub] case=B_caption_face_overlay — Back once (then Next); not default")
    _tap_top_left_back(xml, label="caption-face-back")
    time.sleep(1.0)
    xml2 = dump()
    tb2 = text_block(xml2)
    if _is_draft_exit_sheet(tb2):
        print("[pub] face-overlay Back hit draft sheet — Keep editing")
        tap_exact(xml2, "Keep editing", "Keep Editing", label="face-overlay-keep")
        time.sleep(0.8)
        xml2 = dump()
    if _still_on_share_caption(xml2):
        print("[pub] face overlay cleared — still on caption")
        return True
    print("[pub] face-overlay Back left caption st=%s — will not Back again"
          % detect_state(xml2))
    return False


def _find_publish_nodes(xml, fmt="reel", allow_top_share=False):
    """Publish CTAs for feed/reel caption.

    Dump lessons 2026-08-07:
      yasemin: id/share_button desc=Next (not share_button_container)
      cansu/selin: id/share_footer_button desc=Share — primary on newer IG
      cansu: music overlay blocks Share until dismissed
      Never: save_draft, non-clickable text 'Share', wide inert containers
    kadriye/kub.ra 2026-08-10: blue bottom Share often has clickable=false on
    the TextView — still tap its bounds (parent receives the hit).
    """
    fmt = (fmt or "reel").lower()
    _sw, sh = _screen_wh(xml)
    bottom_y = int(sh * 0.68)
    out = []
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        clk = attr(n, "clickable") == "true"
        en = attr(n, "enabled").lower() in ("", "true")
        if not en:
            continue
        if "save_draft" in rid or d == "save draft" or t == "save draft":
            continue
        if "giphy" in rid or "giphy" in t or "giphy" in d or t == "gif" or d == "gif":
            continue
        if "track_container" in rid or "audio_bar" in rid or "play_pause" in rid:
            continue
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        w, h = bounds_wh(n)

        # Newest IG feed CTA — tap even if clickable=false on wrapper
        if "share_footer_button" in rid:
            pri = 0
            if t in ("share", "next", "post") or d in ("share", "next", "post"):
                pri = 0
            if not clk:
                pri = 1
            out.append((pri, -y, -x, w, n, "share_footer"))
            continue

        # About Reels NUX Share — primary publish when sheet is up (rumeysa)
        if "clips_nux_sheet_share" in rid:
            if not clk and y < int(sh * 0.55):
                continue
            out.append((0, -y, -x, w, n, "clips_nux_share"))
            continue

        # Classic share_button (Next) — never container
        if "share_button" in rid or "share_sheet_button" in rid or \
           "post_capture_button_share" in rid:
            if "container" in rid:
                continue
            if "clips_nux" in rid:
                continue  # handled above / Cancel path
            if not clk and y < bottom_y:
                continue
            if y < 1600 and fmt in ("reel", "feed") and not allow_top_share:
                continue
            labeled = t in ("share", "next", "post") or d in ("share", "next", "post")
            if y > int(sh * 0.93) and not labeled:
                continue  # unlabeled IME / sysbar — labeled Next lives ~2762
            pri = 0
            if rid.endswith("/share_button") or rid.endswith(":id/share_button"):
                pri = 0
            if t in ("share", "next", "post") or d in ("share", "next", "post"):
                pri = 0
            elif y < 1600:
                pri = 2
            # Tiny unlabeled right-edge icon can be inert (nazli 2026-08-12).
            # Never demote labeled Next/Share — that's the real caption CTA
            # @~1068,2762 (2026-08-13: pri=3 let top_ok @179 win).
            if fmt == "reel" and x and _sw and x > int(_sw * 0.80) and y >= bottom_y:
                if t not in ("share", "next", "post") and d not in ("share", "next", "post"):
                    if (w or 0) < 220:
                        pri = max(pri, 3)
            if not clk:
                pri = max(pri, 1)
            out.append((pri, -y, -x, w, n, "share_button"))
            continue

        # Top bar Share / Next on caption — never OK (OK is _caption_ok_button only).
        # After caption OK, caller should pass allow_top_share=False so bottom Next wins.
        if allow_top_share and y < 400 and clk:
            if t in ("ok", "done") or d in ("ok", "done"):
                continue
            if "next_button" in rid or t in ("share", "next", "post") or \
               d in ("share", "next", "post"):
                if t == "open settings" or d == "open settings":
                    continue
                out.append((1, -y, -x, w, n, t or d or "top_cta"))
                continue

        if "clips_right_action" in rid or rid.endswith(":id/primary_button"):
            if t == "open settings" or d == "open settings":
                continue
            if y < 1600:
                continue
            if not clk:
                continue
            out.append((2, -y, -x, w, n, "right_action"))
            continue

        if fmt == "story":
            if t in ("your story", "add to your story", "share to story", "share") or \
               d in ("your story", "add to your story", "share to story", "share"):
                out.append((1 if clk else 2, -y, -x, w, n, t or d))
                continue
            # Never use top Done (text/sticker tools) as story Share.
            if t in ("done",) or d in ("done",):
                continue
            # Never treat create mode tabs as publish (Post/Story/Reel strip).
            if t in ("post", "story", "reel", "reels") or d in ("post", "story", "reel", "reels"):
                if "cam_dest" in rid or "creation_dest" in rid or "mode" in rid:
                    continue
                if y >= int(sh * 0.90) and (w or 0) < int(_sw * 0.45):
                    continue
            # Edit-step Next (add more / ratio) is not final share — caller advances.
            if t == "next" or d == "next" or "next_button" in rid:
                continue

        # Text/desc Share — clickable preferred; non-clickable OK in bottom band
        if t in ("share", "post", "publish", "share reel", "share now", "share post") or \
           d in ("share", "post", "publish", "share reel", "share now", "share post"):
            if "also share" in t or "also share" in d:
                continue
            # Gallery mode tab "Post" is not Share (hayes story 2026-08-27).
            if (t in ("post", "story", "reel", "reels") or d in ("post", "story", "reel", "reels")) and \
               ("cam_dest" in rid or "creation_dest" in rid or
                (y >= int(sh * 0.90) and (w or 0) < int(_sw * 0.45))):
                continue
            if y < 1600 and fmt in ("reel", "feed") and not allow_top_share:
                continue
            if not clk and y < bottom_y:
                continue
            pri = 1 if y >= 1200 else 3
            if not clk:
                pri = 2
            # Prefer wide bottom CTA (full-width Share bar)
            if w and w >= int(_sw * 0.5) and y >= bottom_y:
                pri = 0
            out.append((pri, -y, -x, w or 0, n, t or d or "share"))
            continue

        if clk and (t == "next" or d == "next" or "next_button" in rid) and y >= 1600:
            if y > int(sh * 0.93):
                continue  # IME / nav strip
            out.append((0, -y, -x, w, n, "next"))
    out.sort(key=lambda r: (r[0], r[1], r[2], r[3]))
    return out


def _find_reel_share_nodes(xml):
    return _find_publish_nodes(xml, fmt="reel")


def _tap_publish_cta(xml, fmt="reel", label="pub", allow_top_share=False, skip_xy=None):
    """Tap best publish CTA. skip_xy = set of rounded (x,y) already tried dead.

    Sets _tap_publish_cta.last_xy on success. Returns bool.
    """
    skip_xy = skip_xy or set()
    _tap_publish_cta.last_xy = None
    cands = _find_publish_nodes(xml, fmt=fmt, allow_top_share=allow_top_share)
    for _pri, _nx, _ny, _w, n, kind in cands:
        x, y = bounds_center(n)
        if x is None:
            continue
        key = (int(x) // 10 * 10, int(y) // 10 * 10)
        if key in skip_xy:
            continue
        if tapn_cta(n, "%s:%s" % (label, kind)):
            _tap_publish_cta.last_xy = key
            print("[pub] CTA pick %s @ %d,%d (skip=%d left=%d)"
                  % (kind, x, y, len(skip_xy), len(cands)))
            return True
    return False


def _tap_share_button_any(xml=None, label="share-any", skip_xy=None):
    """Tap share_footer_button / share_button / Share at any Y — never bare top Next.

    Sets _tap_share_button_any.last_xy on success.
    """
    xml = xml or dump()
    skip_xy = skip_xy or set()
    _tap_share_button_any.last_xy = None
    _sw, sh = _screen_wh(xml)
    bottom_y = int(sh * 0.55)
    best = None
    for n in nodes(xml):
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if "giphy" in rid or "giphy" in t or "giphy" in d or t == "gif" or d == "gif":
            continue
        clk = attr(n, "clickable") == "true"
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        key = (int(x) // 10 * 10, int(y) // 10 * 10)
        if key in skip_xy:
            continue
        w, _h = bounds_wh(n)
        kind = None
        pri = 9
        if "share_footer_button" in rid:
            kind = "share_footer"
            pri = 0 if clk else 1
        elif "clips_nux_sheet_share" in rid:
            # About Reels confirm Share = publish (rumeysa)
            kind = "clips_nux_share"
            pri = 0
        elif "share_button" in rid or "share_sheet_button" in rid:
            if "container" in rid or "save_draft" in rid or "clips_nux" in rid:
                continue
            if not clk and y < bottom_y:
                continue
            kind = "share_button"
            pri = 0 if y >= bottom_y else 2
            if rid.endswith("/share_button") or rid.endswith(":id/share_button"):
                pri = max(0, pri - 1)
            if d in ("next", "share", "post") or t in ("next", "share", "post"):
                pri = max(0, pri - 2)
        elif "save_draft" in rid or d == "save draft":
            continue
        elif t in ("share", "share reel", "share now", "share post", "post") or \
             d in ("share", "share reel", "share now", "share post", "post"):
            if "also share" in t or "also share" in d:
                continue
            if not clk and y < bottom_y:
                continue
            # Prefer real footer/button ids; wide bottom Share text is strong
            kind = "share"
            pri = 3 if y >= bottom_y else 5
            if w and w >= int(_sw * 0.45) and y >= bottom_y:
                pri = 0
        elif clk and y >= bottom_y and (t == "next" or d == "next" or "clips_right_action" in rid
                                or "next_button" in rid):
            if t == "ok" or d == "ok":
                continue
            kind = "bottom_next"
            pri = 2
        if kind is None:
            continue
        score = (pri, -y, -x, -(w or 0))
        if best is None or score < best[0]:
            best = (score, n, kind, key, x, y)
    if not best:
        return False
    if tapn_cta(best[1], "%s:%s" % (label, best[2])):
        _tap_share_button_any.last_xy = best[3]
        return True
    return False


def _tap_bottom_share_or_next(xml=None, label="bottom-share", skip_xy=None):
    """Last-resort: Share/Next/Post in the bottom ~45% (works portrait + landscape)."""
    xml = xml or dump()
    skip_xy = skip_xy or set()
    _sw, sh = _screen_wh(xml)
    y_min = int(sh * 0.55)
    best = None
    for n in nodes(xml):
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        clk = attr(n, "clickable") == "true"
        x, y = bounds_center(n)
        if x is None or y is None or y < y_min:
            continue
        key = (int(x) // 10 * 10, int(y) // 10 * 10)
        if key in skip_xy:
            continue
        w, _h = bounds_wh(n)
        if "container" in rid and "share_footer" not in rid:
            if "share_button" in rid or "footer_button" in rid:
                continue
        kind = None
        if "share_footer_button" in rid:
            kind = "share_footer"
        elif "share_button" in rid or \
             "post_capture_button_share" in rid:
            if "container" in rid:
                continue
            if not clk:
                continue
            kind = "rid"
        elif t in ("share", "next", "post", "share reel", "share now") or \
             d in ("share", "next", "post", "share reel", "share now"):
            if "also share" in t or "also share" in d:
                continue
            kind = t or d
        elif clk and "next_button" in rid and t not in ("ok",) and d not in ("ok",):
            kind = "next_rid"
        if not kind:
            continue
        pri = 0 if ("share_footer" in rid or "share_button" in rid or kind == "rid") else 1
        if not clk:
            pri += 1
        if w and w >= int(_sw * 0.45) and kind in ("share", "post", "share_footer"):
            pri = 0
        score = (pri, -y, -x)
        if best is None or score < best[0]:
            best = (score, n, kind, key)
    if not best:
        return False
    if tapn_cta(best[1], "%s:%s" % (label, best[2])):
        _tap_bottom_share_or_next.last_xy = best[3]
        return True
    return False


def _tap_feed_share_coords(xml=None):
    """Blind bottom Share for New post — never Profile tab (1287,2819)."""
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    # Center-bottom Share bar only. 1287,2819 is Profile on Message-center nav
    # (nila_y6790 / aylinsevim82 2026-08-11 false SUCCESS).
    coords = (
        (sw // 2, int(sh * 0.93)),
        (sw // 2, int(sh * 0.90)),
        (sw // 2, int(sh * 0.88)),
        (int(sw * 0.62), int(sh * 0.93)),
        (int(sw * 0.50), int(sh * 0.86)),
    )
    for x, y in coords:
        tap(x, y)
        print("[pub] feed share coord @ %d,%d" % (x, y))
        time.sleep(2.5)
        xml2 = dump()
        if _is_media_preview_overlay(xml2):
            _dismiss_media_preview(xml2)
            continue
        if detect_state(xml2) in ("HUMAN_CHECK", "CAPTCHA"):
            print("[pub] human/captcha after share coord — stop")
            return False
        if _publish_succeeded(xml2, fmt="feed"):
            return True
        if not _still_in_composer(xml2, fmt="feed"):
            st2 = detect_state(xml2)
            if st2 == "FEED":
                return True
            # Profile with 0 posts = missed Share
            continue
        if _is_audio_picker_overlay(xml2):
            _dismiss_audio_picker()
            time.sleep(0.5)
        if _is_tag_people_sheet(xml2):
            _dismiss_tag_people(xml2)
            time.sleep(0.5)
    return False


def _tap_bottom_share_coords(fmt="reel"):
    """Blind bottom Share/Next — never Profile tab (1287,2819)."""
    fmt = (fmt or "reel").lower()
    coords = []
    if fmt in ("reel", "feed"):
        # Center share_footer (handeisik346 @720,2822) + reel Next @~1068,2762.
        # 1287,2819 / 1320,2800 / 1360,2820 are Profile tab (nila/aylin 2026-08-11).
        coords.extend((
            (720, 2822), (720, 2798), (720, 2760),
            (1068, 2762), (1100, 2760), (1000, 2780),
        ))
    for x, y in coords:
        tap(x, y)
        print("[pub] bottom share coord @ %d,%d" % (x, y))
        time.sleep(2.8)
        xml = dump()
        if _publish_succeeded(xml, fmt=fmt) or not _still_in_composer(xml, fmt=fmt):
            return True
    return False


def _tap_edit_advance_next(xml=None, label="pub-edit-next"):
    """Advance edit→caption via BOTTOM Next (clips_right), never top action-bar Next."""
    xml = xml or dump()
    if _is_caption_composer(xml) and not _is_true_reel_edit(xml):
        return False
    tb0 = text_block(xml)
    if "create a sticker" in tb0 or "into a sticker" in tb0:
        print("[pub] sticker NUX before edit Next - dismiss")
        _dismiss_reel_create_tip(xml, label="%s-sticker" % label)
        xml = dump()
    best = None
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if not (t == "next" or d == "next" or "next_button" in rid or
                "clips_right_action" in rid):
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < 1600:
            continue  # skip top Next (caption false-edit)
        if best is None or x > best[0]:
            best = (x, y, n)
    if not best:
        return False
    return tapn(best[2], label)


def _tap_reel_publish_cta(xml, label="reel-pub"):
    return _tap_publish_cta(xml, fmt="reel", label=label)


def _dismiss_composer_chrome(xml=None, fmt="reel"):
    """Clear About / tip / top OK / Edit cover that blocks share_button."""
    xml = xml or dump()
    fmt = (fmt or "reel").lower()
    if _dismiss_giphy_overlay(xml):
        return True
    if _dismiss_story_to_story_nux(xml):
        if fmt in ("feed", "carousel", "reel"):
            print("[pub] feed job after story NUX — Back (leave story editor)")
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.8)
        return True
    if fmt in ("feed", "carousel", "reel") and _is_story_editor_chrome(xml):
        if fmt == "reel" and _is_true_reel_edit(xml):
            return False
        print("[pub] feed job on story editor — Back")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.8)
        return True
    if fmt == "reel" and _dismiss_edit_cover(xml, label="composer-cover"):
        return True
    tb = text_block(xml)
    # About Reels confirm sheet — Share publishes; Cancel clears (rumeysa 2026-08-10)
    if _is_clips_nux_sheet(xml) or "about reels" in tb.lower():
        if _dismiss_clips_nux(xml):
            return True
    if "about reels" in tb.lower() or ("your reel will be shared" in tb.lower() and "got it" in tb.lower()):
        if tap_exact(xml, "Got it", "OK", "Continue", "Done", label="about-reels"):
            time.sleep(1.0)
            return True
    # Caption already ready with Share — top OK is not a blocker (yaseminturan1574)
    at_cap = ("write a caption" in tb or "add a caption" in tb)
    has_share = any(
        (("share_button" in attr(n, "resource-id").lower()
          and "container" not in attr(n, "resource-id").lower())
         or "share_footer_button" in attr(n, "resource-id").lower()
         or attr(n, "text").strip().lower() in ("share", "post"))
        for n in nodes(xml)
    )
    if not (at_cap and has_share):
        for n in nodes(xml):
            rid = attr(n, "resource-id").lower()
            t = attr(n, "text").strip().lower()
            d = attr(n, "content-desc").strip().lower()
            if "action_bar_button_text" in rid and (t == "ok" or d == "ok"):
                if tapn(n, "composer-top-ok"):
                    time.sleep(1.0)
                    return True
    if _is_preview_size_tip(xml, tb):
        return dismiss_preview_size_tip(xml, label="composer-preview")
    if fmt == "reel" and _dismiss_reel_overlays(xml):
        return True
    if detect_state(xml) in ("TIP_SHEET", "ONBOARD_CARD") or _is_reel_create_tip_tb(tb):
        return dismiss_tip_sheet(xml, label="composer-tip", allow_next=False)
    return False


def _dismiss_reel_composer_chrome(xml=None):
    return _dismiss_composer_chrome(xml, fmt="reel")


def _is_draft_exit_sheet(tb=""):
    """True for IG draft-exit / start-new sheets that block create."""
    tb = (tb or "").lower()
    if "keep editing your draft" in tb or "this draft will be saved" in tb:
        return True
    if "save draft?" in tb:
        return True
    if ("start new video" in tb or "start new post" in tb or
            "start a new video" in tb or "start a new post" in tb) and "draft" in tb:
        return True
    if "lose this draft" in tb or "if you go back now" in tb:
        return True
    # Classic sheet: Start over + Keep editing / Save draft together
    if "start over" in tb and ("keep editing" in tb or "save draft" in tb):
        return True
    return False


def _dismiss_save_draft_sheet(xml=None, label="draft-sheet"):
    """Exit IG draft sheets — never Keep editing / Save draft.

    Variants:
      A) Save draft? → Start over | Save draft | Keep editing  (Daniella 2026-08-04)
      B) Keep editing your draft? → Keep editing | Start new video
         (kadriyeaydin8638 feed POST_TIMEOUT 2026-08-10 — REEL leftover draft)
    Prefer discard / start-new CTAs only.
    """
    xml = xml or dump()
    tb = text_block(xml)
    # Prefer exit draft / start fresh (order matters — never Keep editing)
    if tap_exact(
        xml,
        "Start new video",
        "Start new post",
        "Start a new video",
        "Start a new post",
        "Start over",
        "Discard",
        "Discard draft",
        "Delete draft",
        "Don't save",
        "Discard changes",
        "Delete",
        label="%s-start-new" % label,
    ):
        time.sleep(1.4)
        return True
    # Visible row text even if tap_exact missed (action_sheet_row)
    prefer = (
        "start new video", "start new post", "start a new video", "start a new post",
        "start over", "discard", "discard draft", "don't save", "delete draft",
        "discard changes",
    )
    ban = ("keep editing", "save draft", "keep editing your draft")
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        if not t or t in ban or t.startswith("keep editing"):
            continue
        if t in prefer:
            if tapn(n, "%s-row-%s" % (label, t[:16])):
                time.sleep(1.4)
                return True
    # Cancel (X) on edit/caption action bar — brings up sheet or leaves
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").strip().lower()
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        if y < 400 and ("cancel_button" in rid or d == "cancel" or d == "close"):
            if tapn(n, "%s-cancel" % label):
                time.sleep(1.0)
                xml2 = dump()
                if tap_exact(
                    xml2,
                    "Start new video", "Start new post", "Start over",
                    "Discard", "Discard draft", "Don't save",
                    label="%s-after-cancel" % label,
                ):
                    time.sleep(1.3)
                return True
    if _is_draft_exit_sheet(tb):
        print("[post] draft sheet visible but no Start-new/Start-over tap worked")
    return False


def _clear_create_draft_gate(dest="feed", label="draft-gate"):
    """
    If a draft-exit sheet is up, dismiss it then force the intended create dest.
    Returns True if dismiss tapped; False if sheet still needed a tap and none worked.
    """
    dest = (dest or "feed").lower()
    if dest == "carousel":
        dest = "feed"
    xml = dump()
    tb = text_block(xml)
    if not _is_draft_exit_sheet(tb):
        return True
    print("[post] draft-exit sheet — leave draft (dest=%s)" % dest)
    ok = _dismiss_save_draft_sheet(xml, label=label)
    time.sleep(0.8)
    # Re-assert format destination (feed job must not stay on REEL)
    _ensure_create_dest(dump(), dest=dest, force=True)
    time.sleep(0.6)
    return ok


def _back_out_of_composer(max_backs=8, label="stale-back"):
    """Back + Start over/Discard until FEED / create surface. Returns final detect_state."""
    stop = ("FEED", "CREATE_PICKER", "CREATE_CHOOSER", "CREATE_CAMERA")
    for _ in range(max_backs):
        xml = dump()
        st = detect_state(xml)
        if st in stop:
            return st
        if st in ("TIP_SHEET", "ONBOARD_CARD", "NOTIF_PROMPT", "SAVE_INFO"):
            if st == "SAVE_INFO":
                tap_exact(xml, "Not now", "Not Now", label="%s-save" % label)
            elif st == "NOTIF_PROMPT":
                _dismiss_notif_prompt(xml, label="%s-notif" % label)
            else:
                dismiss_tip_sheet(xml, label="%s-tip" % label, allow_next=False)
            time.sleep(1.0)
            continue
        # Draft sheet FIRST (before Discard-only list / BACK)
        if _dismiss_save_draft_sheet(xml, label=label):
            continue
        if tap_exact(xml, "Discard", "Discard draft", "Delete draft", "Don't save",
                     "Discard changes", "Yes", "OK", label="%s-discard" % label):
            time.sleep(1.2)
            continue
        backed = False
        for n in nodes(xml):
            d = attr(n, "content-desc").strip().lower()
            rid = attr(n, "resource-id").lower()
            if d in ("back", "back to home", "close", "cancel") or "cancel_button" in rid:
                if tapn(n, label):
                    backed = True
                    time.sleep(1.0)
                    break
        if not backed:
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1.0)
        xml2 = dump()
        if _dismiss_save_draft_sheet(xml2, label="%s2" % label):
            continue
        if tap_exact(xml2, "Discard", "Discard draft", "Delete draft", "Don't save",
                     "Discard changes", "Yes", "OK", label="%s-discard2" % label):
            time.sleep(1.2)
    return detect_state(dump())


def _on_create_surface(xml=None):
    """True when picker/camera/chooser (safe to open gallery / pick)."""
    xml = xml or dump()
    st = detect_state(xml)
    if st in ("CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER"):
        return True
    if _gallery_has_video_tile(xml):
        return True
    return False


def _open_dest_gallery(prefer_dest="reel"):
    """From create surface: chooser → dest tab → gallery. No-op if already picker."""
    prefer_dest = (prefer_dest or "reel").lower()
    if prefer_dest == "carousel":
        prefer_dest = "feed"
    st = detect_state(dump())
    if st == "CREATE_CHOOSER":
        if prefer_dest == "story":
            tap_exact(dump(), "Story", "STORY", label="stale-chooser-story")
        elif prefer_dest == "reel":
            tap_exact(dump(), "Reel", "REEL", "Reels", label="stale-chooser-reel")
        else:
            _tap_chooser_post_exact(dump()) or \
                tap_exact(dump(), "Post", "POST", label="stale-chooser-post")
        time.sleep(1.2)
        st = detect_state(dump())
    if prefer_dest == "reel":
        if st in ("CREATE_CAMERA", "CREATE_PICKER", "CREATE_CHOOSER") or _is_reel_camera(dump()):
            _ensure_create_dest(dump(), dest="reel", force=True)
            _open_gallery_from_camera(dump())
            if detect_state(dump()) == "CREATE_CAMERA" or _is_reel_camera(dump()):
                _open_gallery_from_camera(dump())
    elif prefer_dest == "story":
        if st in ("CREATE_CAMERA", "CREATE_PICKER", "CREATE_CHOOSER") or _is_reel_camera(dump()):
            _open_gallery_from_camera(dump())
    elif prefer_dest == "feed":
        if st in ("CREATE_CAMERA", "CREATE_PICKER", "CREATE_CHOOSER"):
            _ensure_create_dest(dump(), dest="feed", force=True)
    return _on_create_surface(dump())


def _hard_recover_create(prefer_dest="reel"):
    """FEED → create → gallery. Used when soft stale-abort left EDIT (hankoac542).

    Never tap gallery chip while still on CAPTION/EDIT.
    Must clear Save draft? via Start over (Daniellaall67 2026-08-04) — fail fast,
    do not burn ~11 minutes looping.
    """
    prefer_dest = (prefer_dest or "reel").lower()
    print("[post] hard recover create dest=%s" % prefer_dest)
    _rt_log("stale_hard_recover", dest=prefer_dest, state=detect_state(dump()))
    # Explicit draft kill first
    for _ in range(4):
        xml = dump()
        st = detect_state(xml)
        if st in ("FEED", "CREATE_PICKER", "CREATE_CHOOSER", "CREATE_CAMERA"):
            break
        if _dismiss_save_draft_sheet(xml, label="hard-draft"):
            continue
        st = _back_out_of_composer(max_backs=3, label="hard-back")
        if st in ("FEED", "CREATE_PICKER", "CREATE_CHOOSER", "CREATE_CAMERA"):
            break
    st = detect_state(dump())
    if st not in ("FEED", "CREATE_PICKER", "CREATE_CHOOSER", "CREATE_CAMERA"):
        # Cancel → Start over once more, then force home
        _dismiss_save_draft_sheet(dump(), label="hard-draft-final")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.8)
        _dismiss_save_draft_sheet(dump(), label="hard-draft-back")
        _ensure_on_home_feed()
        st = detect_state(dump())
    if st in ("CAPTION_SCREEN", "EDIT_SCREEN") or in_caption_screen(dump()) \
       or _reel_edit_ready(dump()) or _reel_at_caption(dump()):
        print("[post] hard recover still in composer (st=%s) — force home + create"
              % detect_state(dump()))
        # Force-stop style: home tab / relaunch, don't spin
        pkg = CURRENT_PKG or fg_pkg()
        if pkg:
            force_stop(pkg)
            time.sleep(1.0)
            launch(pkg)
            time.sleep(2.5)
        _ensure_on_home_feed()
        st = detect_state(dump())
    if st in ("CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER"):
        ok = _open_dest_gallery(prefer_dest)
        _rt_log("stale_hard_recover_done", state=detect_state(dump()), ok=ok)
        return ok
    if st != "FEED":
        _ensure_on_home_feed()
    if not _open_ig_create(prefer_dest=prefer_dest, reject_composer=True):
        _rt_log("stale_hard_recover_done", state=detect_state(dump()), ok=False,
                reason="create_fail")
        return False
    time.sleep(1.0)
    ok = _open_dest_gallery(prefer_dest)
    _rt_log("stale_hard_recover_done", state=detect_state(dump()), ok=ok)
    return ok


def _abort_stale_composer(prefer_dest="reel", max_backs=7):
    """Discard leftover caption/edit from a prior job; re-open create for prefer_dest.

    Never treat CREATE_PICKER / gallery with video tiles as stale (velorastar7 2026-08-04).
    If soft abort lands on EDIT (hankoac542), hard-recover FEED→create→gallery —
    never tap gallery chip on leftover EDIT/CAPTION.
    """
    prefer_dest = (prefer_dest or "reel").lower()
    if prefer_dest == "carousel":
        prefer_dest = "feed"
    xml = dump()
    st = detect_state(xml)
    # #region agent log
    _dbg479("C", "_abort_stale_composer", "abort_enter", {
        "runId": "pre-fix", "dest": prefer_dest, "st": st,
        "write_caption": "write a caption" in text_block(xml),
        "tb160": text_block(xml)[:160],
    })
    # #endregion
    tb = text_block(xml)
    # Healthy gallery — do NOT abort; caller should pick
    if st == "CREATE_PICKER" or _gallery_has_video_tile(xml) or \
       any(p in tb for p in ("recents", "video thumbnail", "unselected video",
                               "select multiple")):
        print("[post] not stale — already on picker/gallery (st=%s)" % st)
        return False
    if st not in ("CAPTION_SCREEN", "EDIT_SCREEN") and not in_caption_screen(xml) \
       and not _reel_edit_ready(xml) and not _reel_at_caption(xml):
        return False
    print("[post] STALE composer (state=%s dest=%s) — discard + re-open"
          % (st, prefer_dest))
    _rt_log("stale_composer_abort", state=st, dest=prefer_dest,
            snippet=tb[:220])
    st = _back_out_of_composer(max_backs=max_backs, label="stale-back")
    if st == "FEED":
        if not _open_ig_create(prefer_dest=prefer_dest, reject_composer=True):
            return _hard_recover_create(prefer_dest)
        time.sleep(1.2)
        st = detect_state(dump())
    # Only open gallery from a real create surface — never from EDIT/CAPTION
    if st in ("CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER") or _is_reel_camera(dump()):
        ok = _open_dest_gallery(prefer_dest)
    else:
        ok = False
    st2 = detect_state(dump())
    ok = ok or _on_create_surface(dump())
    _rt_log("stale_composer_after", state=st2, ok=ok, dest=prefer_dest)
    if not ok:
        print("[post] soft stale abort left state=%s — hard recover" % st2)
        return _hard_recover_create(prefer_dest)
    return True


def _abort_stale_reel_composer(max_backs=7):
    return _abort_stale_composer(prefer_dest="reel", max_backs=max_backs)


def publish_from_composer(fmt="reel", xml=None, max_rounds=10, force_caption=False):
    """Publish for real — IME down, share_button/Share/Your story, strict leave-composer.

    Shared across feed / carousel / reel / story.
    lunanest11 2026-08-04: after type, landed true EDIT — must bottom-Next to caption,
    then Share (share_button any y). Never bare top Next; never coord-spam on EDIT only.
    """
    fmt = (fmt or "reel").lower()
    if fmt == "carousel":
        fmt_key = "feed"
    else:
        fmt_key = fmt
    xml = xml or dump()
    print("[pub] build=%s force_caption=%s" % (_publish_build_id(), force_caption))
    if _SECTION is None:
        _section_begin("composer")
    _adb_ime_off()
    # Story must be on post-capture editor — gallery mode tabs are not Share.
    if fmt_key == "story" and detect_state(xml) in ("CREATE_PICKER", "CREATE_CHOOSER"):
        print("[pub] story still on gallery/chooser — refuse fake share")
        return False
    dead_cta_xy = set()  # rounded (x,y) that left us still on caption
    publish_from_composer._audio_tries = 0
    publish_from_composer._face_overlay_tried = False  # case B once only
    edit_next_tries = 0
    for rnd in range(max_rounds):
        if _section_expired(need=2):
            print("[pub] composer budget — stop publish")
            return False
        xml = dump()
        st = detect_state(xml)
        tb = text_block(xml)
        # Link/Aa sticker edit leaves Done + font chrome — exit before Share hunt.
        if fmt_key == "story" and _is_story_text_tool(xml, tb):
            print("[pub] story text/sticker overlay — Done (round %d)" % (rnd + 1))
            if not tap_exact(xml, "Done", label="pub-story-text-done"):
                for n in nodes(xml):
                    if attr(n, "clickable") != "true":
                        continue
                    d = attr(n, "content-desc").strip().lower()
                    t = attr(n, "text").strip().lower()
                    if d == "done" or t == "done":
                        tapn(n, "pub-story-text-done-node")
                        break
            time.sleep(1.1)
            continue
        # Story multi-asset edit → Next until Your story / Share appears.
        if fmt_key == "story" and st == "EDIT_SCREEN":
            tbl = tb.lower()
            if "your story" not in tbl and "add to your story" not in tbl and \
               "share to story" not in tbl:
                if "add more photos" in tbl or "ratio" in tbl or \
                   ("next" in tbl and "suggested audio" in tbl):
                    print("[pub] story edit → Next toward share round=%d" % (rnd + 1))
                    if _advance_next(xml) or tap_exact(xml, "Next", label="pub-story-edit-next"):
                        time.sleep(2.2)
                        continue
        true_edit = _is_true_reel_edit(xml)
        if (not true_edit) and fmt_key == "reel" and st == "EDIT_SCREEN" and \
           not any(p in tb for p in ("write a caption", "add a caption", "tag people",
                                       "also share")):
            true_edit = True
        if fmt_key in ("reel", "feed"):
            at_caption = (not true_edit) and (force_caption or _is_caption_composer(xml))
        else:
            at_caption = st == "CAPTION_SCREEN" or in_caption_screen(xml)
        if at_caption and fmt_key in ("reel", "feed") and not true_edit:
            print("[pub] OK (top-right) then Share - two taps")
            _caption_ok_then_share(xml)
            time.sleep(1.2)
            xml2 = dump()
            # Strict: stale dump after timeout looked like FEED and false-succeeded
            # (Jazlene 2026-09-04: SUCCESS after OK+Share then still on caption).
            if _publish_succeeded(xml2, fmt=fmt_key):
                print("[pub] SUCCESS after OK+Share")
                return True
            if _is_clips_nux_sheet(xml2):
                if _dismiss_clips_nux(xml2) and getattr(_dismiss_clips_nux, "shared", False):
                    time.sleep(1.2)
                    xml3 = dump()
                    if _publish_succeeded(xml3, fmt=fmt_key):
                        print("[pub] SUCCESS after OK+Share+NUX")
                        return True
            if _still_in_composer(xml2, fmt=fmt_key) or _still_on_share_caption(xml2):
                print("[pub] OK+Share coords missed - Share chip/rid")
                _tap_share_chip(xml2) or _tap_share_button_any(xml2, label="okshare-rid") or (
                    _section_left() >= 12 and _vision_share_button())
                time.sleep(1.0)
                xml2 = dump()
                if _publish_succeeded(xml2, fmt=fmt_key):
                    print("[pub] SUCCESS after Share chip/rid")
                    return True
            elif detect_state(xml2) == "FEED" and not _still_in_composer(xml2, fmt=fmt_key):
                print("[pub] SUCCESS after OK+Share (feed)")
                return True
            if rnd >= 1:
                print("[pub] OK+Share failed twice - stop")
                return False
        # #region agent log
        _sb = sum(1 for n in nodes(xml)
                  if (("share_button" in attr(n, "resource-id").lower()
                       and "container" not in attr(n, "resource-id").lower())
                      or "share_footer_button" in attr(n, "resource-id").lower()))
        _dbg479("A", "publish_from_composer:loop", "round_state", {
            "runId": "post-fix", "fmt": fmt_key, "rnd": rnd, "st": st,
            "at_caption": at_caption, "true_edit": true_edit,
            "also_share_in_tb": ("also share" in tb),
            "also_share_on": ("also share on" in tb),
            "also_share_sheet": _is_also_share_sheet(tb, st),
            "write_caption": ("write a caption" in tb or "add a caption" in tb),
            "tag_people_sheet": ("tap to tag people" in tb or "invite collaborators" in tb),
            "share_button_nodes": _sb,
            "edit_cover": _is_edit_cover_screen(xml),
            "tb200": tb[:200],
        })
        # #endregion
        if _publish_succeeded(xml, fmt=fmt_key):
            print("[pub] SUCCESS fmt=%s round=%d st=%s" % (fmt, rnd, st))
            _rt_log("publish", fmt=fmt, round=rnd, status="success", state=st)
            return True

        if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE",
                  "ACTION_LIMIT"):
            print("[pub] blocked by %s during share" % st)
            return False

        # Reel About Reels NUX — before share_button spam (troy 2026-09-01: 8 rounds wasted)
        if fmt_key == "reel" and _is_clips_nux_sheet(xml):
            if _dismiss_clips_nux(xml):
                if getattr(_dismiss_clips_nux, "shared", False):
                    time.sleep(1.5)
                    xml2 = dump()
                    if _publish_succeeded(xml2, fmt=fmt_key) or not _still_in_composer(xml2, fmt=fmt_key):
                        print("[pub] SUCCESS via About Reels NUX (early)")
                        _rt_log("publish", fmt=fmt, round=rnd, cta="about_reels_share_early", state=st)
                        return True
                time.sleep(0.8)
                continue

        if _dismiss_giphy_overlay(xml):
            time.sleep(0.5)
            continue
        if _dismiss_story_to_story_nux(xml):
            if fmt_key in ("feed", "carousel", "reel"):
                print("[pub] feed job after story NUX — Back (leave story editor)")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(0.8)
            continue

        if _is_media_preview_overlay(xml) or (st == "UNKNOWN" and "preview" in tb
                                               and "back" in tb):
            _dismiss_media_preview(xml)
            time.sleep(0.6)
            continue

        if st == "ANDROID_SHARE" or _is_android_share_sheet(xml, tb):
            _handle_android_share_sheet(xml, pkg=CURRENT_PKG)
            time.sleep(2.0)
            continue
        # Edit cover opened by mistake — Done back to caption BEFORE true-edit Next
        if fmt_key == "reel" and _dismiss_edit_cover(xml, label="pub-cover"):
            time.sleep(1.0)
            continue
        # Only real also-share SHEETS — not caption "Also share on…" toggle (2026-08-05)
        if _is_also_share_sheet(tb, st):
            # #region agent log
            _dbg479("A", "publish_from_composer:also_share_branch", "ENTER_also_share_sheet", {
                "runId": "post-fix", "fmt": fmt_key, "rnd": rnd, "st": st,
                "create_chooser": st == "CREATE_CHOOSER",
                "also_share_on": "also share on" in tb,
                "write_caption": ("write a caption" in tb or "add a caption" in tb),
                "will_skip_share_cta": True,
            })
            # #endregion
            if fmt_key == "story" and ("your story" in tb or "share to" in tb):
                if _tap_publish_cta(xml, fmt="story", label="pub-story-sheet"):
                    time.sleep(2.5)
                    continue
            tap_exact(xml, "Not now", "Skip", "Cancel", "Done", label="pub-skip-also")
            time.sleep(1.2)
            continue
        elif "also share" in tb and ("write a caption" in tb or "also share on" in tb):
            # #region agent log
            _dbg479("A", "publish_from_composer:also_share_branch", "SKIP_caption_also_share_on", {
                "runId": "post-fix", "fmt": fmt_key, "rnd": rnd, "st": st,
                "also_share_on": "also share on" in tb,
                "will_skip_share_cta": False,
            })
            # #endregion
            pass  # fall through to share_button

        # Reel post-capture editor — Next BEFORE dismiss_chrome (troy 2026-09-01:
        # stickers+next matched story editor → Back loop forever).
        true_edit_early = _is_true_reel_edit(xml)
        if true_edit_early and fmt_key == "reel":
            edit_next_tries += 1
            if edit_next_tries >= 3:
                print("[pub] still EDIT after %d Next — fail (no Back loop)" % edit_next_tries)
                return False
            print("[pub] true_edit round=%d — advance to caption (early)" % (rnd + 1))
            if _tap_edit_advance_next(xml, label="pub-edit-to-caption"):
                _rt_log("publish", fmt=fmt, round=rnd, cta="edit_to_caption", state=st)
                time.sleep(1.2 if not _human_on() else 2.0)
                continue
            tap(1287, 2819)
            print("[pub] edit bottom Next coord @ 1287,2819 (early)")
            time.sleep(1.2 if not _human_on() else 2.0)
            continue

        if _dismiss_composer_chrome(xml, fmt=fmt_key):
            # #region agent log
            _dbg479("B", "publish_from_composer:dismiss_chrome", "dismissed_chrome", {
                "runId": "post-fix", "fmt": fmt_key, "rnd": rnd, "st": st,
            })
            # #endregion
            if getattr(_dismiss_clips_nux, "shared", False):
                time.sleep(1.5)
                xml2 = dump()
                if _publish_succeeded(xml2, fmt=fmt_key) or not _still_in_composer(xml2, fmt=fmt_key):
                    print("[pub] SUCCESS via About Reels Share (chrome path)")
                    return True
            time.sleep(0.8)
            continue

        # About Reels NUX (rumeysa: Share on sheet publishes; else Cancel then caption Share)
        if _dismiss_clips_nux(xml):
            if getattr(_dismiss_clips_nux, "shared", False):
                time.sleep(1.5)
                xml2 = dump()
                if _publish_succeeded(xml2, fmt=fmt_key) or not _still_in_composer(xml2, fmt=fmt_key):
                    print("[pub] SUCCESS via About Reels NUX Share")
                    _rt_log("publish", fmt=fmt, round=rnd, cta="about_reels_share", state=st)
                    return True
            time.sleep(0.8)
            continue

        # Tag people sheet — no Share until Done (deryaozgur335)
        if _dismiss_tag_people(xml):
            time.sleep(0.8)
            continue

        # Music/track sheet over caption — Share is visible but inert (cansu/nergis)
        # Cap attempts so we don't burn all publish rounds on Back no-ops
        if _is_audio_picker_overlay(xml):
            audio_tries = getattr(publish_from_composer, "_audio_tries", 0)
            if audio_tries < 2:
                publish_from_composer._audio_tries = audio_tries + 1
                cleared = _dismiss_audio_picker()
                time.sleep(0.5)
                if cleared or not _is_audio_picker_overlay():
                    publish_from_composer._audio_tries = 0
                continue
            print("[pub] audio still open after dismiss budget — try Share anyway")
            # fall through; Share may still be inert but better than infinite backs

        _hide_ime()
        xml = dump()
        st = detect_state(xml)
        tb = text_block(xml)
        # Re-check after IME hide — songulertas: detector flipped true only on 2nd dump
        if _dismiss_tag_people(xml):
            time.sleep(0.6)
            continue
        if _is_audio_picker_overlay(xml):
            audio_tries = getattr(publish_from_composer, "_audio_tries", 0)
            if audio_tries < 2:
                publish_from_composer._audio_tries = audio_tries + 1
                _dismiss_audio_picker()
                time.sleep(0.5)
                continue
            print("[pub] audio still open after dismiss budget — try Share anyway")
        true_edit = _is_true_reel_edit(xml)
        if (not true_edit) and fmt_key == "reel" and st == "EDIT_SCREEN" and \
           not any(p in tb for p in ("write a caption", "add a caption", "tag people",
                                       "also share")):
            true_edit = True
        if fmt_key in ("reel", "feed"):
            at_caption = (not true_edit) and (force_caption or _is_caption_composer(xml))
        else:
            at_caption = st == "CAPTION_SCREEN" or in_caption_screen(xml)

        # 0) Edit video / EDIT_SCREEN — BOTTOM Next to caption FIRST
        if true_edit and fmt_key == "reel":
            edit_next_tries += 1
            if edit_next_tries >= 3:
                print("[pub] still EDIT after %d Next — fail (no Back loop)" % edit_next_tries)
                return False
            print("[pub] true_edit round=%d — advance to caption" % (rnd + 1))
            if _tap_edit_advance_next(xml, label="pub-edit-to-caption"):
                print("[pub] edit→caption via bottom Next round=%d" % (rnd + 1))
                _rt_log("publish", fmt=fmt, round=rnd, cta="edit_to_caption", state=st)
                time.sleep(2.5)
                continue
            # Coord once for bottom Next on edit — clips_right_action_button ~1287,2819
            tap(1287, 2819)
            print("[pub] edit bottom Next coord @ 1287,2819")
            time.sleep(2.5)
            continue

        # 0b) Caption field focused → top OK must confirm before Share (selinkorkmaz)
        if (at_caption or force_caption) and fmt_key in ("reel", "feed") and not true_edit:
            ok_n = _caption_ok_button(xml)
            if ok_n is not None:
                tapn(ok_n, "pub-caption-ok")
                print("[pub] caption OK (exit edit mode) round=%d" % (rnd + 1))
                time.sleep(1.2)
                _hide_ime()
                xml = dump()
                if _is_audio_picker_overlay(xml):
                    _dismiss_audio_picker()
                    time.sleep(0.5)
                    xml = dump()
                st = detect_state(xml)
                tb = text_block(xml)
                at_caption = force_caption or _is_caption_composer(xml)

        # Never tap Share while a real track sheet is still up
        if _is_audio_picker_overlay(xml):
            print("[pub] skip Share — audio sheet still open")
            _dismiss_audio_picker()
            time.sleep(0.5)
            continue

        # Caption Next is in the *lower* chrome (y ~78–90%). share_button
        # resource-id often maps to the IME key — hide IME then tap Next.
        at_caption = force_caption or _is_caption_composer(xml)
        if at_caption:
            _adb_ime_off()
            time.sleep(0.35)
            xml = dump()
            if _tap_caption_footer_next(xml):
                time.sleep(2.0)
                xml = dump()
                if not _is_caption_composer(xml):
                    continue
                # Still on New reel — Next didn't leave; try Share with IME off.

        # 1) share_footer / share_button / Share.
        # Caption OK already handled above — never rank top bar as publish.
        # Landscape top Share still reachable via _tap_share_button_any.
        _allow_top = False
        _cands = _find_publish_nodes(xml, fmt=fmt_key, allow_top_share=_allow_top)
        # #region agent log
        # tuple: (pri, -y, -x, w, n, kind) → log kind, y, x, w
        _dbg479("D", "publish_from_composer:cta_search", "publish_candidates", {
            "runId": "post-fix", "fmt": fmt_key, "rnd": rnd,
            "n_cands": len(_cands),
            "top": [(_c[5], int(-_c[1]), int(-_c[2]), int(_c[3])) for _c in _cands[:4]],
            "dead_xy": list(dead_cta_xy)[:6],
            "at_caption": at_caption, "force_caption": force_caption,
            "audio_overlay": _is_audio_picker_overlay(xml),
            "tag_people": _is_tag_people_sheet(xml),
        })
        # #endregion
        if _tap_publish_cta(xml, fmt=fmt_key, label="pub-%s-%d" % (fmt, rnd),
                            allow_top_share=_allow_top,
                            skip_xy=dead_cta_xy):
            tapped_xy = getattr(_tap_publish_cta, "last_xy", None)
            print("[pub] tapped CTA fmt=%s round=%d caption=%s xy=%s"
                  % (fmt, rnd + 1, at_caption, tapped_xy))
            _rt_log("publish", fmt=fmt, round=rnd, cta="share_or_next",
                    state=st, caption=at_caption, xy=tapped_xy)
            # #region agent log
            _dbg479("A", "publish_from_composer:cta_tap", "tapped_share_cta", {
                "runId": "post-fix", "fmt": fmt_key, "rnd": rnd,
                "at_caption": at_caption, "xy": tapped_xy,
            })
            # #endregion
            time.sleep(3.5 if fmt_key != "story" else 4.0)
            xml2 = dump()
            if _publish_succeeded(xml2, fmt=fmt_key):
                print("[pub] SUCCESS after CTA fmt=%s" % fmt)
                return True
            if _still_in_composer(xml2, fmt=fmt_key):
                # Real audio sheet may have opened — dismiss once, then mark Share dead
                # so we never Share↔Back forever (songulertas826)
                if _is_audio_picker_overlay(xml2):
                    print("[pub] audio overlay after Share — dismiss once then mark dead")
                    _dismiss_audio_picker()
                    time.sleep(0.8)
                    xml3 = dump()
                    if _publish_succeeded(xml3, fmt=fmt_key) or \
                       not _still_in_composer(xml3, fmt=fmt_key):
                        print("[pub] SUCCESS after audio dismiss")
                        return True
                # Reel: Share often opens About Reels NUX slowly. Never Back
                # (troy 2026-09-02: face-overlay Back → EDIT_SCREEN).
                if fmt_key == "reel" and (at_caption or force_caption):
                    if _poll_reel_nux_after_share(fmt=fmt_key):
                        return True
                    # Keyboard may still be covering Share — proven Note 8 sequence
                    print("[pub] still caption — Note8 OK then Share")
                    _note8_ok_then_share()
                    time.sleep(2.5)
                    xml_n = dump()
                    if _publish_succeeded(xml_n, fmt=fmt_key) or \
                       not _still_in_composer(xml_n, fmt=fmt_key):
                        print("[pub] SUCCESS via Note8 OK+Share")
                        return True
                    if _poll_reel_nux_after_share(fmt=fmt_key):
                        return True
                    print("[pub] still caption after Share — stop (no NUX, no 10min retry)")
                    return False
                # Case B (feed only): transparent face over caption.
                if (not getattr(publish_from_composer, "_face_overlay_tried", False)
                        and (at_caption or force_caption)
                        and fmt_key == "feed"
                        and _still_on_share_caption(xml2)):
                    publish_from_composer._face_overlay_tried = True
                    if _dismiss_caption_face_overlay_once(xml2):
                        print("[pub] retry Next after face overlay Back")
                        continue
                print("[pub] still in composer after CTA (st=%s) — mark dead xy=%s"
                      % (detect_state(xml2), tapped_xy))
                if tapped_xy:
                    dead_cta_xy.add(tapped_xy)
                # Reel: right-edge share_button often inert — try center footer immediately
                if fmt_key == "reel" and (at_caption or force_caption):
                    if _tap_bottom_share_coords(fmt="reel"):
                        print("[pub] SUCCESS via center footer coord after dead CTA")
                        return True
                # #region agent log
                _dbg479("E", "publish_from_composer:after_cta", "still_composer", {
                    "runId": "post-fix", "fmt": fmt_key, "rnd": rnd,
                    "st2": detect_state(dump()), "tb2": text_block(dump())[:200],
                    "dead_xy": list(dead_cta_xy),
                })
                # #endregion
                continue
            return True

        # 2) Caption: share any y, then bottom Share, then limited coords
        if (at_caption or force_caption) and fmt_key in ("reel", "feed") and not true_edit:
            if _tap_share_button_any(xml, label="pub-share-any-%d" % rnd,
                                     skip_xy=dead_cta_xy):
                tapped_xy = getattr(_tap_share_button_any, "last_xy", None)
                print("[pub] tapped share-any round=%d xy=%s" % (rnd + 1, tapped_xy))
                _rt_log("publish", fmt=fmt, round=rnd, cta="share_any", state=st)
                time.sleep(3.5)
                xml2 = dump()
                if _publish_succeeded(xml2, fmt=fmt_key) or not _still_in_composer(xml2, fmt=fmt_key):
                    print("[pub] SUCCESS after share-any")
                    return True
                if _is_audio_picker_overlay(xml2):
                    _dismiss_audio_picker()
                    time.sleep(0.8)
                if fmt_key == "reel":
                    print("[pub] still caption after share-any — no Back")
                    if tapped_xy:
                        dead_cta_xy.add(tapped_xy)
                    continue
                if (not getattr(publish_from_composer, "_face_overlay_tried", False)
                        and fmt_key == "feed"
                        and _still_on_share_caption(xml2)):
                    publish_from_composer._face_overlay_tried = True
                    if _dismiss_caption_face_overlay_once(xml2):
                        print("[pub] retry Next after face overlay Back (share-any)")
                        continue
                if tapped_xy:
                    dead_cta_xy.add(tapped_xy)
                continue
            if _tap_bottom_share_or_next(xml, label="pub-caption-bottom-%d" % rnd,
                                        skip_xy=dead_cta_xy):
                tapped_xy = getattr(_tap_bottom_share_or_next, "last_xy", None)
                print("[pub] tapped bottom Share/Next (caption) round=%d xy=%s"
                      % (rnd + 1, tapped_xy))
                time.sleep(3.5)
                xml2 = dump()
                if _publish_succeeded(xml2, fmt=fmt_key) or not _still_in_composer(xml2, fmt=fmt_key):
                    return True
                if _is_audio_picker_overlay(xml2):
                    _dismiss_audio_picker()
                if fmt_key == "reel":
                    print("[pub] still caption after bottom Share — no Back")
                    if tapped_xy:
                        dead_cta_xy.add(tapped_xy)
                    continue
                if (not getattr(publish_from_composer, "_face_overlay_tried", False)
                        and fmt_key == "feed"
                        and _still_on_share_caption(xml2)):
                    publish_from_composer._face_overlay_tried = True
                    if _dismiss_caption_face_overlay_once(xml2):
                        print("[pub] retry Next after face overlay Back (bottom)")
                        continue
                if tapped_xy:
                    dead_cta_xy.add(tapped_xy)
                continue
            # Top-right OK coords only while caption field is still focused.
            # Never use them as publish after OK is gone (2026-08-13 top_ok @179).
            if rnd >= 1 and _caption_ok_button(xml) is not None:
                _sw, _sh = _screen_wh(xml)
                top_coords = ((1321, 179), (1360, 168), (1280, 200))
                if _sw > 2000:  # landscape Note 8
                    top_coords = ((2867, 168), (2800, 168), (2700, 180), (1321, 179))
                for x, y in top_coords:
                    tap(x, y)
                    print("[pub] caption OK coord @ %d,%d" % (x, y))
                    time.sleep(1.2)
                    xml2 = dump()
                    if _is_true_reel_edit(xml2):
                        break
                    if _is_tag_people_sheet(xml2):
                        _dismiss_tag_people(xml2)
                        break
                    if _caption_ok_button(xml2) is None:
                        break  # exited edit mode — next round taps bottom Next
            if rnd >= 1 and fmt_key == "feed" and _tap_feed_share_coords(xml):
                print("[pub] SUCCESS via feed share coords")
                return True
            # Reel caption: never 6× coord spam (2.8s+dump each) — that is the 10min sit.
            print("[pub] caption miss round %d st=%s true_edit=%s" % (rnd + 1, st, true_edit))
            _rt_log("publish_miss", fmt=fmt, round=rnd, state=st, caption=True,
                    snippet=tb[:200])
            time.sleep(1.0)
            continue

        # 3) Non-caption edit fallback
        if fmt_key == "reel" and (st == "EDIT_SCREEN" or _reel_edit_ready(xml)):
            if _tap_edit_advance_next(xml, label="pub-edit-next"):
                time.sleep(2.5)
                continue
        if fmt_key in ("feed",) and st == "EDIT_SCREEN":
            if _tap_edit_advance_next(xml, label="pub-feed-edit-next"):
                time.sleep(2.0)
                continue
        print("[pub] no CTA round %d fmt=%s st=%s" % (rnd + 1, fmt, st))
        _rt_log("publish_miss", fmt=fmt, round=rnd, state=st, snippet=tb[:200])
        time.sleep(1.2)

    xml_f = dump()
    still = _still_in_composer(xml_f, fmt=fmt_key)
    _rt_log("publish_fail", fmt=fmt, still_composer=still, state=detect_state(xml_f),
            snippet=text_block(xml_f)[:240])
    return (not still) and _publish_succeeded(xml_f, fmt=fmt_key)


def publish_reel_from_caption(xml=None, max_rounds=10):
    return publish_from_composer(fmt="reel", xml=xml, max_rounds=max_rounds)


def _bottom_nav_nodes(xml, y_min=2300):
    """Clickable nodes in the bottom strip (Note 8 ~2960 tall)."""
    out = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < y_min:
            continue
        out.append((x, y, n, attr(n, "content-desc"), attr(n, "text"),
                    attr(n, "resource-id")))
    out.sort(key=lambda r: r[0])
    return out


def _top_bar_nodes(xml, y_max=220):
    """True IG action-bar clickables only.

    aerinaa70 2026-08-04: y_max=450 pulled feed UFI row (likes @ y~270) and we
    tapped empty leftmost like chrome as Create forever.
    """
    feed_ban = (
        "row_feed", "like", "comment", "save", "media_option", "media_group",
        "follow", "ufi", "feedback", "sponsor", "carousel", "video_states",
        "row_feed_button", "row_feed_photo",
    )
    out = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y > y_max:
            continue
        rid = (attr(n, "resource-id") or "").lower()
        d = (attr(n, "content-desc") or "").lower()
        t = (attr(n, "text") or "").strip()
        blob = rid + " " + d
        if any(b in blob for b in feed_ban):
            continue
        # Numeric like/comment counts on engagement row
        if t and t.replace(",", "").replace(".", "").replace("k", "").replace(
                "m", "").replace(" ", "").isdigit():
            continue
        out.append((x, y, n, attr(n, "content-desc"), attr(n, "text"),
                    attr(n, "resource-id")))
    out.sort(key=lambda r: (r[1], -r[0]))  # top row, rightmost first
    return out


def _find_create_affordance(xml):
    """Find real Create/+ node anywhere (desc/rid). Returns node or None."""
    best = None
    best_score = 0
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        if attr(n, "enabled").lower() not in ("", "true"):
            continue
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        blob = " ".join([d, t, rid])
        # Hard bans — never treat feed engagement / tabs / launcher as Create
        if any(b in blob for b in (
                "row_feed", "like", "comment", "save", "notification", "news_tab",
                "home", "search", "profile", "message", "direct_tab", "clips_tab",
                "feed_tab", "add to saved", "more actions",
                "phone", "messaging", "contacts", "browser", "play store")):
            continue
        # Stock Android Camera app is not IG Create
        if d == "camera" or t == "camera":
            if "instagram" not in rid and "creation" not in rid and "tab_camera" not in rid:
                continue
        score = 0
        if "action_bar_left_button" in rid:
            score += 40
        # Jazlene 2026-09-04: top-right desc='Create a reel' (x~1344) — not left +
        if "create a reel" in d or d == "create a reel":
            score += 45
        if "create a post" in d or "reel or live" in d:
            score += 40
        if d in ("create", "+", "new post") or t in ("create", "+", "new post"):
            score += 30
        if any(k in blob for k in ("creation_tab", "new_post", "action_bar_create",
                                     "tab_camera", "camera_tab")):
            score += 20
        # comment_composer / reply composer is NOT create
        if "composer" in rid and "comment" not in blob and "reply" not in blob:
            score += 20
        if "create" in d and y < 400:
            score += 15 if x < 500 else 12
        if score and y < 500:
            score += 5
        if score > best_score:
            best_score = score
            best = n
    return best if best_score >= 15 else None


def _reveal_feed_action_bar():
    """Scroll feed toward top + re-select Home so action-bar Create can appear."""
    print("[create] reveal action bar (Home + scroll top)")
    if not _ensure_on_home_feed():
        print("[create] abort reveal — not on Home feed")
        return False
    xml = dump()
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        y = bounds_center(n)[1]
        if y and y > 2400 and ("home" in d or "feed_tab" in rid):
            tapn(n, "create-home-reselect")
            time.sleep(0.8)
            tapn(n, "create-home-reselect2")  # second tap often scrolls to top
            time.sleep(1.0)
            break
    # Swipe down (finger down) to pull content toward revealing top
    for _ in range(2):
        adb("shell", "input", "swipe", "720", "1100", "720", "2200", "250")
        time.sleep(0.45)


def _tap_create_coords():
    """Proven top-left Create coords (androii 2026-07-24). Verify after each.

    Stop if a tap leaked to the Samsung launcher — further 54,168 taps hit Home/X
    (arzu16380 2026-08-11).
    """
    if not _on_home_feed_verified(dump()):
        print("[create] refuse coord Create — not verified IG Home")
        return False
    for x, y in ((54, 168), (80, 180), (120, 160)):
        tap(x, y)
        print("[create] top coord @ %d,%d" % (x, y))
        time.sleep(1.8)
        xml = dump()
        if _create_success(detect_state(xml), xml):
            return True
        tb = text_block(xml).lower()
        if sum(1 for m in ("play store", "messaging", "contacts", "browser",
                           "super proxy") if m in tb) >= 2:
            print("[create] coord leaked to launcher — stop spam")
            if CURRENT_PKG:
                launch(CURRENT_PKG)
                time.sleep(2.2)
            return False
    return False


def _nav_has_create_tab(bottoms):
    for _x, _y, _n, d, t, rid in bottoms:
        blob = (" ".join([d, t, rid])).lower()
        if any(k in blob for k in ("create", "new_post", "new post", "creation_tab",
                                     "tab_camera", "camera_tab", "composer")):
            if "story" in blob or "reel" in blob or "clips" in blob:
                continue
            return True
    return False


def _nav_is_message_center(bottoms):
    """2026-07-24 androii: Home | Reels | Message | Search | Profile — no Create tab."""
    descs = " ".join((d or "").lower() for _x, _y, _n, d, _t, _rid in bottoms)
    rids = " ".join((rid or "").lower() for _x, _y, _n, _d, _t, rid in bottoms)
    blob = descs + " " + rids
    has_msg = ("message" in blob or "direct_tab" in blob or "direct" in blob)
    has_home = "home" in blob or "feed_tab" in blob
    has_reels = "reels" in blob or "clips_tab" in blob
    return has_msg and has_home and has_reels and not _nav_has_create_tab(bottoms)


def _create_success(st=None, xml=None):
    st = st or detect_state(xml or dump())
    if st in ("CREATE_CHOOSER", "CREATE_PICKER", "EDIT_SCREEN", "CAPTION_SCREEN",
              "CREATE_CAMERA"):
        return True
    tb = text_block(xml or dump())
    if "what do you want to share" in tb:
        return True
    if "new post" in tb and any(p in tb for p in ("recents", "gallery", "next", "camera")):
        return True
    return False


def _try_create_intent(pkg=None):
    """Open IG create via deep link / SEND-with-file (activities are often not exported)."""
    pkg = pkg or CURRENT_PKG or fg_pkg()
    if not pkg or "instagram" not in pkg:
        return False

    # 1) VIEW create/library deep links (works on many IG builds)
    for uri in (
            "https://www.instagram.com/create/style/",
            "instagram://library",
            "instagram://share",
            "https://www.instagram.com/create/story",
    ):
        print("[create] try VIEW %s" % uri)
        adb("shell", "am", "start", "-a", "android.intent.action.VIEW",
            "-d", uri, "-p", pkg)
        time.sleep(2.8)
        xml = dump()
        st = detect_state(xml)
        print("[create] after VIEW -> %s | %s" % (st, text_block(xml)[:120]))
        if _create_success(st, xml):
            return True
        # Story camera isn't feed post — BACK and keep trying
        if "story" in uri or st == "UNKNOWN":
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(1.0)

    # 2) ACTION_SEND with the file we just pushed (Meta's supported share path)
    remote = CURRENT_REMOTE_IMG
    if remote:
        uri = "file://" + remote
        mime = _media_mime(remote)
        print("[create] try ACTION_SEND %s → %s" % (remote, pkg.split(".")[-1]))
        adb("shell", "am", "start",
            "-a", "android.intent.action.SEND",
            "-t", mime,
            "--eu", "android.intent.extra.STREAM", uri,
            "-p", pkg)
        time.sleep(3.0)
        xml = dump()
        st = detect_state(xml)
        print("[create] after SEND -> %s | %s" % (st, text_block(xml)[:160]))
        if _create_success(st, xml):
            return True
        # Share sheet / chooser — tap Instagram / Post if shown
        if tap_exact(xml, "Post", "Feed", "Share to Feed", "Add post",
                     "Reel", "REEL", "Reels", label="send-sheet-post"):
            time.sleep(3.5)
            xml3 = dump()
            if _is_android_share_sheet(xml3):
                tap_exact(xml3, "Just once", "Only once", label="send-just-once")
                time.sleep(2.5)
                xml3 = dump()
            if _create_success(detect_state(xml3), xml3):
                print("[create] SEND sheet opened create")
                return True
            # Picker often opens a beat later (arzu16380 2026-08-11 left on New post)
            time.sleep(2.0)
            if _create_success():
                print("[create] SEND sheet opened create (late)")
                return True
        if tap_first(xml, "Instagram", label="send-sheet-ig"):
            time.sleep(3.0)
            if _create_success():
                return True

    # 3) Last: non-exported activities (may no-op; cheap to try once)
    for comp in (
            "%s/com.instagram.creation.activity.MediaCaptureActivity" % pkg,
            "%s/com.instagram.mainactivity.CameraMainActivity" % pkg,
    ):
        print("[create] try activity %s" % comp.split("/")[-1])
        adb("shell", "am", "start", "-n", comp)
        time.sleep(2.0)
        if _create_success():
            return True
    return False


def _tap_top_create(xml):
    """Create/+ in top action bar (Message-center nav). Never tap feed UFI junk.

    Proven 2026-07-24 androii: LEFT button
      desc='Create a post, story, reel or live video'
      id=.../action_bar_left_button @ 54,168
    aerinaa70 2026-08-04: unlabeled @ 78,270 was like-row — must NOT tap.
    Returns True only if create surface actually opened.
    """
    xml = xml or dump()
    # 1) Explicit affordance anywhere (best)
    n = _find_create_affordance(xml)
    if n:
        if tapn(n, "open-create-affordance"):
            time.sleep(2.2)
            if _create_success():
                return True
            time.sleep(1.4)
            if _create_success():
                return True
            print("[create] Create + tapped — skip same-xy coord spam")
            return False

    tops = _top_bar_nodes(xml)
    if not getattr(_tap_top_create, "_dumped", False):
        _tap_top_create._dumped = True
        print("[create] top-bar clickables (%d):" % len(tops))
        for x, y, _n, d, t, rid in tops:
            print("   x=%4d y=%4d desc=%r text=%r id=%s"
                  % (x, y, (d or "")[:40], (t or "")[:20], (rid or "")[-48:]))

    ban = ("home", "reels", "search", "profile", "message", "direct", "notification",
           "notifications", "news_tab", "like", "comment", "share", "save", "watch",
           "story", "live", "settings", "options", "more", "unread", "row_feed")
    want = ("create", "new post", "newpost", "camera", "creation",
            "plus", "add post", "make a post", "action_bar_left_button",
            "create a post", "create a reel", "reel or live")
    candidates = []
    for x, y, n, d, t, rid in tops:
        blob = (" ".join([d, t, rid])).lower()
        if "comment" in blob or "reply" in blob:
            continue
        if any(b in blob for b in ban) and not any(w in blob for w in want):
            continue
        if "news_tab" in blob or "notification" in blob:
            continue
        score = 0
        if "action_bar_left_button" in blob or "create a post" in blob:
            score += 30
        if "create a reel" in blob:
            score += 35
        if any(w in blob for w in want):
            score += 10
        if (d or "").strip() in ("+", "Create", "New post", "Camera"):
            score += 20
        if "create" in (rid or "").lower() or "new_post" in (rid or "").lower():
            score += 15
        # Bonus for top-left ONLY when already a create signal
        if score and x < 300 and y < 220:
            score += 8
        if score >= 15:
            candidates.append((score, x, y, n, d or t or rid))
    candidates.sort(key=lambda r: (-r[0], r[1]))
    for score, x, y, n, lab in candidates:
        if tapn(n, "open-create-top:%s" % str(lab)[:28]):
            time.sleep(2.0)
            if _create_success():
                return True
            print("[create] top candidate score=%d @%d,%d did not open create"
                  % (score, x, y))
            # Same button as Create + — extra taps close the picker / go Home
            return False

    # Message-center: unlabeled top-left (often + with empty desc) — Nylah 2026-09-04
    # dump had clickable @72,168 with no text/desc; coord spam then timed out.
    for x, y, n, d, t, rid in tops:
        if x is None or y is None or x > 220 or y > 300:
            continue
        blob = (" ".join([d, t, rid])).lower()
        if any(b in blob for b in ban):
            continue
        if tapn(n, "open-create-top-left"):
            time.sleep(2.0)
            if _create_success():
                return True
            break

    # 2) Proven coords (no unlabeled leftmost feed fallback — that was the bug)
    if _tap_create_coords():
        return True
    return False


def _tap_ig_create_button(xml):
    """Open IG Create. Handles classic Create-tab nav AND Message-center nav (no Create tab).

    2026-07-24 androii dump: bottom = Home|Reels|Message|Search|Profile — mid-tab
    wrongly hit Message / likes. Create is top-bar +.
    2026-08-07 dilanunal2026: bottom = Phone|Messaging|Contacts|Browser|Camera
    (Samsung launcher) — never tap those; relaunch IG first.
    Returns True only when create surface opened (verify), not mere tap success.
    """
    bottoms = _bottom_nav_nodes(xml)
    if not getattr(_tap_ig_create_button, "_dumped", False):
        _tap_ig_create_button._dumped = True
        print("[create] bottom-nav clickables (%d):" % len(bottoms))
        for x, y, _n, d, t, rid in bottoms:
            print("   x=%4d y=%4d desc=%r text=%r id=%s"
                  % (x, y, (d or "")[:40], (t or "")[:20], (rid or "")[-40:]))
        if _nav_is_message_center(bottoms):
            print("[create] detected Message-center nav (no Create tab) → use top-bar +")

    launcher_marks = ("phone", "messaging", "contacts", "browser", "play store")
    bottom_blob = " ".join(
        ("%s %s" % (d or "", t or "")).lower() for _x, _y, _n, d, t, _r in bottoms
    )
    if sum(1 for m in launcher_marks if m in bottom_blob) >= 2:
        print("[create] launcher nav detected (not IG) — relaunch clone")
        if CURRENT_PKG:
            adb("shell", "am", "force-stop", CURRENT_PKG)
            time.sleep(0.8)
            adb("shell", "monkey", "-p", CURRENT_PKG, "-c",
                "android.intent.category.LAUNCHER", "1")
            time.sleep(2.5)
        return False

    # Do NOT ban bare "reel" — "Create a reel" is a real top-bar create (Jazlene 2026-09-04)
    ban = ("story", "reels", "home", "search", "profile", "shop",
           "direct", "message", "messages", "notification", "live", "clips",
           "like", "comment", "watch again", "saved", "phone", "messaging",
           "contacts", "browser", "play store")

    # A) Explicit Create affordance anywhere
    n = _find_create_affordance(xml)
    if n:
        if tapn(n, "open-create-affordance"):
            time.sleep(2.2)
            if _create_success():
                return True
            time.sleep(1.4)
            if _create_success():
                return True

    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        x, y = bounds_center(n)
        if y is None:
            continue
        d = attr(n, "content-desc").strip()
        t = attr(n, "text").strip()
        rid = attr(n, "resource-id")
        blob = (" ".join([d, t, rid])).lower()
        # Feed UFI / comment box — never Create (comment_composer matched "composer")
        if "comment" in blob or "reply" in blob:
            continue
        if any(b in blob for b in ban):
            continue
        # Tab "Reels" / clips — not create; keep "create a reel" via affordance above
        if (("reel" in blob or "reels" in blob) and "create" not in blob
                and "creation" not in blob):
            continue
        # Never treat stock Android "Camera" app as IG Create
        if (d or t).strip().lower() == "camera" and "instagram" not in (rid or "").lower():
            continue
        exact = d.lower() in ("create", "new post", "+", "post", "create a reel") or \
                t.lower() in ("create", "new post", "post", "+", "create a reel")
        soft = any(k in blob for k in ("create", "new post", "creation_tab",
                                         "tab_camera", "camera_tab",
                                         "new_post", "action_bar_create",
                                         "action_bar_left_button", "reel or live",
                                         "create a reel"))
        if exact or soft:
            if tapn(n, "open-create:%s" % (d or t or rid)[:28]):
                time.sleep(1.5)
                if _create_success():
                    return True

    # B) Message-center nav → top-bar Create/+ (do NOT mid-tap bottom)
    if _nav_is_message_center(bottoms) or not _nav_has_create_tab(bottoms):
        return _tap_top_create(xml)

    # C) Classic Create tab in bottom nav
    for x, y, n, d, t, rid in bottoms:
        blob = (" ".join([d, t, rid])).lower()
        if "comment" in blob:
            continue
        if any(b in blob for b in ban):
            continue
        if (d or t or "").strip().lower() == "camera" and "instagram" not in (rid or "").lower():
            continue
        if any(k in blob for k in ("create", "new post", "creation",
                                     "tab_camera", "gallery_tab")):
            if tapn(n, "open-create-nav:%s" % (d or t or rid)[:28]):
                time.sleep(1.5)
                if _create_success():
                    return True

    # D) Five-tab classic middle ONLY if create-ish and not Message
    if len(bottoms) >= 5 and _nav_has_create_tab(bottoms):
        by_y = sorted(bottoms, key=lambda r: -r[1])[:8]
        med_y = sorted(r[1] for r in by_y)[len(by_y) // 2]
        tabs = [r for r in by_y if abs(r[1] - med_y) < 120]
        tabs.sort(key=lambda r: r[0])
        if len(tabs) >= 5:
            mid = tabs[len(tabs) // 2]
            blob = (" ".join([mid[3], mid[4], mid[5]])).lower()
            if not any(b in blob for b in ("message", "direct", "like", "watch")):
                if tapn(mid[2], "open-create-midtab@%d,%d" % (mid[0], mid[1])):
                    time.sleep(1.5)
                    if _create_success():
                        return True

    return False


def _is_search_explore_tab(xml=None, tb=None):
    """IG Search/Explore tab — bottom nav still shows home/reels/search → false FEED."""
    xml = xml or dump()
    tb = (tb if tb is not None else text_block(xml)).lower()
    if edits(xml) and any(p in tb for p in (
            "recent", "recent searches", "try searching", "search for",
            "accounts", "audio", "tags", "places", "not now", "clear all")):
        if "write a caption" not in tb and "tag people" not in tb:
            return True
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").lower()
        if "search" in rid and ("edit" in rid or "row_search" in rid):
            y = bounds_center(n)[1]
            if y and y < 500:
                return True
        sel = attr(n, "selected") == "true" or "selected" in d
        if sel and ("search" in d or "search_tab" in rid):
            return True
    return False


def _is_launcher_bottom_nav(xml=None):
    """Samsung launcher icons in bottom bar — never treat as IG Home."""
    xml = xml or dump()
    bottoms = _bottom_nav_nodes(xml)
    blob = " ".join(
        ("%s %s" % (d or "", t or "")).lower()
        for _x, _y, _n, d, t, _r in bottoms
    )
    marks = ("phone", "messaging", "contacts", "browser", "play store", "screenshot")
    return sum(1 for m in marks if m in blob) >= 2


def _tap_home_tab(xml=None):
    xml = xml or dump()
    sw, sh = _screen_wh(xml)
    y_min = int(sh * 0.78) if sh else 2400
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        y = bounds_center(n)[1]
        if y and y >= y_min and (d == "home" or t == "home" or "feed_tab" in rid):
            return tapn(n, "home-tab")
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        y = bounds_center(n)[1]
        if y and y >= y_min and ("home" in d or "feed_tab" in rid):
            return tapn(n, "home-tab")
    return False


def _on_home_feed_verified(xml=None):
    xml = xml or dump()
    st = detect_state(xml)
    if st != "FEED":
        return False
    if _is_search_explore_tab(xml):
        return False
    if _is_launcher_bottom_nav(xml):
        return False
    if _is_wrong_app_xml(xml):
        return False
    return True


def _ensure_on_home_feed(max_tries=5):
    """Reach real IG Home feed — not Search tab, not launcher."""
    for attempt in range(max_tries):
        xml = dump()
        if _on_home_feed_verified(xml):
            return True
        st = detect_state(xml)
        print("[home] recover attempt %d state=%s" % (attempt + 1, st))
        if _is_launcher_bottom_nav(xml) or st == "WRONG_APP" or _is_wrong_app_xml(xml):
            if CURRENT_PKG:
                adb("shell", "am", "force-stop", CURRENT_PKG)
                time.sleep(0.6)
                launch(CURRENT_PKG)
                time.sleep(2.2)
            continue
        if st in ("SYS_SETTINGS",):
            _recover_from_sys_settings(CURRENT_PKG, label="home-feed")
            continue
        if st == "SEARCH_TAB" or _is_search_explore_tab(xml):
            if _tap_home_tab(xml):
                time.sleep(1.5)
                continue
            for _ in range(3):
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(0.45)
            continue
        if _tap_home_tab(xml):
            time.sleep(1.5)
            continue
        for _ in range(2):
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.45)
        pkg = CURRENT_PKG or fg_pkg()
        if pkg:
            launch(pkg)
            time.sleep(2.0)
    ok = _on_home_feed_verified(dump())
    if not ok:
        print("[home] FAILED — not on verified Home feed")
    return ok


def _open_ig_create(prefer_dest="feed", reject_composer=False):
    """From feed: open New post / Create. Handles tips + Post vs Story/Reel chooser.

    prefer_dest: feed | story | reel — which chooser tile to tap when CREATE_CHOOSER.
    reject_composer: if True, do not treat leftover CAPTION/EDIT as success
                     (hard recover after stale abort — hankoac542 2026-08-04).
    """
    prefer_dest = (prefer_dest or "feed").lower()
    if prefer_dest == "carousel":
        prefer_dest = "feed"
    # NekoBox / other apps sometimes steal FG (create_timeout dump 2026-08-03)
    if not _ensure_ig_foreground(CURRENT_PKG, hard=True):
        print("[create] abort — Instagram not foreground")
        return False
    xml0 = dump()
    if _on_create_surface(xml0):
        print("[create] already on %s — skip Home reset" % detect_state(xml0))
        if detect_state(xml0) == "CREATE_CHOOSER":
            _open_dest_gallery(prefer_dest=prefer_dest)
        return True
    if _on_own_profile(xml0):
        print("[create] own profile — camera roll now (no Home)")
        if _tap_ig_create_button(xml0) or _tap_top_create(xml0):
            time.sleep(0.55)
            if _on_create_surface():
                return True
        sw, sh = _screen_wh(xml0)
        tap(int(sw * 0.93), int(sh * 0.07))
        print("[create] profile + coord")
        time.sleep(0.55)
        if _on_create_surface():
            return True
        print("[create] + miss from profile — one more tap")
        tap(int(sw * 0.50), int(sh * 0.95))
        time.sleep(0.45)
        if _on_create_surface():
            return True
    if not _ensure_on_home_feed():
        print("[create] abort — could not reach Home feed before Create")
        return False
    dumped = False
    _tap_ig_create_button._dumped = False
    _tap_top_create._dumped = False
    _open_ig_create._coord2 = False
    intent_tried = False
    profile_tried = False
    unknown_n = 0
    sys_perm_n = 0
    for step in range(10):  # fail-fast — escalate intent, don't spam 14 fake taps
        if _section_expired(need=1.5):
            xml_b = dump()
            if _on_create_surface(xml_b):
                print("[create] budget but picker/chooser is up — continue")
                return True
            print("[create] budget — stop open")
            return False
        xml = dump()
        st = detect_state(xml)
        tb0 = text_block(xml)
        print("[create-step %02d] state=%s prefer=%s" % (step, st, prefer_dest))
        # Leftover Reel draft blocks create (nergis/kadriye 2026-08-10)
        if _is_draft_exit_sheet(tb0):
            print("[create] draft-exit sheet on open")
            _clear_create_draft_gate(dest=prefer_dest, label="create-draft")
            time.sleep(0.8)
            continue
        if st == "WRONG_APP" or st == "SYS_SETTINGS" or _is_wrong_app_xml(xml):
            if st == "SYS_SETTINGS" or _is_sys_settings_xml(xml):
                _recover_from_sys_settings(CURRENT_PKG, label="create-settings")
            elif not _ensure_ig_foreground(CURRENT_PKG, hard=True):
                return False
            time.sleep(1.0); continue
        if st == "ANDROID_SHARE":
            _handle_android_share_sheet(xml, pkg=CURRENT_PKG)
            time.sleep(1.5); continue
        # Mid-create: another app took FG
        if step > 0 and step % 4 == 0:
            _ensure_ig_foreground(CURRENT_PKG, hard=True)
        if st in ("CREATE_PICKER", "CREATE_CAMERA"):
            if _is_draft_exit_sheet(text_block(xml)):
                _clear_create_draft_gate(dest=prefer_dest, label="create-picker-draft")
                time.sleep(0.8)
                continue
            # Feed jobs must not stay on leftover REEL tab
            if prefer_dest == "feed":
                _ensure_create_dest(xml, dest="feed", force=True)
                time.sleep(0.5)
            elif prefer_dest == "reel":
                _ensure_create_dest(xml, dest="reel", force=False)
            return True
        if st in ("EDIT_SCREEN", "CAPTION_SCREEN"):
            if reject_composer:
                print("[create] reject stale composer (state=%s) — back out" % st)
                _back_out_of_composer(max_backs=4, label="create-reject")
                if detect_state(dump()) in ("EDIT_SCREEN", "CAPTION_SCREEN"):
                    _ensure_on_home_feed()
                time.sleep(0.8)
                continue
            return True
        if st in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE"):
            print("[create] blocked by %s — abort create" % st)
            return False
        if st == "CREATE_CHOOSER":
            tapped = False
            if prefer_dest == "reel":
                tapped = tap_exact(xml, "Reel", "REEL", "Reels", label="create-chooser-reel")
            elif prefer_dest == "story":
                tapped = tap_exact(xml, "Story", "STORY", label="create-chooser-story")
            else:
                tapped = _tap_chooser_post_exact(xml)
            if not tapped:
                if not dumped:
                    dumped = True
                    print("[create-chooser] %s" % text_block(xml)[:280])
                    _dump_chooser_once(xml)
                print("[create-chooser] no exact dest — BACK to feed")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(2); continue
        if st == "SYS_PERMISSION":
            sys_perm_n += 1
            dismiss_android_permission()
            if sys_perm_n >= 2:
                print("[create] SYS_PERMISSION stuck — BACK and try create on FEED")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(1.0)
                _ensure_on_home_feed()
            time.sleep(1.0); continue
        if st in ("TIP_SHEET", "ONBOARD_CARD", "NOTIF_PROMPT", "SAVE_INFO"):
            if st == "SAVE_INFO":
                tap_exact(xml, "Not now", "Not Now", label="create-skip-save")
            elif st == "NOTIF_PROMPT":
                _dismiss_notif_prompt(xml, label="create-notif")
                if _is_sys_settings_xml() or _is_wrong_app_xml():
                    _recover_from_sys_settings(CURRENT_PKG, label="create-notif-settings")
            else:
                dismiss_tip_sheet(xml, label="create-dismiss-tip", allow_next=False)
            time.sleep(1.5); continue
        if st in ("SYS_SETTINGS", "WRONG_APP") or _is_sys_settings_xml(xml):
            _recover_from_sys_settings(CURRENT_PKG, label="create-settings")
            time.sleep(1.5); continue
        if st == "UNKNOWN":
            unknown_n += 1
            if unknown_n == 1:
                print("[create-unknown] %s" % text_block(xml)[:320])
                print("[create-unknown] clickables:")
                for n in nodes(xml):
                    if attr(n, "clickable") != "true":
                        continue
                    cx, cy = bounds_center(n)
                    print("   %s,%s text=%r desc=%r"
                          % (cx, cy, attr(n, "text")[:30], attr(n, "content-desc")[:40]))
            if _is_sys_settings_xml(xml) or _is_wrong_app_xml(xml):
                _recover_from_sys_settings(CURRENT_PKG, label="create-unknown-settings")
                time.sleep(1.5)
                continue
            # Create tap leaked to Samsung home (arzu16380 2026-08-11 Super Proxy / Camera)
            launcher_marks = ("super proxy", "surfshark", "play store", "messaging",
                              "contacts", "browser")
            tb_u = text_block(xml).lower()
            if sum(1 for m in launcher_marks if m in tb_u) >= 2:
                print("[create] home launcher after Create — relaunch clone")
                if CURRENT_PKG:
                    launch(CURRENT_PKG)
                    time.sleep(2.5)
                continue
            if is_tip_sheet(xml):
                dismiss_tip_sheet(xml, label="create-unknown-tip")
                time.sleep(1.5); continue
            # Prefer real Create (e.g. desc='Create a reel') before bottom Post/OK
            if _tap_top_create(xml):
                time.sleep(2)
                if _create_success():
                    return True
                continue
            n_aff = _find_create_affordance(xml)
            if n_aff and tapn(n_aff, "create-unknown-affordance"):
                time.sleep(2)
                if _create_success():
                    return True
                continue
            if tap_exact(xml, "Post", "New post", "Next", "Got it", "Not now",
                         "Continue", "OK", label="create-unknown-cta"):
                time.sleep(2)
                if _create_success():
                    return True
                continue
            if unknown_n >= 2:
                print("[create] UNKNOWN recover → BACK + Home")
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(1.0)
                _ensure_on_home_feed()
            if unknown_n >= 4 and not intent_tried:
                intent_tried = True
                if _try_create_intent():
                    continue
            if unknown_n >= 6:
                print("[create] giving up after UNKNOWN loop")
                return False
            time.sleep(STEP_PAUSE); continue
        else:
            unknown_n = 0

        if st == "FEED":
            # aerinaa70: don't spam fake Create taps — reveal bar, then intent early
            if step == 0:
                _reveal_feed_action_bar()
                xml = dump()
            ok = _tap_ig_create_button(xml)
            if ok:
                time.sleep(STEP_PAUSE + 0.5); continue
            if step == 1:
                _reveal_feed_action_bar()
                if _tap_create_coords() or _tap_top_create(dump()):
                    time.sleep(STEP_PAUSE + 0.5); continue
            if not intent_tried and step >= 1:
                intent_tried = True
                print("[create] escalate to intent (UI Create not opening)")
                if _try_create_intent():
                    continue
            if not profile_tried and step >= 2:
                profile_tried = True
                print("[create] try Profile tab then Create")
                _tap_profile_tab(); time.sleep(2.0)
                xml2 = dump()
                if _tap_top_create(xml2) or _tap_ig_create_button(xml2):
                    time.sleep(2); continue
                _ensure_on_home_feed()
            if step >= 3 and not getattr(_open_ig_create, "_coord2", False):
                _open_ig_create._coord2 = True
                if _tap_create_coords():
                    continue
            print("[create] no create control found this step")
            time.sleep(STEP_PAUSE); continue

        if not dumped:
            dumped = True
            print("[create] unexpected: %s" % text_block(xml)[:240])
        time.sleep(STEP_PAUSE)
    _open_ig_create._coord2 = False
    return False


def _advance_next(xml=None):
    """Tap the rightmost exact Next (picker/edit). Prefer next_button resource-id."""
    xml = xml or dump()
    # Resource-id first (picker dump: next_button_textview)
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        rid = attr(n, "resource-id").lower()
        if "next_button" in rid:
            return tapn(n, "next-rid")
    best = None
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t == "next" or d == "next":
            x, y = bounds_center(n)
            if x is not None and (best is None or x > best[0]):
                best = (x, n)
    if best:
        return tapn(best[1], "next")
    return tap_first(xml, "Next", "Done", "OK", label="next")


def _dismiss_picker_gallery_prompt(xml=None):
    """IG 'Turn on' / save-to-gallery sheet over the picker (bagjaesin283 2026-07-29).

    Blocks Next until dismissed. Prefer Turn on (enables gallery) over Cancel.
    """
    xml = xml or dump()
    tb = text_block(xml)
    if "turn on" not in tb and "automatically save" not in tb and \
       "photos from your phone" not in tb:
        return False
    if tap_exact(xml, "Turn on", "Turn On", "Allow", "OK", "Got it",
                 label="picker-gallery-on"):
        time.sleep(1.5)
        return True
    return False


def _dump_picker_once(xml, force=False):
    if getattr(_dump_picker_once, "_done", False) and not force:
        return
    _dump_picker_once._done = True
    print("[picker] stuck dump text: %s" % text_block(xml)[:280])
    print("[picker] clickables:")
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        cx, cy = bounds_center(n)
        print("   %s,%s text=%r desc=%r id=%s"
              % (cx, cy, attr(n, "text")[:24], attr(n, "content-desc")[:48],
                 (attr(n, "resource-id") or "")[-40:]))


def in_caption_screen(xml):
    tb = text_block(xml)
    if any(p in tb for p in ["write a caption", "add a caption", "caption…", "caption...",
                               "tag people", "add location", "also share on"]):
        return True
    # Caption affordance may be content-desc only (no EditText yet)
    for n in nodes(xml):
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        if "write a caption" in d or "add a caption" in t or t.startswith("add a caption"):
            return True
    if len(edits(xml)) >= 1 and any(p in tb for p in ("share", "ok", "post")) and \
       "log in" not in tb:
        return True
    return False


def _focus_caption_field(xml=None):
    """Tap caption field only — never Add audio / music row (opens track sheet).

    New post often uses a non-clickable 'Add a caption…' TextView — tap bounds anyway.
    """
    xml = xml or dump()
    if _is_edit_cover_screen(xml):
        _dismiss_edit_cover(xml, label="focus-cover")
        xml = dump()
    if _is_audio_picker_overlay(xml):
        _dismiss_audio_picker()
        xml = dump()
    es = edits(xml)
    if es:
        tapn(es[0], "caption-edit"); time.sleep(0.6)
        return True
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "caption_input" not in rid and "caption_text" not in rid:
            continue
        w, h = bounds_wh(n)
        # Huge bounds = photo preview container, not the caption row
        # (nila/aylin 2026-08-11: tap @720,1140 opened Preview overlay).
        if h and h > 280:
            continue
        tapn(n, "caption-input"); time.sleep(0.8)
        return True
    best = None
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        blob = (t + " " + d).strip()
        if "edit cover" in blob or "cover photo" in blob:
            continue
        if "audio" in rid or "music" in rid or "track" in rid:
            continue
        if "add audio" in blob or "select track" in blob:
            continue
        if "write a caption" in d or "add a caption" in t or t.startswith("add a caption") \
           or "caption…" in t or "caption..." in t or t.endswith("caption…") \
           or t.endswith("caption..."):
            x, y = bounds_center(n)
            if x is None:
                continue
            w, h = bounds_wh(n)
            if h and h > 280:
                continue
            # Prefer short caption row (not preview). Mid-screen Y is typical.
            score = y if y is not None else 9999
            if best is None or score < best[0]:
                best = (score, n)
    if best:
        tapn(best[1], "caption-affordance"); time.sleep(0.8)
        return True
    return False


def _caption_looks_filled(xml=None, want=""):
    """True if caption EditText/placeholder no longer empty-only."""
    xml = xml or dump()
    want = (want or "").strip()
    tb = text_block(xml).lower()
    if want and want[:24].lower() in tb:
        return True
    for n in edits(xml):
        t = attr(n, "text").strip()
        tl = t.lower()
        if not t:
            continue
        if tl.startswith("add a caption") or tl.startswith("write a caption"):
            continue
        if len(t) >= 2:
            return True
    return False


def _apply_caption(caption, fmt="feed"):
    """Focus, type human-like, leave. Share still runs if field looks empty."""
    caption = (caption or "").strip()
    if not caption or fmt == "story":
        return False
    if _section_expired(need=8):
        print("[caption] skip type — composer budget gone")
        return False
    focused = _focus_caption_field()
    xml = dump()
    es = edits(xml)
    if es:
        tapn(es[0], "caption-field")
        human_pause(0.2, 0.4)
        clear_field()
        _type_caption_human(caption)
    elif focused:
        print("[caption] no EditText — typing into focused field")
        _type_caption_human(caption)
    else:
        sw, sh = _screen_wh(xml)
        tap(sw // 2, int(sh * 0.52))
        human_pause(0.25, 0.45)
        print("[caption] blind focus @ caption-row then type")
        _type_caption_human(caption)
    xml2 = dump()
    if _is_media_preview_overlay(xml2):
        print("[caption] opened Preview — Back")
        _dismiss_media_preview(xml2)
        xml2 = dump()
    filled = _caption_looks_filled(xml2, want=caption)
    print("[caption] focused=%s filled=%s edits=%s"
          % (focused, filled, [attr(n, "text")[:40] for n in edits(xml2)]))
    if filled:
        return True
    print("[caption] WARN empty after type — Share anyway (no retry sit)")
    return False


def _tap_chooser_post_exact(xml):
    """Tap ONLY exact Post / New post on create chooser — never substring 'Post'.

    2026-07-24: tap_first('Post') hit x=315 forever (advance-chooser-post-sub loop).
    """
    ban = ("story", "reel", "live", "options", "draft")
    best = None
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        blob = (t + " " + d).strip()
        if any(b in blob for b in ban) and blob not in ("post", "new post"):
            continue
        if t in ("post", "new post") or d in ("post", "new post"):
            x, y = bounds_center(n)
            if x is None:
                continue
            # Prefer more central tiles over far-left junk
            score = -abs(x - 720) - abs((y or 1500) - 1600)
            if best is None or score > best[0]:
                best = (score, n, t or d)
    if best:
        return tapn(best[1], "chooser-post-exact:%s" % best[2][:20])
    return False


def _dump_chooser_once(xml):
    if getattr(_dump_chooser_once, "_done", False):
        return
    _dump_chooser_once._done = True
    print("[chooser] stuck dump text: %s" % text_block(xml)[:300])
    print("[chooser] clickables:")
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        cx, cy = bounds_center(n)
        print("   %s,%s text=%r desc=%r"
              % (cx, cy, attr(n, "text")[:28], attr(n, "content-desc")[:40]))



def _advance_to_caption(dest="feed", carousel=False, pick_count=1):
    """EDIT / picker / chooser → caption (or story share-ready).

    dest: feed | story | reel
    carousel: multi-select N photos then Next
    """
    unknown_dumped = False
    photo_picked = False
    picker_next_tries = 0
    chooser_tries = 0
    send_bypass_tried = False
    gallery_opened = False
    gallery_tries = 0
    _dump_picker_once._done = False
    _dump_chooser_once._done = False
    _ensure_create_dest._done = None
    _ensure_create_feed_dest._done = False
    _tap_picker_next._logged = False
    dest = (dest or "feed").lower()
    if dest == "carousel":
        dest, carousel = "feed", True
    prefer_video = dest == "reel" or _remote_is_video()
    if _SECTION is None:
        _section_begin("create")
    max_steps = 10 if dest == "reel" else 8
    tip_stuck = 0
    story_done_stuck = 0
    _advance_to_caption._draft_tries = 0
    _advance_to_caption._story_edit_dumped = False

    for step in range(max_steps):
        if _section_expired(need=1.5):
            print("[post] create budget — leave advance")
            return False
        xml = dump()
        st = detect_state(xml)
        nedit = len(edits(xml))
        tb = text_block(xml)
        print("[post] advance %02d state=%s edits=%d picked=%s dest=%s"
              % (step, st, nedit, photo_picked, dest))
        if dest == "reel":
            fields = dict(step=step, state=st, picked=photo_picked,
                          gallery_opened=gallery_opened, picker_next_tries=picker_next_tries,
                          nedit=nedit, snippet=tb)
            if st == "CREATE_PICKER":
                fields.update(_gallery_trace_summary(xml))
            _rt_log("advance", **fields)

        if st == "WRONG_APP" or st == "SYS_SETTINGS" or _is_wrong_app_xml(xml):
            if st == "SYS_SETTINGS" or _is_sys_settings_xml(xml):
                if not _recover_from_sys_settings(CURRENT_PKG, label="advance-settings"):
                    print("[FAIL] SYS_SETTINGS during advance")
                    return False
            elif not _ensure_ig_foreground(CURRENT_PKG, hard=True):
                print("[FAIL] WRONG_APP during advance")
                return False
            time.sleep(1.0); continue

        # Leftover draft sheet blocks pick/Next (kadriyeaydin8638 2026-08-10)
        if _is_draft_exit_sheet(tb):
            if not getattr(_advance_to_caption, "_draft_tries", None):
                _advance_to_caption._draft_tries = 0
            _advance_to_caption._draft_tries += 1
            print("[post] draft sheet during advance (try %d)" % _advance_to_caption._draft_tries)
            if not _clear_create_draft_gate(dest=dest, label="advance-draft"):
                print("[FAIL] draft sheet present but dismiss failed")
                return False
            if _advance_to_caption._draft_tries >= 2 and _is_draft_exit_sheet(text_block(dump())):
                print("[FAIL] draft sheet still up after 2 dismiss tries")
                return False
            photo_picked = False
            gallery_opened = False
            continue

        if st == "ANDROID_SHARE" or _is_android_share_sheet(xml, tb):
            _rt_log("android_share", step=step)
            if _handle_android_share_sheet(xml, pkg=CURRENT_PKG):
                time.sleep(2.0)
                continue
            print("[FAIL] stuck on Android share sheet")
            return False

        if st in ("TIP_SHEET", "SYS_PERMISSION", "ONBOARD_CARD", "NOTIF_PROMPT") or \
           _is_reel_create_tip_tb(tb) or _is_preview_size_tip(xml, tb):
            # Story text tool / canvas mislabeled as tip (dorothhds129 2026-08-30).
            if dest == "story" and photo_picked:
                if _is_story_text_tool(xml, tb):
                    print("[post] story text tool — Done (exit Aa)")
                    tap_exact(xml, "Done", label="story-text-done")
                    time.sleep(1.0)
                    continue
                if _story_has_stickers_affordance(xml):
                    print("[post] at story editor (tip misdetect)")
                    return True
            if st == "SYS_PERMISSION":
                dismiss_android_permission()
                tip_stuck = 0
            elif st == "NOTIF_PROMPT":
                _dismiss_notif_prompt(xml, label="post-advance-notif")
                if _is_sys_settings_xml() or _is_wrong_app_xml():
                    _recover_from_sys_settings(CURRENT_PKG, label="post-advance-notif-settings")
                tip_stuck = 0
            else:
                if _is_preview_size_tip(xml, tb):
                    dismiss_preview_size_tip(xml, label="post-advance-preview")
                else:
                    dismiss_tip_sheet(xml, label="post-advance-tip", allow_next=False)
                time.sleep(1.2)
                after_xml = dump()
                after = text_block(after_xml)
                if _is_reel_create_tip_tb(after) or _is_preview_size_tip(after_xml, after):
                    tip_stuck += 1
                    print("[tip] still present after dismiss (%d)" % tip_stuck)
                    if tip_stuck >= 2:
                        # Force Back then try gallery — tip was blocking create
                        adb("shell", "input", "keyevent", "KEYCODE_BACK")
                        time.sleep(1.0)
                        tip_stuck = 0
                        photo_picked = False  # prior pick may have been false progress
                        _open_gallery_from_camera(dump())
                else:
                    tip_stuck = 0
                    # Camera/save tips after a "pick" often mean selection was not
                    # committed — reset. Preview-size tip appears ON edit after a
                    # real pick (siennasky58 2026-08-03) — keep photo_picked.
                    if photo_picked and dest == "reel" and not _is_preview_size_tip(xml, tb):
                        photo_picked = False
            continue

        # Story must never ride leftover Reel edit (hayes 2026-08-27 dump).
        # Do NOT use _is_true_reel_edit here — story camera shares post_capture_* ids
        # and false-loops (shutter / story settings / boomerang).
        if dest == "story":
            tbl = tb.lower()
            story_cam = any(p in tbl for p in (
                "story settings", "boomerang", "shutter", "create mode button",
                "flash off", "flash on", "switch to back camera",
                "switch to front camera"))
            leftover_reel = (
                ("edit video" in tbl and ("reel preview" in tbl or "voiceover" in tbl)) or
                ("reel preview playing" in tbl) or
                (st == "EDIT_SCREEN" and "edit video" in tbl and "add audio" in tbl
                 and not story_cam)
            )
            if leftover_reel and not story_cam:
                print("[post] story: leftover reel edit — abort + reopen story gallery")
                try:
                    _ensure_create_dest._done = None
                except Exception:
                    pass
                photo_picked = False
                gallery_opened = False
                if not _abort_stale_composer(prefer_dest="story"):
                    if not _hard_recover_create("story"):
                        print("[FAIL] cannot leave reel edit for story")
                        return False
                time.sleep(0.8)
                xml_g = dump()
                _ensure_create_dest(xml_g, dest="story", force=True)
                time.sleep(0.6)
                _open_gallery_from_camera(dump())
                time.sleep(1.0)
                continue

        if dest == "story":
            _hide_ime()
            tbls = tb.lower()
            if photo_picked and (_story_has_stickers_affordance(xml) or
                                 _is_story_asset_edit(xml, tb)):
                print("[post] at story editor (share-ready)")
                return True
            if photo_picked and _is_story_text_tool(xml, tb):
                story_done_stuck += 1
                print("[post] story text tool — Done (exit Aa) try=%d" % story_done_stuck)
                tap_exact(xml, "Done", label="story-text-done")
                human_pause(0.35, 0.75)
                if story_done_stuck >= 3:
                    if _story_has_stickers_affordance(dump()) or \
                       _is_story_asset_edit(dump()):
                        print("[post] story editor ready after Done tries")
                        return True
                continue
            story_done_stuck = 0
            xml = dump()
            tb = text_block(xml)
            tbls = tb.lower()
            if photo_picked and (_story_has_stickers_affordance(xml) or
                                 _is_story_asset_edit(xml, tb)):
                print("[post] at story editor (share-ready)")
                return True
            # Do NOT tap Next on Audio/Text/Overlay tray — it opens Aa text tool.
            # Only final share chrome — never Edit Next / Done.
            if st not in ("CREATE_PICKER", "CREATE_CHOOSER", "EDIT_SCREEN") or \
               "your story" in tbls or "add to your story" in tbls:
                if tap_exact(xml, "Your story", "Add to your story", "Share to story",
                             "Share", label="story-share-early") or \
                   _tap_publish_cta(xml, fmt="story", label="story-share-early"):
                    time.sleep(3.0)
                    st2 = detect_state(dump())
                    if st2 not in ("CREATE_PICKER", "CREATE_CHOOSER", "EDIT_SCREEN"):
                        return True
                    if "your story" not in text_block(dump()).lower():
                        # Left edit — likely shared / on feed
                        if st2 in ("FEED",):
                            return True
            if "add to story" in tbls or ("your story" in tbls and photo_picked and
                    st not in ("CREATE_PICKER", "CREATE_CHOOSER")):
                if tap_exact(xml, "Your story", "Add to your story", "Share",
                             label="story-share2") or \
                   _tap_publish_cta(xml, fmt="story", label="story-share2") or \
                   tap_share_btn(xml):
                    time.sleep(2.5)
                    st2 = detect_state(dump())
                    if st2 not in ("CREATE_PICKER", "CREATE_CHOOSER"):
                        return True

        # Caption: for reel require real caption chrome (not music search EditText)
        # Stale leftover composer without a pick — abort for ALL formats
        if st == "CAPTION_SCREEN" or in_caption_screen(xml):
            if dest == "story" and photo_picked:
                print("[post] story: CAPTION_SCREEN misdetect after pick — continue")
                continue
            if not photo_picked:
                # Soft-retry / budget-exit mid-Next can land here with picked=False
                # while this IS the reel we just selected (edit cover + write caption).
                tbl = (tb or "").lower()
                if dest == "reel" and ("edit cover" in tbl or "write a caption" in tbl) and (
                        gallery_opened or "hashtags" in tbl or "link a reel" in tbl):
                    print("[post] at caption after pick (flag lost) — accept")
                    return True
                print("[post] caption without pick — abort stale composer dest=%s" % dest)
                if _abort_stale_composer(prefer_dest=dest):
                    photo_picked = False
                    gallery_opened = False
                    continue
                print("[FAIL] cannot leave stale caption")
                return False
            print("[post] at caption (edits=%d)" % nedit)
            return True
        if dest == "reel":
            if _reel_at_caption(xml):
                if not photo_picked:
                    tbl = (tb or "").lower()
                    if "edit cover" in tbl or "write a caption" in tbl:
                        print("[post] at reel caption after pick (flag lost)")
                        return True
                    print("[post] reel-caption chrome without pick — abort stale")
                    if _abort_stale_composer(prefer_dest="reel"):
                        photo_picked = False
                        gallery_opened = False
                        continue
                    return False
                print("[post] at reel caption")
                return True
            # EDIT without pick is also stale (prior job) — NEVER on CREATE_PICKER
            # (velorastar7: "new reel" title made _reel_edit_ready true → abort loop)
            if st == "CREATE_PICKER":
                pass  # fall through to picker pick path below
            elif st == "EDIT_SCREEN" and not photo_picked:
                print("[post] EDIT without pick — abort stale composer")
                if _abort_stale_composer(prefer_dest="reel"):
                    photo_picked = False
                    gallery_opened = False
                    continue
                return False
            elif _reel_edit_ready(xml) and not photo_picked and st not in (
                    "CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER"):
                print("[post] reel-edit chrome without pick — abort stale")
                if _abort_stale_composer(prefer_dest="reel"):
                    photo_picked = False
                    gallery_opened = False
                    continue
                return False
        elif nedit >= 1:
            if dest == "story" and photo_picked:
                print("[post] story: EditText misdetect (edits=%d) — continue" % nedit)
                continue
            if not photo_picked and dest in ("feed", "story"):
                print("[post] edits without pick — abort stale dest=%s" % dest)
                if _abort_stale_composer(prefer_dest=dest):
                    photo_picked = False
                    gallery_opened = False
                    continue
            print("[post] at caption (edits=%d)" % nedit)
            return True

        # Reel camera — open gallery (even if a prior false pick set photo_picked)
        if dest == "reel" and st == "CREATE_CAMERA":
            if photo_picked:
                print("[post] CREATE_CAMERA after pick — treat as tip/camera, reset pick")
                photo_picked = False
            if _open_gallery_from_camera(xml):
                gallery_opened = True
                gallery_tries += 1
                _rt_log("gallery_open", step=step, cta="gallery", ok=True)
                time.sleep(1.2)
                continue
            gallery_tries += 1
            _rt_log("gallery_open", step=step, ok=False, tries=gallery_tries)
            if tap_exact(xml, "Gallery", "Library", "Recents", "Camera roll",
                         label="cam-gallery-cta"):
                time.sleep(1.5)
                continue
            time.sleep(1.0)
            if gallery_tries >= 8:
                print("[FAIL] CREATE_CAMERA — gallery never opened")
                return False
            continue

        # Story camera only — never when Recents grid is already open (remote/mirror runs).
        if dest == "story" and st == "CREATE_CAMERA" and not _gallery_grid_visible(xml):
            print("[post] story camera — open gallery for pick")
            _ensure_create_dest(xml, dest="story", force=False)
            if _open_gallery_from_camera(dump()):
                gallery_opened = True
                gallery_tries += 1
                time.sleep(1.2)
                continue
            gallery_tries += 1
            if tap_exact(dump(), "Gallery", "Library", "Recents", "Camera roll",
                         label="story-cam-gallery"):
                time.sleep(1.5)
                continue
            if gallery_tries >= 8:
                print("[FAIL] story camera — gallery never opened")
                return False
            time.sleep(1.0)
            continue

        if st == "CREATE_CHOOSER":
            if photo_picked and any(p in tb for p in ("also share", "share to story", "your story")):
                if tap_exact(xml, "Not now", "Skip", "Cancel", "Done", label="skip-also-share"):
                    time.sleep(1.5)
                    if detect_state(dump()) in ("FEED", "CAPTION_SCREEN") or in_caption_screen(dump()):
                        return detect_state(dump()) != "CREATE_CHOOSER"
                    continue
            chooser_tries += 1
            if photo_picked:
                if _advance_next(xml):
                    time.sleep(2.0); continue
                if dest == "feed" and _tap_chooser_post_exact(xml):
                    time.sleep(2.0); continue
                if dest == "reel" and tap_exact(xml, "Reel", "REEL", "Reels",
                                                label="chooser-reel-after-pick"):
                    time.sleep(2.0); continue
            else:
                if dest == "reel" and tap_exact(xml, "Reel", "REEL", "Reels",
                                                label="chooser-open-reel"):
                    time.sleep(2.0); continue
                if dest == "story" and tap_exact(xml, "Story", "STORY",
                                                  label="chooser-open-story"):
                    time.sleep(2.0); continue
                if dest == "feed" and _tap_chooser_post_exact(xml):
                    time.sleep(2.0); continue
                if _advance_next(xml):
                    time.sleep(2.0); continue
            if chooser_tries == 1:
                _dump_chooser_once(xml)
            if chooser_tries >= 4:
                print("[FAIL] CREATE_CHOOSER stuck after pick=%s" % photo_picked)
                return False
            time.sleep(1.5); continue

        if st == "CREATE_PICKER":
            # CRITICAL: do NOT force-tap REEL every step — that clears video selection
            # (2026-08-03: force=True each advance → pick_lost forever).
            if dest == "story" and (
                    "edit video" in text_block(xml).lower() and
                    "reel preview" in text_block(xml).lower()):
                print("[post] CREATE_PICKER misdetect — actually reel edit")
                continue  # handled by story leftover recover above next loop
            _ensure_create_dest(xml, dest=dest, force=False)
            time.sleep(0.5)
            xml = dump()
            st = detect_state(xml)
            if dest == "story":
                tbl2 = text_block(xml).lower()
                if "edit video" in tbl2 and "reel preview" in tbl2:
                    continue  # leftover recover on next iteration
                if not _gallery_grid_visible(xml):
                    _open_gallery_from_camera(xml)
                    time.sleep(1.0)
                    xml = dump()
            if dest == "reel":
                # One-time ensure REEL is selected if not already
                reel_sel = False
                for n in nodes(xml):
                    if attr(n, "resource-id").lower().endswith("cam_dest_clips") or \
                       (attr(n, "text").strip().upper() == "REEL" and
                        attr(n, "clickable") == "true"):
                        if attr(n, "selected").lower() == "true":
                            reel_sel = True
                        break
                if not reel_sel and not photo_picked:
                    _ensure_create_dest(xml, dest="reel", force=True)
                    xml = dump()
            if _dismiss_picker_gallery_prompt(xml):
                xml = dump()
            gsum = _gallery_trace_summary(xml)
            if dest == "reel":
                _rt_log("picker_view", step=step, picked=photo_picked, **gsum)

            # Reel: require a video tile before picking
            if dest == "reel" and not photo_picked:
                if not _gallery_has_video_tile(xml):
                    print("[picker] no video tiles — wait/scan/folder")
                    if not _wait_for_gallery_video(max_rounds=4):
                        print("[FAIL] no video in gallery (no SEND without tiles)")
                        _rt_log("no_video_abort", **_gallery_trace_summary())
                        return False
                    xml = dump()

            if not photo_picked:
                if carousel:
                    if not pick_n_photos(max(2, pick_count)):
                        print("[post] carousel pick failed")
                        return False
                    photo_picked = True
                elif dest == "reel" or prefer_video:
                    ok_pick, why = _reel_pick_succeeded(xml)
                    if ok_pick:
                        print("[img] reel already ready (%s)" % why)
                        _rt_log("pick_already_true", reason=why, **gsum)
                    else:
                        ok_pick = pick_reel_video(max_attempts=8)
                        if not ok_pick:
                            print("[post] reel pick failed after multi-attempt")
                            return False
                    # Accept Next-appeared as success even if desc still Unselected
                    ok2, why2 = _reel_pick_succeeded(dump())
                    if not ok2:
                        print("[post] FAIL: pick returned but picker not ready")
                        _rt_log("pick_lie", **_gallery_trace_summary())
                        return False
                    _rt_log("pick_ready", reason=why2, **_gallery_trace_summary())
                    photo_picked = True
                    # Fresh budget for Next → edit → caption (Nylah 2026-09-04:
                    # pick ok then create STOP mid-Next left CAPTION as "stale").
                    _section_begin("create", 50)
                else:
                    selected = False
                    tb0 = text_block(xml)
                    if "photo preview" in tb0 or "video preview" in tb0:
                        selected = True
                    else:
                        for n in nodes(xml):
                            d = attr(n, "content-desc").lower()
                            if attr(n, "clickable") != "true" or not _desc_is_selected(d):
                                continue
                            if "photo" in d or "video" in d or "image" in d:
                                selected = True
                                break
                    if not selected:
                        ok_pick = pick_newest_photo(prefer_video=False)
                        if not ok_pick:
                            print("[post] picker has no usable media")
                            return False
                    photo_picked = True
                gallery_opened = True
                time.sleep(1.0)
                xml = dump()
                if dest == "reel":
                    _rt_log("picker_after_pick", **_gallery_trace_summary(xml))
                    if not _tap_picker_next(xml, allow_coord=False):
                        picker_next_tries += 1
                        print("[picker] no Next yet after pick — wait (try %d)"
                              % picker_next_tries)
                        time.sleep(2.0)
                        continue
                    time.sleep(3.5)
                    continue
            else:
                # Already marked picked — for reel, re-verify without re-tapping REEL
                if dest == "reel":
                    ok_r, why_r = _reel_pick_succeeded(xml)
                    if not ok_r:
                        print("[picker] lost reel readiness — re-pick (no dest force)")
                        _rt_log("pick_lost", **gsum)
                        photo_picked = False
                        continue
                picker_next_tries += 1
                cands = _next_button_nodes(xml)
                enabled = [c for c in cands if c[4] in ("", "true")]
                if not enabled and picker_next_tries < 5:
                    print("[picker] Next not enabled yet — wait (%d)" % picker_next_tries)
                    time.sleep(2.0)
                    continue
                if picker_next_tries >= 2:
                    _dump_picker_once(xml)
                # SEND only after true ready + Next still missing
                if (dest == "reel" and picker_next_tries >= 5 and not send_bypass_tried
                        and _reel_pick_succeeded(xml)[0]):
                    send_bypass_tried = True
                    _rt_log("send_bypass", step=step, mime=_media_mime(),
                            reason="next_missing_after_select", **_gallery_trace_summary(xml))
                    if _bypass_picker_via_send():
                        st2 = detect_state(dump())
                        _rt_log("send_bypass_result", state=st2)
                        if st2 in ("EDIT_SCREEN", "CAPTION_SCREEN") or _reel_at_caption(dump()) \
                           or in_caption_screen(dump()):
                            return True
                        if st2 in ("CREATE_CHOOSER", "ANDROID_SHARE"):
                            continue
                elif dest != "reel" and picker_next_tries >= 3 and not send_bypass_tried \
                        and not carousel:
                    send_bypass_tried = True
                    _rt_log("send_bypass", step=step, mime=_media_mime())
                    if _bypass_picker_via_send():
                        st2 = detect_state(dump())
                        if st2 in ("EDIT_SCREEN", "CAPTION_SCREEN") or in_caption_screen(dump()):
                            return True
                if picker_next_tries >= (12 if dest == "reel" else 6):
                    print("[FAIL] CREATE_PICKER Next never advanced after pick")
                    _rt_log("picker_next_fail", **_gallery_trace_summary(xml))
                    return False
                if not _tap_picker_next(xml, allow_coord=(dest != "reel")):
                    time.sleep(1.5)
                    continue
                time.sleep(3.5 if dest == "reel" else 3.0)
                st_after = detect_state(dump())
                if st_after == "CREATE_PICKER" and photo_picked:
                    print("[picker] still picker after Next — double-tap Next")
                    _tap_picker_next(dump(), allow_coord=(dest != "reel"))
                    time.sleep(2.5 if dest == "reel" else 2.0)
                continue
            # feed/carousel path after first pick
            _tap_picker_next(xml, allow_coord=True)
            time.sleep(3.0)
            continue

        if st == "EDIT_SCREEN" or (dest == "reel" and _reel_edit_ready(xml)):
            photo_picked = True
            chooser_tries = 0
            if dest == "story":
                if _story_has_stickers_affordance(xml) or _is_story_asset_edit(xml, tb):
                    print("[post] at story editor (EDIT_SCREEN)")
                    return True
                if _is_story_text_tool(xml, tb):
                    print("[post] story text tool — Done (exit Aa)")
                    tap_exact(xml, "Done", label="story-text-done")
                    human_pause(0.35, 0.75)
                    continue
                if _story_has_stickers_affordance(xml):
                    print("[post] at story editor (EDIT_SCREEN)")
                    return True
                # Dump once so we can map Stickers / Your story if missing
                if not getattr(_advance_to_caption, "_story_edit_dumped", False):
                    _advance_to_caption._story_edit_dumped = True
                    print("[post-story-edit] %s" % tb[:400])
                # Single-photo story: NEVER tap Next (opens Aa text tool).
                # Multi-asset only uses Next when "add more photos" / ratio chrome.
                if ("add more photos" in tb.lower() or "ratio" in tb.lower()) and \
                   ("next" in tb.lower()):
                    if _advance_next(xml) or tap_exact(xml, "Next", label="story-edit-next"):
                        time.sleep(2.0)
                else:
                    # Nudge: try Stickers / Your story exact once
                    if tap_exact(xml, "Stickers", "Add sticker", "Your story",
                                 "Add to your story", label="story-canvas-cta"):
                        time.sleep(1.2)
                        if _story_has_stickers_affordance(dump()) or \
                           _is_story_editor_chrome(dump()):
                            return True
                    time.sleep(1.0)
                continue
            if dest == "reel":
                _rt_log("reel_edit", step=step, snippet=tb)
                _dismiss_reel_overlays(xml)
                xml = dump()
            if tap_exact(xml, "Next", "OK", "Done", "Continue", label="reel-edit-next") or \
               _advance_next(xml):
                time.sleep(2.5 if dest == "reel" else 2.0)
            else:
                # Toast-only Add audio: dismiss then try Next again
                if dest == "reel" and "add audio" in tb:
                    _dismiss_reel_overlays(dump())
                    tap_exact(dump(), "Next", "OK", "Done", "Continue",
                              label="reel-edit-next2")
                time.sleep(1.5)
            continue

        if st == "UNKNOWN":
            if _is_preview_size_tip(xml, tb) or ("got it" in tb and "preview" in tb):
                if dismiss_preview_size_tip(xml, label="unknown-preview"):
                    tip_stuck = 0
                    continue
            if _reel_at_caption(xml) or in_caption_screen(xml):
                print("[post] UNKNOWN is caption chrome → treat as caption")
                return True
            if dest == "reel" and _dismiss_reel_overlays(xml):
                continue
            if dest == "reel" and _reel_edit_ready(xml):
                photo_picked = True
                if tap_exact(xml, "Next", "OK", "Done", "Continue", label="reel-unknown-next"):
                    time.sleep(2.5)
                    continue
            if dest == "reel" and not photo_picked and gallery_tries < 6:
                if _open_gallery_from_camera(xml):
                    gallery_opened = True
                    gallery_tries += 1
                    continue
            if not unknown_dumped:
                unknown_dumped = True
                print("[post-unknown] %s" % tb[:320])
            # Feed only: nedit alone; reel needs caption phrases
            if dest != "reel" and nedit >= 1:
                return True
            # Preview tip / sparse Got it must be before generic Next (Next absent here)
            if tap_exact(xml, "Got it", "OK", "Done", "Next", "Continue",
                         label="post-unknown-cta"):
                time.sleep(2.0); continue
            if dest == "feed" and _tap_chooser_post_exact(xml):
                time.sleep(2.0); continue
            if dest == "reel" and tap_exact(xml, "Reel", "REEL", label="unknown-reel"):
                time.sleep(2.0); continue
            if "next" in tb:
                _advance_next(xml); time.sleep(2.0); continue
            time.sleep(1.5); continue

        if st == "FEED":
            print("[post] dropped back to FEED during advance")
            return False

        if "next" in tb or (dest == "reel" and "ok" in tb):
            _advance_next(xml); time.sleep(2.0); continue
        time.sleep(1.5)

    if dest == "reel":
        return _reel_at_caption(dump()) or in_caption_screen(dump())
    if dest == "story":
        xml_e = dump()
        return _is_story_editor_chrome(xml_e) or in_caption_screen(xml_e)
    return len(edits(dump())) >= 1 or in_caption_screen(dump())


def _escape_to_profile():
    """After share: dismiss overlays, Home if needed, then Profile. Fail-closed."""
    for _ in range(5):
        xml = dump()
        st = detect_state(xml)
        tb = text_block(xml)
        if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE",
                  "ACTION_LIMIT", "ACCOUNT_SUSPENDED"):
            print("[post] escape-to-profile skip — state=%s" % st)
            return False
        if _dismiss_giphy_overlay(xml):
            time.sleep(0.5)
            continue
        if _dismiss_story_to_story_nux(xml):
            time.sleep(0.5)
            continue
        if _is_also_share_sheet(tb, st):
            if tap_exact(xml, "Not now", "Skip", "Cancel", "Done",
                         label="escape-also"):
                time.sleep(0.8)
                continue
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.8)
            continue
        if _tap_profile_tab():
            return True
        _ensure_on_home_feed()
        time.sleep(1.0)
        if _tap_profile_tab():
            return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.8)
    return False


def _tap_profile_tab():
    """Bottom-nav Profile — right-most exact tab, never left-nav substring.

    2026-08-13: first-match 'profile' in desc hit @102,2475 (not the tab);
    live Profile is @1296,2816. Require x > 50% width + exact Profile.
    2026-08-17: story NUX / GIPHY have no tab — do NOT coord-tap 0.90,0.95
    (obsessedsnipe @1209,1400). Dismiss overlay, Back once, then tab only.
    """
    xml = dump()
    st = detect_state(xml)
    if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE",
              "ACTION_LIMIT", "ACCOUNT_SUSPENDED"):
        print("[post] profile-tab skip — state=%s" % st)
        return False
    if _dismiss_giphy_overlay(xml):
        time.sleep(0.6)
        xml = dump()
    if _dismiss_story_to_story_nux(xml):
        time.sleep(0.6)
        xml = dump()

    def _find_tab(xml):
        sw, sh = _screen_wh(xml)
        x_min = int(sw * 0.50)
        y_min = int(sh * 0.78)
        exact = []
        fuzzy = []
        for nd in nodes(xml):
            if attr(nd, "clickable") != "true":
                continue
            d = attr(nd, "content-desc").strip().lower()
            t = attr(nd, "text").strip().lower()
            x, y = bounds_center(nd)
            if x is None or y is None or y < y_min or x < x_min:
                continue
            if d == "profile" or t == "profile":
                exact.append((x, nd))
            elif "profile" in d or "profile" in t:
                fuzzy.append((x, nd))
        cands = exact or fuzzy
        if not cands:
            return None
        cands.sort(key=lambda r: -r[0])
        return cands[0][1]

    n = _find_tab(xml)
    if n is None:
        print("[post] profile-tab missing — Back once (no coord fallback)")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.8)
        xml = dump()
        st = detect_state(xml)
        if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE",
                  "ACTION_LIMIT", "ACCOUNT_SUSPENDED"):
            print("[post] profile-tab skip after Back — state=%s" % st)
            return False
        if _dismiss_giphy_overlay(xml):
            time.sleep(0.5)
            xml = dump()
        if _dismiss_story_to_story_nux(xml):
            time.sleep(0.5)
            xml = dump()
        n = _find_tab(xml)
    if n is None:
        print("[post] profile-tab still missing — refuse coord tap")
        return False
    tapn(n, "profile-tab")
    return True


def session_matches_user(username):
    """True only if the open IG session is this username (not a leftover account).

    Feed chrome often has no handle — open Profile and require the planned
    username in the dump. 2026-08-14: andrprd skipped login for
    shywidgeon4kgd8 while wakanaarakawa35 was still in the clone.
    """
    u = (username or "").strip().lstrip("@").lower()
    if not u:
        return False
    xml = dump()
    tbl = text_block(xml).lower()
    if u in tbl or ("@" + u) in tbl:
        print("[session] dump already has %s" % u)
        return True
    _tap_profile_tab()
    time.sleep(2.2)
    xml = dump()
    tbl = text_block(xml).lower()
    if u in tbl or ("@" + u) in tbl:
        print("[session] profile is %s — reuse session" % u)
        return True
    print("[session] WRONG USER want=%s (profile dump has no handle) — cold login"
          % u)
    return False


def human_reel_flick(xml=None, dump_ui=False, settle=None, direction="up"):
    """Vertical flick to next (up) or previous (down) Reel. No dump during play."""
    if dump_ui:
        xml = dump()
    if xml:
        sw, sh = _screen_wh(xml)
    else:
        sw, sh = _wm_size()
    if sw <= 0 or sh <= 0:
        sw, sh = 1440, 2960
    cx = sw // 2 + random.randint(-45, 45)
    if (direction or "up").lower() == "down":
        y1 = int(sh * random.uniform(0.22, 0.36))
        y2 = int(sh * random.uniform(0.58, 0.72))
    else:
        y1 = int(sh * random.uniform(0.58, 0.72))
        y2 = int(sh * random.uniform(0.22, 0.36))
    dur = random.randint(140, 260)
    j = _human_jitter_px() if _human_on() else 0
    x1 = max(0, cx + random.randint(-j, j))
    x2 = max(0, cx + random.randint(-j, j) + random.randint(-24, 24))
    adb("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(dur))
    if settle is not None:
        time.sleep(max(0.05, float(settle)))
    else:
        human_pause(0.08, 0.25)


def _tap_ig_profile_no_dump():
    """Own Profile tab — rightmost bottom. No dump (safe after Reels)."""
    sw, sh = _wm_size()
    x, y = int(sw * 0.90), int(sh * 0.95)
    tap(x, y)
    print("[warmup] Profile (no dump) @ %d,%d" % (x, y))


def _on_own_profile(xml=None):
    xml = xml or dump()
    tb = text_block(xml).lower()
    return any(p in tb for p in ("edit profile", "share profile", "edit picture"))


# iOS instagram-reels-ios modules/warmup_policy.py — pre-post FYP window
_WARMUP_POST_PRE_RANGE = (3.0, 7.0)


def _warmup_minutes(max_seconds=None):
    """Roll 3–7 min like iOS roll_post_pre_minutes; optional env / max_seconds cap."""
    env_m = (os.environ.get("IG_WARMUP_MINUTES") or "").strip()
    if env_m:
        try:
            minutes = float(env_m)
        except Exception:
            minutes = random.uniform(*_WARMUP_POST_PRE_RANGE)
    else:
        minutes = random.uniform(*_WARMUP_POST_PRE_RANGE)
    if max_seconds is not None:
        try:
            minutes = min(minutes, float(max_seconds) / 60.0)
        except Exception:
            pass
    env_cap = (os.environ.get("IG_WARMUP_MAX_SEC") or "").strip()
    if env_cap:
        try:
            minutes = min(minutes, max(0.5, float(env_cap) / 60.0))
        except Exception:
            pass
    return max(0.5, round(minutes, 2))


def _warmup_max_seconds():
    return int(_warmup_minutes() * 60)


def swipe(x1, y1, x2, y2, duration_ms=400):
    adb("shell", "input", "swipe", str(int(x1)), str(int(y1)),
        str(int(x2)), str(int(y2)), str(int(duration_ms)))


def swipe_up(times=1, pause=1.2):
    """Scroll content upward (next Reel / next feed item)."""
    for _ in range(max(1, times)):
        swipe(540, 1600, 540, 600, 350)
        time.sleep(pause)


def _find_ig_tab_xy(xml, role):
    """Bottom-nav Home or Reels from an existing Home dump. No extra dump()."""
    role = (role or "").lower()
    if not xml:
        return None
    sw, sh = _screen_wh(xml)
    y_min = int(sh * 0.78) if sh else 2400
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < y_min:
            continue
        d = attr(n, "content-desc").lower()
        t = attr(n, "text").strip().lower()
        rid = attr(n, "resource-id").lower()
        if role == "home":
            if d == "home" or t == "home" or "feed_tab" in rid:
                return (x, y)
        elif role == "reels":
            if d == "reels" or t == "reels" or "clips_tab" in rid or (
                    "reel" in d and "create" not in d):
                return (x, y)
    if role == "home":
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            x, y = bounds_center(n)
            if y and y >= y_min and "home" in attr(n, "content-desc").lower():
                return (x, y)
    return None


def _tap_reels_tab():
    """Open Reels from Home. Dumps while still on Home (not during playback)."""
    xml = dump(timeout=5, attempts=1)
    xy = _find_ig_tab_xy(xml, "reels") if xml else None
    if xy:
        tap(xy[0], xy[1])
        print("[tap] tab-reels @ %d,%d" % xy)
        time.sleep(1.5)
        return True
    if xml and tap_exact(xml, "Reels", "reels", label="tab-reels"):
        time.sleep(1.5)
        return True
    sw, sh = _wm_size()
    tap(int(sw * 0.30), int(sh * 0.95))
    print("[tap] tab-reels coord")
    time.sleep(1.2)
    return True


def _warmup_dismiss_popups(xml=None):
    """iOS _dismiss_ig_popups — Not now / Maybe Later / Cancel."""
    xml = xml or dump(timeout=3, attempts=1)
    if not xml:
        return False
    if tap_exact(xml, "Not now", "Not Now", "Maybe Later", "Cancel",
                 label="warmup-popup"):
        time.sleep(0.7)
        return True
    tb = text_block(xml).lower()
    if "finish setting up" in tb and tap_exact(xml, "Continue", label="warmup-setup"):
        time.sleep(0.7)
        return True
    return False


def _warmup_open_home():
    xml = dump(timeout=4, attempts=1)
    xy = _find_ig_tab_xy(xml, "home") if xml else None
    if xy:
        tap(xy[0], xy[1])
        print("[tap] tab-home @ %d,%d" % xy)
    else:
        _tap_ig_home_no_dump()
    time.sleep(random.uniform(0.8, 1.6))
    _warmup_dismiss_popups()
    return True


def _warmup_slow_scroll_feed(n=3):
    """Slow home-feed scroll (not Reels flick). Port of iOS _slow_scroll_down."""
    sw, sh = _wm_size()
    for _ in range(max(1, n)):
        x = int(sw * random.uniform(0.35, 0.55))
        y1 = int(sh * random.uniform(0.62, 0.72))
        y2 = int(sh * random.uniform(0.28, 0.40))
        swipe(x, y1, x + random.randint(-18, 18), y2,
              duration_ms=random.randint(380, 700))
        time.sleep(random.uniform(1.2, 3.2))


def _warmup_pull_refresh():
    sw, sh = _wm_size()
    x = int(sw * random.uniform(0.40, 0.55))
    y1 = int(sh * random.uniform(0.18, 0.24))
    y2 = int(sh * random.uniform(0.48, 0.58))
    swipe(x, y1, x, y2, duration_ms=random.randint(450, 700))
    time.sleep(random.uniform(1.0, 2.0))


def _warmup_intro_notifications():
    """Home → Activity/Notifications → refresh → short scroll → leave for Reels."""
    if not _warmup_open_home():
        return
    xml = dump(timeout=4, attempts=1)
    opened = False
    if xml:
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            d = attr(n, "content-desc").lower()
            t = attr(n, "text").strip().lower()
            rid = attr(n, "resource-id").lower()
            if any(k in d or k in t or k in rid for k in (
                    "activity", "notification", "news")):
                if "story" in d:
                    continue
                if tapn(n, "warmup-activity"):
                    opened = True
                    break
        if not opened:
            opened = tap_exact(xml, "Activity", "Notifications", "News",
                               label="warmup-activity")
    if not opened:
        sw, sh = _wm_size()
        tap(int(sw * 0.90), int(sh * 0.07))
        print("[tap] warmup-activity coord")
    time.sleep(random.uniform(0.8, 1.5))
    _warmup_pull_refresh()
    _warmup_slow_scroll_feed(n=random.randint(1, 3))
    time.sleep(random.uniform(0.8, 2.0))


def _warmup_intro_home_feed():
    """Slow home feed ~24–36s then Reels (iOS _intro_home_feed)."""
    if not _warmup_open_home():
        return
    deadline = time.time() + random.uniform(24.0, 36.0)
    while time.time() < deadline:
        _warmup_slow_scroll_feed(n=1)
        time.sleep(random.uniform(0.4, 1.8))


def _warmup_close_story_viewer():
    xml = dump(timeout=3, attempts=1)
    if xml and tap_exact(xml, "Close", "Close story camera", label="story-close"):
        time.sleep(0.5)
        return
    human_reel_flick(dump_ui=False, direction="down", settle=0.5)


def _warmup_intro_stories():
    """Open 2–3 other stories from tray, then close (iOS _intro_stories)."""
    if not _warmup_open_home():
        return
    xml = dump(timeout=4, attempts=1)
    tapped = False
    if xml:
        sw, sh = _screen_wh(xml)
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            x, y = bounds_center(n)
            if x is None or y is None or y > int(sh * 0.28):
                continue
            d = attr(n, "content-desc").lower()
            t = attr(n, "text").strip().lower()
            blob = d + " " + t
            if "your story" in blob or "add to story" in blob:
                continue
            if "story" in blob or "live" in blob:
                if tapn(n, "warmup-story"):
                    tapped = True
                    break
    if not tapped:
        sw, sh = _wm_size()
        tap(int(sw * 0.28), int(sh * 0.12))
        print("[tap] warmup-story coord")
    time.sleep(random.uniform(1.2, 2.2))
    xml2 = dump(timeout=3, attempts=1)
    tb2 = text_block(xml2).lower() if xml2 else ""
    if "story camera" in tb2 or "hold to record" in tb2 or "boomerang" in tb2:
        _warmup_close_story_viewer()
        return
    sw, sh = _wm_size()
    for _ in range(random.randint(2, 3)):
        time.sleep(random.uniform(1.4, 3.6))
        tap(int(sw * random.uniform(0.72, 0.88)),
            int(sh * random.uniform(0.40, 0.55)))
        time.sleep(random.uniform(0.3, 0.7))
    _warmup_close_story_viewer()
    time.sleep(0.6)


def _warmup_run_opener():
    """iOS _run_warmup_opener — dice: reels 40% / feed 25% / notif 17% / stories 18%."""
    r = random.random()
    if r < 0.40:
        kind = "reels"
    elif r < 0.65:
        kind = "feed"
    elif r < 0.82:
        kind = "notifications"
    else:
        kind = "stories"
    print("[warmup] opener=%s" % kind)
    try:
        if kind == "feed":
            _warmup_intro_home_feed()
        elif kind == "notifications":
            _warmup_intro_notifications()
        elif kind == "stories":
            _warmup_intro_stories()
    except Exception as e:
        print("[warmup] opener %s fail: %s — Reels" % (kind, e))
    _tap_reels_tab()
    return kind


def _pick_reel_beat():
    """iOS _pick_reel_beat — skip / glance / watch / linger (not flat like-prob)."""
    r = random.random()
    if r < 0.28:
        return {"kind": "skip", "dwell": random.uniform(0.12, 0.40),
                "like": False, "rewatch": False}
    if r < 0.68:
        return {"kind": "glance", "dwell": random.uniform(0.55, 1.35),
                "like": False, "rewatch": False}
    if r < 0.88:
        return {"kind": "watch", "dwell": random.uniform(1.8, 4.5),
                "like": random.random() < 0.12, "rewatch": random.random() < 0.04}
    return {"kind": "linger", "dwell": random.uniform(5.5, 11.0),
            "like": random.random() < 0.72, "rewatch": random.random() < 0.08}


def _warmup_peek_comments():
    """Rare: open comments, slow scroll, leave (iOS _peek_comments)."""
    xml = dump(timeout=3, attempts=1)
    if not xml:
        return
    opened = False
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        if "comment" in d or "comment" in rid or t == "comment":
            if "share" in d or "like" in d:
                continue
            if tapn(n, "warmup-comment"):
                opened = True
                break
    if not opened:
        return
    print("[warmup] peek comments")
    time.sleep(random.uniform(0.8, 1.6))
    sw, sh = _wm_size()
    try:
        for _ in range(random.randint(2, 4)):
            x = int(sw * random.uniform(0.35, 0.55))
            swipe(x, int(sh * 0.72), x, int(sh * 0.42),
                  duration_ms=random.randint(500, 900))
            time.sleep(random.uniform(1.0, 2.4))
        time.sleep(random.uniform(0.6, 1.4))
    finally:
        xml2 = dump(timeout=3, attempts=1)
        if not (xml2 and tap_exact(xml2, "Close", "Dismiss", label="comment-close")):
            human_reel_flick(dump_ui=False, direction="down", settle=0.4)
        time.sleep(0.5)
        _warmup_dismiss_popups()


def _warmup_scroll_reels_feed(minutes):
    """iOS scroll_feed — beat mix until deadline."""
    print("[warmup] scroll_feed %.1f min (skip/glance/watch/linger)" % minutes)
    deadline = time.time() + float(minutes) * 60.0
    swipe_count = 0
    likes = 0
    while time.time() < deadline:
        if swipe_count % 5 == 0:
            _warmup_dismiss_popups(dump(timeout=2, attempts=1))
        beat = _pick_reel_beat()
        dwell = min(beat["dwell"], max(0.0, deadline - time.time()))
        print("[warmup] reel %s %.1fs like=%s" % (
            beat["kind"], dwell, beat["like"]))
        time.sleep(dwell)
        if time.time() >= deadline:
            break
        if beat["like"]:
            xml = dump(timeout=3, attempts=1)
            if xml and _like_current_reel(xml):
                likes += 1
                time.sleep(random.uniform(0.3, 0.8))
        if beat["kind"] in ("watch", "linger") and random.random() < 0.10:
            try:
                _warmup_peek_comments()
            except Exception as e:
                print("[warmup] peek comments fail: %s" % e)
        if beat["rewatch"]:
            human_reel_flick(dump_ui=False, direction="down", settle=None)
            time.sleep(random.uniform(0.8, 2.2))
        human_reel_flick(dump_ui=False, settle=random.uniform(0.08, 0.25))
        swipe_count += 1
    print("[warmup] scroll_feed done flicks=%d likes=%d" % (swipe_count, likes))
    return swipe_count


def _open_profile_search(username):
    """Search and open a profile by username. Best-effort."""
    u = (username or "").strip().lstrip("@")
    if not u:
        return False
    xml = dump()
    if not (tap_exact(xml, "Search", "Search and explore", label="tab-search")
            or tap_exact(xml, "Explore", label="tab-explore")):
        for n in nodes(xml):
            d = attr(n, "content-desc").lower()
            if "search" in d and attr(n, "clickable") == "true":
                tapn(n, "tab-search"); break
        else:
            print("[warmup] no Search tab")
            return False
    time.sleep(1.5)
    xml = dump()
    es = edits(xml)
    if es:
        tapn(es[0], "search-field"); time.sleep(0.4)
    else:
        tap_exact(xml, "Search", label="search-field-fallback")
        time.sleep(0.4)
    adb("shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME")
    adb("shell", "ime", "set", "com.android.adbkeyboard/.AdbIME")
    time.sleep(0.3)
    clear_field()
    adb_b64(u)
    time.sleep(2.0)
    xml = dump()
    tap_exact(xml, "Accounts", "Users", label="search-accounts")
    time.sleep(1.0)
    xml = dump()
    for n in nodes(xml):
        t = attr(n, "text").strip().lstrip("@").lower()
        d = attr(n, "content-desc").lower()
        if t == u.lower() or d == u.lower() or ("@" + u.lower()) in d:
            if attr(n, "clickable") == "true":
                ok = tapn(n, "open-profile-%s" % u)
                time.sleep(2.5)
                return ok
    for n in nodes(xml):
        blob = (attr(n, "text") + " " + attr(n, "content-desc")).lower()
        if u.lower() in blob and attr(n, "clickable") == "true":
            ok = tapn(n, "open-profile-fuzzy")
            time.sleep(2.5)
            return ok
    print("[warmup] profile not found: %s" % u)
    return False


def _open_profile_reels_grid():
    xml = dump()
    if tap_exact(xml, "Reels", "reels", label="profile-reels-tab"):
        time.sleep(1.5)
        return True
    for n in nodes(xml):
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        if "reel" in d or "clips" in rid:
            if attr(n, "clickable") == "true":
                return tapn(n, "profile-reels-tab")
    return False


def _dismiss_save_collection_nux(xml=None):
    """IG save onboarding — never tap Start a collection."""
    xml = xml or dump()
    tb = text_block(xml).lower()
    if any(p in tb for p in (
            "collect the posts you love", "start a collection",
            "save posts in collections", "build a collection with others")):
        print("[warmup] save/collection NUX — Back (skip Start a collection)")
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        human_pause(0.35, 0.65)
        return True
    if "collection" in tb and ("save to" in tb or "saved" in tb):
        if tap_exact(xml, "Not now", "Done", "Cancel", label="save-collection-dismiss"):
            human_pause(0.35, 0.6)
            return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        human_pause(0.35, 0.6)
        return True
    return False


def _save_current_reel():
    """Tap Save / bookmark on current Reel viewer."""
    xml = dump()
    for n in nodes(xml):
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        if attr(n, "clickable") != "true":
            continue
        if "comment" in d or "comment" in rid or "share" in d:
            continue
        if "bookmark" in d or "bookmark" in rid or \
           (d.strip() in ("save", "saved") or rid.endswith("/save") or
            "save_button" in rid or "row_save" in rid):
            ok = tapn(n, "save-reel-icon")
            human_pause(0.5, 0.9)
            if ok:
                xml2 = dump()
                if _dismiss_save_collection_nux(xml2):
                    return False
                if "collection" in text_block(xml2) or "save to" in text_block(xml2):
                    if not tap_exact(xml2, "Save", "Done", "Not now",
                                     label="save-collection"):
                        adb("shell", "input", "keyevent", "KEYCODE_BACK")
                        human_pause(0.4, 0.7)
                return True
    for phrase in ("Save", "Saved", "Add to Saved", "Bookmark"):
        if tap_exact(xml, phrase, label="save-reel"):
            human_pause(0.5, 0.9)
            xml2 = dump()
            if _dismiss_save_collection_nux(xml2):
                return False
            if "collection" in text_block(xml2) or "save to" in text_block(xml2):
                if not tap_exact(xml2, "Save", "Done", "Not now", label="save-collection"):
                    adb("shell", "input", "keyevent", "KEYCODE_BACK")
                    human_pause(0.4, 0.7)
            return True
    print("[warmup] Save control not found")
    return False


def _is_reels_viewer(xml=None, tb=None):
    """Full-screen Reels feed (not Search, not profile grid)."""
    xml = xml or dump()
    tb = (tb if tb is not None else text_block(xml)).lower()
    if _is_search_explore_tab(xml, tb):
        return False
    if _is_launcher_bottom_nav(xml):
        return False
    if any(p in tb for p in ("recent searches", "search for", "try searching")):
        return False
    if any(p in tb for p in ("write a caption", "new post", "your story")):
        return False
    for n in nodes(xml):
        rid = attr(n, "resource-id").lower()
        if "clips_viewer" in rid or "reels_viewer" in rid or "clips_video" in rid:
            return True
    if "reels" in tb and any(p in tb for p in ("like", "comment", "share")):
        return True
    return False


def _is_reel_ad(tb=""):
    tb = (tb or "").lower()
    return any(p in tb for p in (
        "sponsored", "shop now", "learn more", "advertisement",
        "paid partnership", "special offer", "advertising", "install now",
    ))


def _like_current_reel(xml=None):
    """Tap Like on current Reel. Never Comment/Share."""
    xml = xml or dump()
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").lower()
        rid = attr(n, "resource-id").lower()
        t = attr(n, "text").strip().lower()
        if any(x in d or x in rid for x in ("comment", "share", "bookmark", "save", "send")):
            continue
        if d in ("liked", "unlike") or "unlike" in d:
            return False
        if d == "like" or t == "like" or "like_button" in rid or rid.endswith("/like"):
            return tapn(n, "like-reel")
    return False


def _warmup_scroll_profile_reels(username, minutes, like_prob=0.20, save_prob=0.20):
    """iOS scroll_profile_reels — Search → profile Reels viewer → beat scroll."""
    u = (username or "").strip().lstrip("@")
    if not u or minutes <= 0.05:
        return 0
    print("[warmup] profile_reels @%s %.1fmin" % (u, minutes))
    if not _open_profile_search(u):
        return 0
    _open_profile_reels_grid()
    time.sleep(1.2)
    xml = dump(timeout=4, attempts=1)
    tapped = False
    if xml:
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            d = attr(n, "content-desc").lower()
            rid = attr(n, "resource-id").lower()
            if "thumbnail" in d or "thumbnail" in rid or "reel" in d:
                x, y = bounds_center(n)
                if x and y and y < 2200:
                    if tapn(n, "profile-first-reel"):
                        tapped = True
                        break
    if not tapped:
        sw, sh = _wm_size()
        tap(int(sw * 0.18), int(sh * 0.42))
    time.sleep(2.0)
    deadline = time.time() + minutes * 60
    n = 0
    while time.time() < deadline:
        n += 1
        if n % 3 == 0:
            _warmup_dismiss_popups(dump(timeout=2, attempts=1))
        beat = _pick_reel_beat()
        time.sleep(min(beat["dwell"], max(0.0, deadline - time.time())))
        if time.time() >= deadline:
            break
        if beat["like"] or random.random() < like_prob:
            xml = dump(timeout=3, attempts=1)
            if xml:
                _like_current_reel(xml)
        if random.random() < save_prob:
            try:
                _save_current_reel()
            except Exception:
                pass
        if beat["rewatch"]:
            human_reel_flick(dump_ui=False, direction="down")
            time.sleep(random.uniform(0.8, 2.2))
        if time.time() < deadline:
            human_reel_flick(dump_ui=False, settle=random.uniform(0.25, 0.9))
    adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.6)
    return n


def do_warmup(pkg=None, profiles=None, scroll_feed=8, scroll_profile=5,
              saves_per_profile=2, max_seconds=None):
    """Port of iOS instagram-reels-ios modules/warmup.run_warmup (classic).

    Dice opener → Reels beat scroll 3–7 min → own Profile (session stays for Create).
    Does NOT kill/logout Instagram (Android farm must keep session).
    """
    global CURRENT_PKG
    if pkg:
        CURRENT_PKG = pkg
    minutes = _warmup_minutes(max_seconds)
    print("[warmup] iOS-classic minutes=%.2f (opener+beats, session kept)" % minutes)
    _drain_post_login_tips(max_steps=4)
    xml0 = dump(timeout=6, attempts=1)
    st0 = detect_state(xml0) if xml0 else ""
    if st0 in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE"):
        return emit_fail("CAPTCHA" if st0 == "HUMAN_CHECK" else st0, note="warmup_gate")
    hit = _check_action_limit(xml0, note="warmup_gate")
    if hit:
        return hit

    t0 = time.time()
    opener = _warmup_run_opener()
    remaining = max(0.4, minutes - (time.time() - t0) / 60.0)

    seeds = [p.strip().lstrip("@") for p in (profiles or []) if p and str(p).strip()]
    if seeds and remaining > 1.2:
        random.shuffle(seeds)
        seed = seeds[0]
        seed_m = min(1.5, remaining * 0.35)
        try:
            _warmup_scroll_profile_reels(seed, seed_m)
        except Exception as e:
            print("[warmup] seed profile fail: %s" % e)
        _tap_reels_tab()
        remaining = max(0.4, minutes - (time.time() - t0) / 60.0)

    flicks = _warmup_scroll_reels_feed(remaining)
    print("[warmup] Reels done opener=%s flicks=%d — Profile now" % (opener, flicks))
    _section_begin("own_profile", 5)
    _tap_ig_profile_no_dump()
    time.sleep(0.45)
    xml_p = dump(timeout=4, attempts=1)
    on_me = _on_own_profile(xml_p)
    print("[warmup] own profile=%s (max 5s)" % on_me)
    _sw_step("warmup_done", state="PROFILE" if on_me else (st0 or ""),
             note="opener=%s min=%.1f flicks=%d profile=%s" % (
                 opener, minutes, flicks, on_me),
             expected="own profile then Create")
    if flicks >= 4:
        return "WARMUP_DONE"
    return "WARMUP_PARTIAL"

def _node_bounds_rect(n):
    m = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', n)
    if not m:
        return None
    return tuple(int(m.group(i)) for i in range(1, 5))


def _tap_label_clickable(xml, *labels, tag=""):
    """Tap a tool whose text/desc matches, preferring the clickable parent.

    Nomix Overlay/Text chips: clickable=true parent, label on a child TextView
    (dorothhds129 2026-08-30 dump — tap_exact hit the non-clickable child).
    """
    want = {p.strip().lower() for p in labels if p and p.strip()}
    ns = nodes(xml)
    hits = []
    for n in ns:
        t = attr(n, "text").strip().lower()
        d = attr(n, "content-desc").strip().lower()
        if t not in want and d not in want:
            continue
        x, y = bounds_center(n)
        if x is None:
            continue
        hits.append((n, x, y, t or d))
    for n, x, y, name in hits:
        if attr(n, "clickable") == "true":
            return tapn(n, tag or name)
        best = None
        for p in ns:
            if attr(p, "clickable") != "true":
                continue
            b = _node_bounds_rect(p)
            if not b:
                continue
            l, t0, r, btm = b
            if not (l <= x <= r and t0 <= y <= btm):
                continue
            area = (r - l) * (btm - t0)
            if area <= 0 or (r - l) > 900 or (btm - t0) > 600:
                continue
            if best is None or area < best[0]:
                best = (area, p)
        if best:
            return tapn(best[1], "%s-parent" % (tag or name))
        print("[tap]", tag or name, "@ %d,%d (label)" % (x, y))
        tap(x, y)
        return True
    return False


def _tap_picture_sticker_below_aa(xml):
    """Native right-rail: Aa on top, picture/sticker chip directly below."""
    sw, sh = _screen_wh(xml)
    aa_y = None
    rail = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        x, y = bounds_center(n)
        if x is None or sw <= 0:
            continue
        if x < sw * 0.78 or y > sh * 0.55:
            continue
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        rail.append((y, x, n, d, t))
        if t in ("aa", "text") or d in ("aa", "text", "text tool"):
            aa_y = y
    rail.sort()
    if aa_y is None:
        return False
    for y, x, n, d, t in rail:
        if y <= aa_y + 24:
            continue
        blob = (d + " " + t)
        if any(k in blob for k in ("download", "save", "close", "back", "music",
                                     "audio", "draw", "doodle", "pen", "undo")):
            continue
        return tapn(n, "picture-sticker-below-aa")
    return False


def _is_story_sticker_tray(xml=None):
    """True when sticker/asset picker tray is open."""
    xml = xml or dump()
    xl = xml.lower()
    tb = text_block(xml).lower()
    if "no results found" in tb:
        return True
    if "cutout sticker" in tb or "gif sticker" in tb:
        return True
    if "asset_picker" in xl and ("row_search_edit_text" in xl or "sticker" in tb):
        return True
    tray_bits = (
        "add yours", "cutouts", "frames", "mention", "location",
        "questions", "avatar", "poll", "swipe", "gif",
    )
    if sum(1 for p in tray_bits if p in tb) >= 2:
        return True
    if "link" in tb and any(p in tb for p in ("gif", "poll", "mention", "location")):
        return True
    return False


def _open_story_sticker_tray(xml=None):
    """Open sticker tray: bottom Overlay chip, Stickers, or picture icon below Aa."""
    xml = xml or dump()
    _dismiss_story_to_story_nux(xml)
    xml = dump()
    # Nomix: bottom toolbar Overlay opens sticker tray (not right-rail Aa).
    if _tap_label_clickable(xml, "Overlay", tag="story-overlay-tray"):
        human_pause(0.55, 1.05)
        _dismiss_story_to_story_nux()
        if _is_story_sticker_tray(dump()):
            return True
    if _tap_label_clickable(xml, "Stickers", "Add sticker", "Sticker", "Overlay",
                            tag="story-picture-sticker"):
        human_pause(0.55, 1.05)
        _dismiss_story_to_story_nux()
        return True
    if _tap_picture_sticker_below_aa(xml):
        human_pause(0.55, 1.05)
        _dismiss_story_to_story_nux()
        return True
    if _tap_phrase(xml, "Stickers", "Overlay", "Add sticker", "Sticker",
                   label="story-sticker-phrase", prefer_clickable=False):
        human_pause(0.55, 1.05)
        _dismiss_story_to_story_nux()
        return True
    # Vision last — Grok often times out 30–45s and blocks the farm.
    if _vision_tap(
            "picture sticker",
            question="Story editor: Aa is top-right. The picture/sticker/overlay chip "
                     "is directly below Aa, or the Overlay tool on the bottom toolbar. "
                     "Not Text/Aa, not Next, not POST.",
            tag="picture_sticker"):
        human_pause(0.45, 0.85)
        _dismiss_story_to_story_nux()
        if _is_story_to_story_nux():
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            human_pause(0.45, 0.75)
        return True
    return False


def story_add_link_sticker(url, sticker_text=""):
    """On story editor: picture sticker → Link → URL + optional CTA text → Done.

    Operator flow (Tijana 2026-08-30): Aa top-right, picture sticker below,
    dismiss 'introducing story to story sharing', tray ends with LINK,
    then customize sticker text (e.g. Chat here), Done.
    """
    url = (url or "").strip()
    if not url:
        return False
    if not url.startswith("http"):
        url = "https://" + url

    def _in_sticker_tray(xml=None):
        return _is_story_sticker_tray(xml)

    def _link_on_canvas(xml=None):
        xml = xml if xml is not None else dump()
        tb = text_block(xml).lower()
        needle = url.lower()
        if needle in tb or needle.replace("https://", "").replace("http://", "") in tb:
            return True
        try:
            host = needle.split("://", 1)[-1].split("/", 1)[0]
        except Exception:
            host = ""
        if host and host in tb:
            return True
        for n in nodes(xml):
            blob = (attr(n, "text") + " " + attr(n, "content-desc")).lower()
            if "link sticker" in blob or blob.strip() == "link":
                return True
            if host and host in blob:
                return True
        return False

    def _tap_link_tile(xml):
        # Exact labels only — never bare "link" substring (hits search chrome).
        if tap_exact(xml, "Link", "Add link", label="story-link-sticker"):
            return True
        for n in nodes(xml):
            if attr(n, "clickable") != "true":
                continue
            d = attr(n, "content-desc").strip().lower()
            t = attr(n, "text").strip().lower()
            if d in ("link", "add link", "link sticker") or t in ("link", "add link"):
                return tapn(n, "story-link-desc")
        return False

    def _leave_tray():
        for _ in range(3):
            if not _in_sticker_tray():
                return True
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.6)
        return not _in_sticker_tray()

    _hide_ime()
    for _ in range(3):
        xml0 = dump()
        if not _is_story_text_tool(xml0):
            break
        print("[story] Aa/text tool open — Done before stickers")
        tap_exact(xml0, "Done", label="story-text-done-pre-sticker")
        time.sleep(1.0)
    xml = dump()
    opened = _open_story_sticker_tray(xml)
    if not opened:
        tools = []
        for n in nodes(xml):
            t = attr(n, "text").strip()
            d = attr(n, "content-desc").strip()
            if t or d:
                tools.append("%s/%s clk=%s" % (t[:24], d[:24], attr(n, "clickable")))
        print("[story] sticker tools seen: %s" % " | ".join(tools[:18]))
        print("[story] Stickers tray not opened")
        return False
    time.sleep(1.2)
    xml = dump()
    _dismiss_story_to_story_nux(xml)
    if _is_story_to_story_nux():
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.8)
    xml = dump()
    if _vision_tap(
            "Link",
            question="Sticker tray: location, mention, music, gif, add yours, frames, "
                     "photo, cutouts, questions, avatar, emoji, poll, swipe emoji, LINK. "
                     "Find LINK. If not visible say visible=false.",
            tag="link_tile") or _tap_link_tile(xml):
        pass
    else:
        # Link is last in the tray — swipe toward it, then search "Link"
        found_link = False
        for i in range(6):
            human_swipe(1180, 2100, 280, 2100, duration_ms=280)
            xml = dump()
            if _tap_link_tile(xml) or (i == 2 and _vision_tap(
                    "Link",
                    question="After swiping the sticker tray, find the LINK tile.",
                    tag="link_tile_swipe")):
                found_link = True
                break
        if not found_link:
            es = edits(dump())
            if es:
                tapn(es[0], "sticker-search"); time.sleep(0.3)
                clear_field(); adb_b64("Link"); time.sleep(1.4)
                xml = dump()
                tb = text_block(xml).lower()
                if "no results found" in tb:
                    print("[story] Link sticker search: no results")
                    _leave_tray()
                    return False
                if not _tap_link_tile(xml):
                    print("[story] Link sticker not available (account/app may lack it)")
                    _leave_tray()
                    return False
            else:
                print("[story] Link sticker UI not found")
                _leave_tray()
                return False
    time.sleep(1.2)
    _hide_ime()
    xml = dump()
    es = edits(xml)
    if not es:
        print("[story] Link URL field missing after opening Link sticker")
        _leave_tray()
        return False
    tapn(es[0], "link-url-field"); human_pause(0.25, 0.45)
    clear_field(); adb_b64(url); human_pause(0.55, 0.95)
    label = (sticker_text or os.environ.get("IG_STORY_LINK_TEXT") or "Chat here").strip()
    if label:
        xml = dump()
        tb = text_block(xml).lower()
        es2 = edits(xml)
        target = None
        if len(es2) >= 2:
            target = es2[1]
        elif "customize sticker text" in tb or "sticker text" in tb:
            if not _tap_label_clickable(xml, "Customize sticker text", "Sticker text",
                                        tag="link-sticker-text"):
                if es2:
                    target = es2[0]
        if target is not None:
            tapn(target, "link-sticker-text")
            human_pause(0.25, 0.45)
            clear_field()
            adb_b64(label)
            human_pause(0.4, 0.75)
        elif "customize sticker text" in tb:
            _tap_phrase(xml, "Customize sticker text", "Chat here",
                        label="link-sticker-text-phrase", prefer_clickable=False)
            human_pause(0.25, 0.45)
            adb_b64(label)
            human_pause(0.4, 0.75)
    _hide_ime()
    if not (tap_exact(dump(), "Done", "Add", "OK", label="link-done")):
        done = False
        for n in nodes(dump()):
            d = attr(n, "content-desc").lower()
            rid = attr(n, "resource-id").lower()
            if attr(n, "clickable") != "true":
                continue
            if d in ("done", "check", "confirm") or "done" in rid or "check" in rid:
                done = tapn(n, "link-done-icon")
                break
        if not done:
            print("[story] Link Done/Add missing")
            _leave_tray()
            return False
    human_pause(0.9, 1.35)
    xml = dump()
    if _in_sticker_tray(xml) and not _link_on_canvas(xml):
        print("[story] still in sticker tray after Done — link NOT confirmed")
        _leave_tray()
        if _in_sticker_tray() or not _link_on_canvas():
            return False
    if _link_on_canvas():
        print("[story] link sticker set -> %s" % url[:60])
        return True
    xml = dump()
    # Tray closed + share chrome after a real URL field fill: accept.
    if not _in_sticker_tray(xml):
        xl = xml.lower()
        tb = text_block(xml)
        if ("post_capture_button_share" in xl or "share_container" in xl or
                "your story" in tb.lower()):
            print("[story] link sticker Done (tray closed, share chrome) -> %s" % url[:60])
            return True
    print("[story] link sticker NOT confirmed on canvas")
    return False


def _settle_after_story_share(max_backs=5):
    """Leave story composer after Share; wait until feed/home is reachable."""
    for _ in range(max_backs):
        xml = dump()
        st = detect_state(xml)
        tb = text_block(xml).lower()
        if st == "FEED" or ("your story" in tb and "home" in tb):
            return True
        if _is_story_editor_chrome(xml) or _is_story_text_tool(xml) or \
           st in ("CREATE_PICKER", "EDIT_SCREEN", "CAPTION_SCREEN", "CREATE_CAMERA"):
            if _is_story_text_tool(xml):
                tap_exact(xml, "Done", label="post-share-settle-done")
            else:
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
            human_pause(0.45, 0.85)
            continue
        if _dismiss_story_to_story_nux(xml):
            human_pause(0.4, 0.7)
            continue
        if st == "FEED":
            return True
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        human_pause(0.4, 0.75)
    return detect_state(dump()) == "FEED"


def _open_own_story_view():
    """Open just-shared story: feed tray ring first, then profile ring."""
    _settle_after_story_share()
    human_pause(1.8, 3.2)
    _ensure_on_home_feed()
    human_pause(0.6, 1.1)
    xml = dump()
    u = (CURRENT_USER or "").strip().lower()
    feed_hits = []
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        x, y = bounds_center(n)
        if x is None or y is None:
            continue
        blob = d + " " + t
        if "add to story" in blob or "add to your story" in blob:
            continue
        if "your story" in d or (u and u in d and "story" in d):
            feed_hits.append((y, x, n))
        elif d.endswith("'s story") or (u and ("%s's story" % u) in d):
            feed_hits.append((y, x, n))
    if feed_hits:
        feed_hits.sort()
        if tapn(feed_hits[0][2], "open-own-story-feed"):
            human_pause(1.6, 2.6)
            return True
    if tap_exact(xml, "Your story", label="open-own-story-feed-text"):
        human_pause(1.6, 2.6)
        return True
    if not _escape_to_profile():
        print("[story] Highlight: profile unreachable")
        return False
    human_pause(1.0, 1.8)
    xml = dump()
    for n in nodes(xml):
        d = attr(n, "content-desc").lower()
        if attr(n, "clickable") != "true":
            continue
        if "your story" in d or ("story" in d and "highlight" not in d and "add" not in d):
            if tapn(n, "open-own-story-profile"):
                human_pause(1.6, 2.6)
                return True
    if tap_exact(xml, "Your story", "Add to story", label="open-own-story-profile-text"):
        human_pause(1.6, 2.6)
        return True
    print("[story] Highlight: own story ring not found")
    return False


def story_add_to_highlight(title=""):
    """After story is live: open own story → Highlight → create/add.

    Fail-closed: True only when highlight UI accepts Add/Done. Empty title → skip.
    """
    title = (title or "").strip()
    if not title:
        return False
    if not _open_own_story_view():
        return False
    xml = dump()
    if not tap_exact(xml, "Highlight", "Highlights", label="story-highlight"):
        for n in nodes(xml):
            d = attr(n, "content-desc").lower()
            if attr(n, "clickable") != "true":
                continue
            if "more" in d or "options" in d:
                tapn(n, "story-more"); human_pause(0.7, 1.1); break
        xml = dump()
        if not tap_exact(xml, "Highlight", "Add to highlight", label="story-highlight"):
            print("[story] Highlight control not found")
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            return False
    human_pause(1.0, 1.6)
    xml = dump()
    added = False
    if tap_exact(xml, "New", "New highlight", label="new-highlight"):
        human_pause(0.7, 1.1)
        es = edits(dump())
        if not es:
            print("[story] highlight title field missing")
            return False
        tapn(es[0], "highlight-title"); human_pause(0.25, 0.45)
        clear_field(); adb_b64(title); human_pause(0.5, 0.85)
        added = tap_exact(dump(), "Add", "Done", "OK", label="highlight-add")
    else:
        hit = False
        want = title.lower()
        for n in nodes(xml):
            blob = (attr(n, "text") + " " + attr(n, "content-desc")).lower()
            if want in blob and attr(n, "clickable") == "true":
                tapn(n, "existing-highlight"); hit = True; break
        if hit:
            added = tap_exact(dump(), "Add", "Done", label="highlight-add-existing")
        else:
            print("[story] highlight title '%s' not found" % title)
            return False
    if not added:
        print("[story] highlight Add/Done missing")
        return False
    human_pause(1.0, 1.5)
    print("[story] highlight '%s' updated" % title)
    for _ in range(3):
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        human_pause(0.4, 0.65)
    return True


def _profile_hard_empty(tb):
    """True only for real empty-state chrome — not a lagging '0 posts' counter.

    bur_cu5439 / handeisik346 2026-08-10: header said 0 posts but grid already
    showed the new photo → old '0 posts' check caused false POST_BLOCKED.
    """
    tb = (tb or "").lower()
    return any(p in tb for p in (
        "no posts yet",
        "share photos and videos",
        "share your first photo",
        "when you share photos",
        "capture your first",
        "start capturing",
    ))


def _profile_zero_post_count(tb):
    """True when profile header shows 0 posts (count can lag behind grid).

    IG sometimes concatenates '0posts' / '1posts' (no space).
    """
    return bool(re.search(r"(^|[^\d])0\s*posts?\b", (tb or "").lower()))


def _profile_shows_empty_grid(tb):
    """Back-compat: hard empty chrome OR zero count (prefer _profile_hard_empty)."""
    return _profile_hard_empty(tb) or _profile_zero_post_count(tb)


def _own_grid_display_name_tile(d):
    """Own-profile grid: 'Photo by James Graves at Row 1, Column 1'.

    Username is not in the desc (adoringleopard / ecstaticcurlew 2026-08-17).
    Not 'reel by otherhandle' (Tsumugi).
    """
    d = (d or "").strip().lower()
    if "reel by " in d:
        return False
    return bool(re.search(
        r"\b(?:photo|video|post|carousel|album|image)\s+by\s+.+\s+at\s+row\s+\d+",
        d))


def _desc_names_other_user(d, username=""):
    """True if a grid tile names a different account.

    TsumugiKawamura46 2026-08-13: 'reel by hitominakagawa346' was counted POST_DONE.
    2026-08-17: 'Photo by James Graves at Row 1, Column 1' is a display name on
    own grid — first token 'james' is NOT a foreign handle.
    """
    d = (d or "").strip().lower()
    u = (username or "").strip().lower()
    if _own_grid_display_name_tile(d):
        return False
    m = re.search(
        r"\b(?:reel|photo|video|post|carousel|album)\s+by\s+(.+?)(?:\s+at\s+row|\s*$)",
        d)
    if m:
        who = m.group(1).strip().rstrip(".,")
        if " " in who:
            return False
        other = who
    else:
        m2 = re.search(
            r"\b(?:reel|photo|video|post|carousel|album)\s+by\s+([a-z0-9._]+)", d)
        if not m2:
            return False
        other = m2.group(1).rstrip(".,")
    if not re.match(r"^[a-z0-9._]+$", other):
        return False
    if not u:
        return True
    return other != u and u not in other and not other.startswith(u)


def _pick_own_live_hit(hits, username="", on_own=False):
    """Choose a live own-grid hit. Keep Tsumugi: foreign reel-by is already skipped."""
    u = (username or "").strip().lower()
    strict = [h for h in hits if h[1] == "strict"]
    if strict:
        return strict[0], "strict"
    if not on_own:
        return None, ""
    soft = [h for h in hits if h[1] == "grid_tile"]
    owned = [h for h in soft if u and u in h[0]]
    named = [h for h in soft if _own_grid_display_name_tile(h[0])]
    generic = [h for h in soft if " by " not in h[0]]
    pick = owned or named or generic
    if pick:
        return pick[0], "grid_tile"
    return None, ""


def _media_desc_is_live_post(d, username="", prefer_reels=False):
    """Strict: real grid media owned by username — never another user's tile."""
    d = (d or "").strip().lower()
    u = (username or "").strip().lower()
    if not d:
        return False
    # Empty-state / chrome — never success
    if any(x in d for x in (
            "no posts", "edit profile", "share profile", "create", "add to story",
            "your story", "camera", "gallery", "unselected", "recents", "follow",
            "discover people", "suggested")):
        return False
    if _desc_names_other_user(d, u):
        return False
    if prefer_reels:
        if u and ("reel by %s" % u) in d:
            return True
        if u and u in d and "reel" in d and ("thumbnail" in d or "video" in d):
            return True
        return False
    # Strongest: "Photo by username"
    if u and d.startswith("photo by ") and u in d:
        return True
    if u and ("photo by %s" % u) in d:
        return True
    # Grid tile: must include username + media word (not orphan 'photo thumbnail')
    if u and u in d and any(k in d for k in ("photo", "image", "post", "reel",
                                               "carousel", "album")):
        if "thumbnail" in d or "by " in d or "carousel" in d:
            return True
    return False


def _profile_grid_media_hits(xml, username="", prefer_reels=False):
    """Find own-profile grid media nodes (strict or large soft tiles).

    Soft tiles: on own profile IG often exposes only 'photo thumbnail' while the
    posts counter still says 0 (bur_cu / hande 2026-08-10 screenshots).
    Require large bounds in the grid band — never tiny Discover-people avatars.
    """
    u = (username or "").strip().lower()
    xml = xml or dump()
    _sw, sh = _screen_wh(xml)
    grid_y_min = int(sh * 0.32)
    hits = []
    for n in nodes(xml):
        d = attr(n, "content-desc").strip().lower()
        if not d:
            continue
        if _media_desc_is_live_post(d, username=u, prefer_reels=prefer_reels):
            hits.append((d, "strict"))
            continue
        if _desc_names_other_user(d, u):
            continue
        x, y = bounds_center(n)
        if x is None or y is None or y < grid_y_min:
            continue
        w, h = bounds_wh(n)
        # Grid cell on Note 8 is ~large; Discover avatars are small
        if not w or not h or w < 280 or h < 280:
            continue
        if any(b in d for b in (
                "edit profile", "share profile", "follow", "discover", "suggested",
                "add to story", "your story", "camera", "live", "close friends")):
            continue
        # Named "by X" without our username is never a live own post —
        # except own-grid display-name tiles (Photo by James Graves at Row 1).
        if " by " in d and u and u not in d and not _own_grid_display_name_tile(d):
            continue
        if prefer_reels:
            if "reel" in d or ("video" in d and "thumbnail" in d) or d == "video thumbnail":
                hits.append((d, "grid_tile"))
        else:
            if d == "photo thumbnail" or d.startswith("photo thumbnail") or \
               ("photo" in d and "thumbnail" in d) or \
               ("image" in d and "thumbnail" in d) or \
               "carousel" in d or "album" in d or "multiple photos" in d or \
               d.startswith("post by ") or d.startswith("photo by "):
                hits.append((d, "grid_tile"))
    return hits


def verify_post_live(username="", prefer_reels=False):
    """CONFIRM IG post/reel is live on OWN profile. Composer closed != success.

    deryaozgur335 2026-08-08: refuse fake POST_DONE on empty chrome.
    bur_cu5439 2026-08-10: '0 posts' counter can lag while grid already shows
    the new photo — check media tiles before treating as empty; do not short-
    circuit on zero count alone.
    """
    polls = 14 if prefer_reels else 10
    for _ in range(polls):
        tb = text_block(dump()).lower()
        if any(p in tb for p in ("sharing", "posting", "uploading", "processing",
                                   "your post has been shared", "your reel has been shared")):
            print("[post] still publishing…"); time.sleep(2.8); continue
        break
    time.sleep(6 if not prefer_reels else 8)
    st = detect_state(dump())
    if st in ("ACCOUNT_SUSPENDED", "CHALLENGE", "CAPTCHA", "CONTACT_VERIFY",
              "ACTION_LIMIT"):
        if st == "ACTION_LIMIT":
            return fail_action_limit("verify_pre")
        print("[post] blocked at post time (%s) -> NOT live" % st); return emit_fail("POST_BLOCKED")
    u = (username or "").strip().lower()
    if not u:
        print("[post] verify needs username — refuse POST_DONE")
        return emit_fail("POST_BLOCKED")
    if not _escape_to_profile():
        print("[post] verify — profile unreachable after share")
        return emit_fail("POST_BLOCKED", note="profile_tab_missing")
    time.sleep(2.5)
    hard_empty_hits = 0
    soft_zero_hits = 0
    max_attempts = 18 if prefer_reels else 16
    for attempt in range(max_attempts):
        xml = dump()
        st = detect_state(xml)
        if st == "ACTION_LIMIT":
            return fail_action_limit("verify_profile")
        if st in ("ACCOUNT_SUSPENDED", "CHALLENGE"):
            print("[post] profile shows %s -> post NOT live" % st); return emit_fail("POST_BLOCKED")
        if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY"):
            print("[post] verify blocked by %s (nila_y6790 class)" % st)
            return emit_fail("POST_CAPTCHA" if st != "CONTACT_VERIFY" else "POST_BLOCKED")
        tb = text_block(xml)
        tbl = tb.lower()
        # Reels land on Reels tab; feed on Posts
        if attempt == 1:
            if prefer_reels:
                tap_exact(xml, "Reels", "REELS", "Clips", label="profile-reels-tab")
            else:
                tap_exact(xml, "Posts", "POSTS", label="profile-posts-tab")
            time.sleep(1.2)
            xml = dump()
            tb = text_block(xml)
            tbl = tb.lower()
        if attempt in (2, 5, 9):
            # Discover people sits above the grid — swipe UP to reveal tiles
            # (aylinsevim82 2026-08-11: 1 carousel live, verify never saw it).
            print("[post] swipe-up to reveal profile grid")
            adb("shell", "input", "swipe", "720", "1700", "720", "900", "350")
            time.sleep(1.4)
            xml = dump()
            tb = text_block(xml)
            tbl = tb.lower()
        if attempt in (3, 7, 12):
            print("[post] pull-refresh profile grid")
            adb("shell", "input", "swipe", "720", "900", "720", "1700", "300")
            time.sleep(2.5)
            xml = dump()
            tb = text_block(xml)
            tbl = tb.lower()

        on_own = any(p in tbl for p in ("edit profile", "share profile", "edit picture"))
        if not on_own and u and u not in tbl:
            # Not clearly own profile — don't invent success
            print("[post] verify not on own profile yet (attempt %d)" % (attempt + 1))
            foreign = ("follow" in tbl and "followers" in tbl
                       and "edit profile" not in tbl)
            if foreign:
                print("[post] foreign profile chrome — Back then Profile")
                adb("shell", "input", "keyevent", "4")
                time.sleep(0.9)
            if not _escape_to_profile():
                time.sleep(1.0)
            else:
                time.sleep(2.0)
            if attempt == 4 and u:
                print("[post] search own username to recover profile")
                _open_profile_search(username)
                time.sleep(1.5)
            continue

        hits = _profile_grid_media_hits(xml, username=u, prefer_reels=prefer_reels)
        pick, kind = _pick_own_live_hit(hits, username=u, on_own=on_own)
        if pick and kind == "strict":
            print("[OK] POST_LIVE strict desc=%r" % pick[0][:80])
            return "POST_DONE"
        # Soft generic / display-name tile on OWN profile only (0-posts lag).
        # Never another account's "reel by X" (TsumugiKawamura46 2026-08-13).
        if pick:
            print("[OK] POST_LIVE own grid tile (count may lag 0) desc=%r"
                  % pick[0][:80])
            return "POST_DONE"

        # Posts count >= 1 + own media (never another user's named tile)
        m = re.search(r"(\d+)\s*posts?\b", tbl)
        if m and int(m.group(1)) >= 1 and on_own:
            mediaish = any(
                _media_desc_is_live_post(attr(n, "content-desc"), u, prefer_reels)
                or ("thumbnail" in attr(n, "content-desc").lower()
                    and u in attr(n, "content-desc").lower())
                or _own_grid_display_name_tile(attr(n, "content-desc"))
                for n in nodes(xml)
            )
            soft = [h for h in hits if h[1] == "grid_tile"]
            if mediaish or (soft and any(
                    u in h[0] or " by " not in h[0] or _own_grid_display_name_tile(h[0])
                    for h in soft)):
                print("[OK] POST_LIVE (posts count=%s + media)" % m.group(1))
                return "POST_DONE"

        hard_empty = _profile_hard_empty(tbl)
        zero_count = _profile_zero_post_count(tbl)
        if hard_empty and not hits:
            hard_empty_hits += 1
            print("[post] hard empty chrome (attempt %d)" % (attempt + 1))
            if hard_empty_hits >= 5:
                print("[post] empty grid confirmed — NOT live (refuse false POST_DONE)")
                return emit_fail("POST_BLOCKED")
            time.sleep(2.2)
            _tap_profile_tab(); time.sleep(2.0)
            continue
        if zero_count and not hits:
            soft_zero_hits += 1
            print("[post] 0 posts count, no grid tile yet (attempt %d) — wait for lag"
                  % (attempt + 1))
            if soft_zero_hits >= 10:
                print("[post] still 0 posts + no tile after waits — NOT live")
                return emit_fail("POST_BLOCKED")
            time.sleep(3.0)
            _tap_profile_tab(); time.sleep(2.0)
            continue

        if prefer_reels and attempt in (5, 8, 11):
            tap_exact(dump(), "Reels", "REELS", "Clips", label="profile-reels-retry")
            time.sleep(1.5)
        _tap_profile_tab(); time.sleep(2.5)
    print("[post] no verified own media after polling -> NOT live")
    return emit_fail("POST_BLOCKED")


def peek_own_post_live(username="", prefer_reels=False):
    """One-shot: own profile already shows a live grid tile.

    RETRY of false POST_BLOCKED must not Create/Share again (double post +
    Drive claim + Meta heat). New scheduled jobs do not call this skip.
    HUMAN_CHECK is not live.
    """
    u = (username or "").strip().lower()
    if not u:
        return False
    _section_begin("peek", 8)
    xml = dump()
    st = detect_state(xml)
    if st in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE",
              "ACTION_LIMIT", "ACCOUNT_SUSPENDED"):
        print("[post] peek live skip — state=%s" % st)
        return False
    if st in ("CAPTION_SCREEN", "EDIT_SCREEN", "CREATE_PICKER", "CREATE_CAMERA",
              "CREATE_CHOOSER"):
        return False
    if _dismiss_giphy_overlay(xml) or _dismiss_story_to_story_nux(xml):
        time.sleep(0.6)
    if not _tap_profile_tab():
        return False
    if _section_expired(need=1):
        print("[post] peek budget — go Create")
        return False
    time.sleep(0.6)
    xml = dump()
    st = detect_state(xml)
    if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE"):
        print("[post] peek live — %s on profile" % st)
        return False
    tbl = text_block(xml).lower()
    on_own = any(p in tbl for p in ("edit profile", "share profile", "edit picture"))
    if not on_own:
        return False
    hits = _profile_grid_media_hits(xml, username=u, prefer_reels=prefer_reels)
    pick, kind = _pick_own_live_hit(hits, username=u, on_own=True)
    if not pick:
        return False
    print("[post] peek already live (%s) desc=%r" % (kind, pick[0][:80]))
    return True


def verify_story_live(username=""):
    """Confirm own profile shows a story ring. Fail-closed — no soft POST_DONE."""
    time.sleep(4)
    if not _escape_to_profile():
        print("[post] story verify — profile unreachable")
        return emit_fail("POST_BLOCKED", note="story_profile_missing")
    time.sleep(2.5)
    for _ in range(6):
        xml = dump()
        tb = text_block(xml)
        u = (username or "").strip().lower()
        for n in nodes(xml):
            d = attr(n, "content-desc").lower()
            if "story" in d and (not u or u in d or "your story" in d or "0 of" in d):
                print("[OK] STORY_LIVE (story ring/desc): %s" % d[:60])
                return "POST_DONE"
        if "your story" in tb.lower():
            print("[OK] STORY_LIVE (Your story text)")
            return "POST_DONE"
        time.sleep(2.0)
        if not _escape_to_profile():
            break
        time.sleep(1.5)
    print("[post] story verify inconclusive — refuse soft POST_DONE")
    return emit_fail("POST_BLOCKED", note="story_verify_inconclusive")


def _do_post_body(caption=CAPTION, image_path=None, username="", pkg=None,
            format="feed", media_paths=None, story_link="", highlight_title="",
            retry=False):
    """IG publish: format=feed|story|carousel|reel.

    media_paths: list of local files (carousel uses multiple; reel prefers video).
    image_path: legacy single-file alias.
    story_link: URL for story link sticker (stories only). Fail-closed if set.
    highlight_title: highlight name after story share (empty = skip). Fail-closed if set.
    retry: True for scheduler/farm RETRY of a prior fail — if own grid is
    already live, return POST_DONE without Create (no double post).
    """
    _lock_portrait()
    global CURRENT_PKG, CURRENT_USER
    _reset_post_meta()
    if pkg:
        CURRENT_PKG = pkg
    if username:
        CURRENT_USER = (username or "").strip()
    fmt = (format or "feed").strip().lower()
    if fmt not in ("feed", "story", "carousel", "reel"):
        fmt = "feed"
    paths = list(media_paths or [])
    if not paths and image_path:
        paths = [image_path]
    print("[post] IG %s flow (%d media)..." % (fmt, len(paths)))
    _sw_step("post_start", note="format=%s paths=%d" % (fmt, len(paths)),
             expected="open create and publish")
    grant_media_permissions(CURRENT_PKG)

    if rt is not None:
        try:
            rt.configure(serial=SERIAL, username=username, pkg=CURRENT_PKG or "",
                         fmt=fmt)
        except Exception:
            pass
        _rt_log("do_post_start", paths=paths, mime=[_media_mime(p) for p in paths])

    for p in paths:
        if not os.path.isfile(p):
            print("[FAIL] local media missing: %s" % p)
            return emit_fail("NO_IMAGE")
        if mn is not None and not mn.is_safe_basename(os.path.basename(p)):
            print("[media] WARN local name has spaces/parens — push will rename: %s"
                  % os.path.basename(p))
        remote = push_media(p)
        _rt_log("push_media", path=p, remote=remote or CURRENT_REMOTE_IMG or "",
                stem=CURRENT_REMOTE_STEM or "", ok=bool(remote), fmt=fmt)
        if not remote or not CURRENT_REMOTE_IMG:
            print("[FAIL] media push/verify failed — cannot POST_DONE (%s)" % fmt)
            _rt_fail("push_fail", path=p, fmt=fmt)
            return emit_fail("NO_IMAGE")

    _ensure_ig_foreground(CURRENT_PKG, hard=True)
    _drain_post_login_tips(max_steps=12)
    if not _clear_notif_gate(max_tries=4, label="post-notif"):
        print("[FAIL] notification prompt blocked create/post (ayar_ii68 class)")
        return emit_fail("TIP_STUCK", note="notif_intro_stuck")

    hit = _check_action_limit(note="pre_create")
    if hit:
        return hit
    st0 = detect_state(dump())
    if st0 in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE"):
        print("[FAIL] %s before create (logged in but post gated)" % st0)
        return emit_fail("POST_CAPTCHA")

    dest = "feed" if fmt in ("feed", "carousel") else fmt
    if retry and fmt in ("feed", "carousel", "reel") and username:
        xml_pk = dump()
        if _on_own_profile(xml_pk):
            hits = _profile_grid_media_hits(
                xml_pk, username=username, prefer_reels=(fmt == "reel"))
            pick, kind = _pick_own_live_hit(hits, username=username, on_own=True)
            if pick:
                print("[post] already live on profile (%s) — skip Create" % kind)
                return "POST_DONE"

    do_post._pre_draft_tries = 0
    # Separate budgets: Message-center phones burn 30–60s just opening Create.
    # Pick/Next must not inherit that debt (Nylah 2026-09-04: picker had 7 videos,
    # create STOP at 60s before any tile tap → POST_TIMEOUT).
    _section_begin("create_open", 90)
    if not _open_ig_create(prefer_dest=dest):
        hit = _check_action_limit(note="create_blocked")
        if hit:
            return hit
        st1 = detect_state(dump())
        if st1 in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY", "CHALLENGE"):
            print("[FAIL] %s blocked create" % st1)
            return emit_fail("POST_CAPTCHA")
        if _on_create_surface() or st1 in ("CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER"):
            print("[post] create open returned False but surface is up (%s) — continue" % st1)
        else:
            print("[FAIL] POST_TIMEOUT (create never opened)")
            if fmt == "reel":
                _rt_fail("create_timeout", state=st1, snippet=text_block(dump()))
            return emit_fail("POST_TIMEOUT")
    _section_begin("create", 55)

    for _ in range(6):
        xml_pre = dump()
        tb_pre = text_block(xml_pre)
        if _is_draft_exit_sheet(tb_pre):
            draft_n = getattr(do_post, "_pre_draft_tries", 0) + 1
            do_post._pre_draft_tries = draft_n
            print("[post] draft sheet pre-pick (try %d)" % draft_n)
            if not _clear_create_draft_gate(dest=dest, label="post-pre-pick-draft"):
                print("[FAIL] draft sheet blocked create (dismiss failed)")
                return emit_fail("POST_TIMEOUT")
            if draft_n >= 2 and _is_draft_exit_sheet(text_block(dump())):
                print("[FAIL] draft sheet still up after 2 pre-pick tries")
                return emit_fail("POST_TIMEOUT")
            time.sleep(0.8)
            continue
        st = detect_state(xml_pre)
        if st in ("TIP_SHEET", "SYS_PERMISSION", "ONBOARD_CARD"):
            if st == "SYS_PERMISSION":
                dismiss_android_permission()
            else:
                dismiss_tip_sheet(label="post-pre-pick-tip")
            time.sleep(1.2); continue
        break

    st = detect_state(dump())
    if st == "CREATE_CHOOSER":
        if dest == "story":
            tap_exact(dump(), "Story", "STORY", label="open-chooser-story")
        elif dest == "reel":
            tap_exact(dump(), "Reel", "REEL", "Reels", label="open-chooser-reel")
        else:
            tap_exact(dump(), "Post", "POST", label="post-open-chooser")
        time.sleep(2.0)
        if fmt == "reel":
            _rt_log("chooser", cta="reel", state=detect_state(dump()))
    elif dest == "reel":
        _ensure_create_dest(dump(), dest="reel", force=True)
        time.sleep(0.8)
        _open_gallery_from_camera(dump())
        _rt_log("force_reel_dest", state=detect_state(dump()))
        # Same phone/pkg may still be on prior account's caption draft
        # Never abort when already on gallery (velorastar7: picker ≠ stale EDIT)
        st_fr = detect_state(dump())
        if st_fr not in ("CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER") and (
                st_fr in ("CAPTION_SCREEN", "EDIT_SCREEN") or in_caption_screen(dump())
                or _reel_at_caption(dump()) or _reel_edit_ready(dump())):
            _abort_stale_composer(prefer_dest="reel")
        # Purpose gate: must be on create surface before waiting for tiles
        # (hankoac542: wait on EDIT with 0 tiles wasted 6 rounds)
        if not _on_create_surface(dump()) and not _gallery_has_video_tile(dump()):
            print("[post] not on gallery after create — hard recover before wait")
            if not _hard_recover_create("reel"):
                print("[FAIL] cannot reach Reel gallery after stale composer")
                _rt_fail("gallery_recover_fail", state=detect_state(dump()),
                         snippet=text_block(dump())[:240])
                return emit_fail("POST_TIMEOUT")
        if detect_state(dump()) == "CREATE_CAMERA" or _is_reel_camera(dump()):
            _open_gallery_from_camera(dump())
            time.sleep(1.0)
        if not _wait_for_gallery_video(max_rounds=3):
            print("[post] gallery wait empty — hard recover + retry wait")
            if not (_hard_recover_create("reel") and _wait_for_gallery_video(max_rounds=2)):
                print("[FAIL] pushed video never appeared in IG gallery")
                _rt_fail("gallery_no_video", remote=CURRENT_REMOTE_IMG or "",
                         stem=CURRENT_REMOTE_STEM or "",
                         snippet=text_block(dump())[:240])
                return emit_fail("POST_TIMEOUT")
    elif dest in ("feed", "story"):
        if _is_draft_exit_sheet(text_block(dump())):
            _clear_create_draft_gate(dest=dest, label="post-feed-draft")
        # Feed jobs often land on leftover REEL tab — force POST
        if dest == "feed":
            _ensure_create_dest(dump(), dest="feed", force=True)
            time.sleep(0.6)
        if dest == "story":
            _ensure_create_dest(dump(), dest="story", force=True)
            time.sleep(0.6)
            # Story create lands on camera shutter — open gallery before pick
            # (dorothhds129 2026-08-30: CREATE_PICKER misdetect + Gallery CTA).
            _open_gallery_from_camera(dump())
            time.sleep(1.0)
        st_fr = detect_state(dump())
        if st_fr in ("CAPTION_SCREEN", "EDIT_SCREEN") or in_caption_screen(dump()):
            _abort_stale_composer(prefer_dest=dest)

    if not paths:
        print("[FAIL] no media paths for %s" % fmt)
        return emit_fail("NO_IMAGE")
    if fmt == "reel" and not any(_remote_is_video(p) for p in paths):
        if not _remote_is_video():
            print("[FAIL] reel needs a video file (.mp4/.mov) — got %s" % paths)
            _rt_log("no_video", paths=paths)
            return emit_fail("NO_IMAGE")

    carousel = fmt == "carousel"
    pick_count = max(2, len(paths)) if carousel else 1
    if not _advance_to_caption(dest=dest, carousel=carousel, pick_count=pick_count):
        st2 = detect_state(dump())
        if fmt == "story" and st2 in ("FEED",):
            return verify_story_live(username)
        recovered = False

        def _send_recover_loop(tag="send_recover"):
            nonlocal recovered
            # Only SEND after a true video selection — otherwise share-sheet loop
            if fmt == "reel" and not _reel_pick_succeeded(dump())[0]:
                gsum = _gallery_trace_summary()
                # Gallery already showing videos — pick then continue (don't no-op skip)
                if gsum.get("unsel_video", 0) > 0 or _gallery_has_video_tile():
                    print("[post] %s: tiles up, pick before SEND" % tag)
                    _section_begin("create", 45)
                    if pick_reel_video(max_attempts=6) and _reel_pick_succeeded(dump())[0]:
                        xml_n = dump()
                        _tap_picker_next(xml_n, allow_coord=False) or _advance_next(xml_n)
                        time.sleep(2.0)
                    else:
                        _rt_log("%s_skip" % tag, reason="reel_pick_fail", **gsum)
                        return
                else:
                    _rt_log("%s_skip" % tag, reason="reel_not_ready", **gsum)
                    return
            if not _bypass_picker_via_send():
                return
            _rt_log("%s_start" % tag, state=detect_state(dump()))
            for _ in range(3):
                if _section_expired(need=1.5):
                    return
                xml = dump()
                st3 = detect_state(xml)
                if st3 == "ANDROID_SHARE" or _is_android_share_sheet(xml):
                    if not _handle_android_share_sheet(xml, pkg=CURRENT_PKG):
                        return
                    time.sleep(2.0)
                    continue
                if st3 == "WRONG_APP":
                    _ensure_ig_foreground(CURRENT_PKG, hard=True)
                    continue
                if st3 == "CAPTION_SCREEN" or in_caption_screen(xml) or \
                   (fmt == "reel" and _reel_at_caption(xml)) or \
                   (fmt != "reel" and len(edits(xml)) >= 1):
                    print("[post] recovered via SEND after advance fail")
                    recovered = True
                    return
                if st3 == "EDIT_SCREEN" or (fmt == "reel" and _reel_edit_ready(xml)):
                    _dismiss_reel_overlays(xml)
                    tap_exact(dump(), "Next", "OK", "Done", "Continue",
                              label="send-recover-next") or _advance_next(dump())
                    time.sleep(2.0)
                    continue
                if st3 in ("TIP_SHEET", "ONBOARD_CARD") or _is_reel_create_tip_tb(text_block(xml)):
                    dismiss_tip_sheet(xml, label="send-recover-tip", allow_next=False)
                    time.sleep(1.0)
                    continue
                if tap_exact(xml, "Next", "OK", "Share", "Continue", label="send-recover-cta"):
                    time.sleep(2.0)
                    continue
                break

        if fmt in ("feed", "reel"):
            _send_recover_loop("send_recover")

        # Soft retry once for reels: tip dismiss + FG + folder/gallery + advance again
        if not recovered and fmt == "reel":
            print("[post] soft retry advance (tip/fg/gallery)")
            _rt_log("soft_retry", state=st2, snippet=text_block(dump()))
            _section_begin("create", 50)
            _ensure_ig_foreground(CURRENT_PKG, hard=True)
            xml_r = dump()
            st_early = detect_state(xml_r)
            tb_early = text_block(xml_r).lower()
            # First advance often dies on budget right after Next — caption is already up.
            if st_early == "CAPTION_SCREEN" or in_caption_screen(xml_r) or \
               _reel_at_caption(xml_r) or (
                   "write a caption" in tb_early and "edit cover" in tb_early):
                print("[post] soft retry — already at caption after pick")
                recovered = True
                _rt_log("soft_retry_ok", reason="already_caption")
            if recovered:
                pass
            else:
                if _is_android_share_sheet(xml_r):
                    _handle_android_share_sheet(xml_r, pkg=CURRENT_PKG)
                    time.sleep(2.0)
                    xml_r = dump()
                if _is_preview_size_tip(xml_r) or _is_reel_create_tip_tb(text_block(xml_r)) or \
                   detect_state(xml_r) in ("TIP_SHEET", "ONBOARD_CARD"):
                    if _is_preview_size_tip(xml_r):
                        dismiss_preview_size_tip(xml_r, label="soft-retry-preview")
                    else:
                        dismiss_tip_sheet(xml_r, label="soft-retry-tip", allow_next=False)
                    time.sleep(1.2)
                    if _is_preview_size_tip(dump()):
                        dismiss_preview_size_tip(label="soft-retry-preview2")
                        time.sleep(1.0)
                st_r = detect_state(dump())
                if st_r == "CREATE_CAMERA" or _is_reel_camera(dump()):
                    _open_gallery_from_camera(dump())
                    time.sleep(1.5)
                if detect_state(dump()) == "CREATE_PICKER":
                    if not _gallery_has_video_tile():
                        _wait_for_gallery_video(max_rounds=3)
                    if not _reel_pick_succeeded(dump())[0] and _gallery_has_video_tile():
                        print("[post] soft retry — pick video now (budget reset)")
                        pick_reel_video(max_attempts=6)
                        xml_p = dump()
                        if _reel_pick_succeeded(xml_p)[0]:
                            _tap_picker_next(xml_p, allow_coord=False) or _advance_next(xml_p)
                            time.sleep(2.0)
                if _advance_to_caption(dest=dest, carousel=carousel, pick_count=pick_count):
                    recovered = True
                    _rt_log("soft_retry_ok")
                else:
                    _send_recover_loop("send_recover2")

        if not recovered:
            print("[FAIL] never reached caption/share (stuck=%s format=%s)" % (st2, fmt))
            if fmt == "reel":
                _rt_fail("advance_timeout", state=detect_state(dump()),
                         snippet=text_block(dump()))
            return emit_fail("POST_TIMEOUT")

    # Stories: link sticker before share, then share, then optional highlight
    if fmt == "story":
        hl = (highlight_title or "").strip()
        if story_link:
            LAST_POST_META["story_link"] = story_link
            adb("shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME")
            adb("shell", "ime", "set", "com.android.adbkeyboard/.AdbIME")
            human_pause(0.3, 0.55)
            human_think()
            if story_add_link_sticker(story_link, sticker_text=(
                    os.environ.get("IG_STORY_LINK_TEXT") or "Chat here").strip()):
                LAST_POST_META["link_ok"] = "1"
            else:
                LAST_POST_META["link_ok"] = "0"
                print("[FAIL] story link sticker required but not confirmed")
                return emit_fail("STORY_LINK_FAIL", note="link_sticker")
        _hide_ime()
        # Exit sticker/text overlay left by link Done before hunting Share.
        for _ in range(3):
            xml_pre = dump()
            if not _is_story_text_tool(xml_pre):
                break
            print("[story] text/sticker overlay before share — Done")
            tap_exact(xml_pre, "Done", label="story-pre-share-done")
            human_pause(0.45, 0.85)
        human_think()
        _section_begin("composer")
        shared = publish_from_composer(fmt="story", max_rounds=3)
        if not shared:
            # Fallback legacy CTAs once
            xml = dump()
            hit = _check_action_limit(note="story_share")
            if hit:
                return hit
            shared = (_tap_publish_cta(xml, fmt="story", label="story-share") or
                      tap_share_btn(xml) or
                      tap_exact(xml, "Share", "Your story", "Add to your story",
                                label="story-share-legacy"))
            if shared:
                time.sleep(3)
        else:
            time.sleep(3)
        if not shared:
            hit = _check_action_limit(note="story_share_fail")
            if hit:
                return hit
            print("[FAIL] story share CTA never left editor")
            return emit_fail("POST_TIMEOUT")
        human_pause(2.0, 3.5)
        if hl:
            try:
                ok_hl = story_add_to_highlight(hl)
                LAST_POST_META["highlight_ok"] = "1" if ok_hl else "0"
                if not ok_hl:
                    print("[FAIL] story highlight required but not confirmed")
                    return emit_fail("STORY_HIGHLIGHT_FAIL", note="highlight")
            except Exception as e:
                LAST_POST_META["highlight_ok"] = "0"
                print("[FAIL] story highlight exception: %s" % e)
                return emit_fail("STORY_HIGHLIGHT_FAIL", note=str(e)[:80])
        live = verify_story_live(username)
        if live == "POST_DONE":
            return live
        print("[post] story share OK — verify inconclusive, POST_DONE (soft)")
        return "POST_DONE"

    adb("shell", "ime", "enable", "com.android.adbkeyboard/.AdbIME")
    adb("shell", "ime", "set", "com.android.adbkeyboard/.AdbIME")
    time.sleep(0.35)
    _section_begin("composer")
    # Reel may still be on EDIT (AI label) — advance Next into caption before typing
    if fmt == "reel":
        for _ in range(3):
            if _section_expired(need=8):
                break
            xml_c = dump()
            st_c = detect_state(xml_c)
            if st_c == "CAPTION_SCREEN" or in_caption_screen(xml_c) or _reel_at_caption(xml_c):
                break
            if st_c == "EDIT_SCREEN" or _reel_edit_ready(xml_c):
                _dismiss_composer_chrome(xml_c, fmt="reel")
                _hide_ime()
                if not _tap_publish_cta(dump(), fmt="reel", label="pre-caption-next"):
                    _tap_edit_advance_next(dump(), label="pre-caption-edit-next")
                time.sleep(0.9)
                continue
            break
    elif fmt in ("feed", "carousel"):
        for _ in range(2):
            if _section_expired(need=8):
                break
            xml_c = dump()
            st_c = detect_state(xml_c)
            if st_c == "CAPTION_SCREEN" or in_caption_screen(xml_c):
                break
            if st_c == "EDIT_SCREEN":
                _hide_ime()
                if not _tap_publish_cta(dump(), fmt="feed", label="feed-pre-caption"):
                    tap_exact(dump(), "Next", "OK", "Done", label="feed-edit-next")
                time.sleep(0.8)
                continue
            break

    # Dismiss music sheet before focusing caption (tap would hit track rows)
    if _is_audio_picker_overlay():
        _dismiss_audio_picker()
        time.sleep(0.5)
    if _dismiss_giphy_overlay():
        time.sleep(0.4)
    if _dismiss_story_to_story_nux() and fmt != "story":
        adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(0.6)
    if _is_clips_nux_sheet():
        _dismiss_clips_nux()
        time.sleep(0.5)

    if caption and fmt != "story":
        _apply_caption(caption, fmt=fmt)
    elif not caption:
        print("[warn] empty caption — sharing without caption")

    # AdbIME {ON} bar sits on the footer and steals Next/Share (Nylah 2026-09-02).
    _adb_ime_off()
    _hide_ime()
    time.sleep(0.25)
    if _is_audio_picker_overlay():
        _dismiss_audio_picker()
        time.sleep(0.4)
    if _dismiss_giphy_overlay():
        time.sleep(0.4)
    if _is_clips_nux_sheet():
        _dismiss_clips_nux()
        time.sleep(0.4)
    if _is_tag_people_sheet():
        _dismiss_tag_people()
        time.sleep(0.4)

    pub_fmt = "reel" if fmt == "reel" else "feed"
    xml_ps = dump()
    _rt_log("pre_share", fmt=fmt, state=detect_state(xml_ps),
            caption=True, force_caption=True,
            snippet=text_block(xml_ps)[:220])
    if _section_expired(need=4):
        print("[FAIL] composer budget before Share")
        return emit_fail("POST_TIMEOUT")
    # Always force caption publish path after typing — never top edit Next
    shared = publish_from_composer(
        fmt=pub_fmt, max_rounds=2 if fmt == "reel" else 3, force_caption=True)
    if not shared:
        hit = _check_action_limit(note="share_blocked")
        if hit:
            return hit
        print("[FAIL] %s publish never left composer" % fmt)
        _rt_fail("share_timeout", state=detect_state(dump()),
                 snippet=text_block(dump()), fmt=fmt)
        return emit_fail("POST_TIMEOUT")
    hit = _check_action_limit(note="after_share")
    if hit:
        return hit
    _rt_log("shared", fmt=fmt, cta="publish_from_composer")
    if _still_in_composer(dump(), fmt="reel" if fmt == "reel" else "feed"):
        print("[FAIL] publish returned True but still on caption")
        return emit_fail("POST_TIMEOUT")
    # Do not sit on New reel staring. Poll upload at most ~8s then verify.
    t_up = time.time()
    while time.time() - t_up < 8:
        xml = dump()
        st = detect_state(xml)
        tb = text_block(xml)
        if any(p in tb for p in ("sharing", "uploading", "posting", "processing")):
            print("[post] still publishing…")
            time.sleep(1.0)
            continue
        if st in ("HUMAN_CHECK", "CAPTCHA", "CONTACT_VERIFY", "CHALLENGE"):
            print("[post] after share gated by %s" % st)
            return emit_fail("POST_CAPTCHA" if st != "CONTACT_VERIFY" else "POST_BLOCKED")
        if _dismiss_giphy_overlay(xml):
            time.sleep(0.4)
            continue
        if _dismiss_story_to_story_nux(xml):
            if fmt != "story":
                adb("shell", "input", "keyevent", "KEYCODE_BACK")
                time.sleep(0.4)
            continue
        if _is_preview_size_tip(xml, tb):
            dismiss_preview_size_tip(xml, label="post-share-preview")
            time.sleep(0.5)
            continue
        if _is_also_share_sheet(tb, st):
            if tap_exact(xml, "Not now", "Skip", "Cancel", "Done", label="post-skip-also"):
                time.sleep(0.5)
                continue
            adb("shell", "input", "keyevent", "KEYCODE_BACK")
            time.sleep(0.5)
            continue
        if st in ("TIP_SHEET", "SYS_PERMISSION"):
            dismiss_tip_sheet(xml, allow_next=False) if st == "TIP_SHEET" else dismiss_android_permission()
            time.sleep(0.5)
            continue
        break

    check_fmt = "reel" if fmt == "reel" else "feed"
    if _still_in_composer(dump(), fmt=check_fmt):
        print("[wait] still in composer after Share — one retry fmt=%s" % fmt)
        if not _section_expired(need=3) and publish_from_composer(fmt=pub_fmt, max_rounds=1):
            time.sleep(2)
        if _still_in_composer(dump(), fmt=check_fmt):
            if fmt == "reel":
                _rt_fail("still_caption", snippet=text_block(dump()))
            return emit_fail("POST_TIMEOUT")

    live = verify_post_live(username, prefer_reels=(fmt == "reel"))
    if fmt == "reel":
        _rt_log("verify_result", result=live)
        if live != "POST_DONE":
            _rt_fail("verify_fail", result=live, snippet=text_block(dump()))
    return live


def do_post(caption=CAPTION, image_path=None, username="", pkg=None,
            format="feed", media_paths=None, story_link="", highlight_title="",
            retry=False):
    """IG publish wrapper: always drop on-phone gallery copies after the attempt."""
    try:
        return _do_post_body(
            caption=caption, image_path=image_path, username=username, pkg=pkg,
            format=format, media_paths=media_paths, story_link=story_link,
            highlight_title=highlight_title, retry=retry,
        )
    finally:
        try:
            cleanup_pushed_media()
        except Exception as e:
            print("[media] cleanup skip: %s" % e)


def do_logout(pkg=None):
    """Settings → Log out after publish. Do not leave the account sitting in IG."""
    pkg = pkg or CURRENT_PKG
    _section_begin("logout", 18)
    print("[logout] start")
    if not _on_own_profile():
        _tap_ig_profile_no_dump()
        time.sleep(0.5)
    xml = dump()
    menu = None
    for n in nodes(xml):
        if attr(n, "clickable") != "true":
            continue
        d = attr(n, "content-desc").strip().lower()
        t = attr(n, "text").strip().lower()
        x, y = bounds_center(n)
        if y is None or y > 420:
            continue
        if d in ("options", "menu", "settings") or t in ("options", "menu") \
           or "settings and activity" in d:
            menu = n
            break
    if menu is not None:
        tapn(menu, "logout-menu")
    else:
        sw, sh = _screen_wh(xml)
        tap(int(sw * 0.93), int(sh * 0.06))
        print("[logout] menu coord")
    time.sleep(0.55)
    xml = dump()
    if not tap_exact(xml, "Settings and activity", "Settings", label="logout-settings"):
        for n in nodes(xml):
            blob = (attr(n, "text") + " " + attr(n, "content-desc")).lower()
            if "settings" in blob and attr(n, "clickable") == "true":
                tapn(n, "logout-settings-fuzzy")
                break
    time.sleep(0.6)
    for _ in range(7):
        if _section_expired(need=1):
            break
        xml = dump()
        if tap_exact(xml, "Log out", "Log Out", "Logout", label="logout"):
            time.sleep(0.5)
            tap_exact(dump(), "Log out", "Log Out", "Logout", label="logout-confirm")
            print("[logout] confirmed")
            time.sleep(0.7)
            return True
        sw, sh = _wm_size()
        swipe(sw // 2, int(sh * 0.78), sw // 2, int(sh * 0.32), 260)
        time.sleep(0.3)
    print("[logout] UI miss — force-stop")
    if pkg:
        force_stop(pkg)
    return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_csv():
    with open(CSVPATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    # Optional:  --serial <adb_serial>   (else uses SERIAL constant at top of file)
    global SERIAL
    _lock_portrait()
    args = [a for a in sys.argv[1:] if a]
    if "--serial" in args:
        i = args.index("--serial")
        if i + 1 < len(args):
            SERIAL = args[i + 1]
            print("[serial] %s" % SERIAL)
            del args[i:i + 2]

    rows = load_csv()

    # CLI:  python ig_loop.py <row> <clone> [nopost|login-only] [--serial SERIAL]
    #   e.g.  python ig_loop.py 0 androif nopost
    #   e.g.  python ig_loop.py 0 com.instagram.androif nopost --serial 988a...
    jobs = JOBS
    pos = [a for a in args if not a.startswith("-") and a not in ("nopost", "login-only")]
    if len(pos) >= 2:
        row_idx = int(pos[0])
        clone = pos[1]
        if not clone.startswith("com.instagram."):
            clone = "com.instagram." + clone
        # Catch common typos like adnroif vs androif
        if "androi" not in clone and "android" not in clone:
            print("[warn] clone does not look like IG (androi*): %s" % clone)
        jobs = [(row_idx, clone)]
        print("CLI override -> row %d on %s" % (row_idx, clone))

    no_post = ("nopost" in args) or ("login-only" in args)
    if no_post:
        print("** LOGIN-ONLY warm-up mode: will log in and NOT post **")

    print("Loaded %d accounts from CSV" % len(rows))
    print("Running %d IG jobs on serial %s\n" % (len(jobs), SERIAL))

    results = []
    for row_idx, pkg in jobs:
        if row_idx >= len(rows):
            print("[skip] row %d out of range" % row_idx); continue
        r = rows[row_idx]
        username   = r["username"]
        password   = r["password"]
        tfa_secret = r["tfa_secret"]

        login_result = do_login(pkg, username, password, tfa_secret)
        if login_result != "LOGGED_IN":
            results.append((username, pkg, login_result))
            print("[result] %s -> %s\n" % (username, login_result))
            time.sleep(5); continue

        if no_post:
            results.append((username, pkg, "LOGGED_IN_NOPOST"))
            print("[result] %s -> LOGGED_IN (warm-up, no post)\n" % username)
            time.sleep(5); continue

        post_result = do_post(CAPTION, username=username, pkg=pkg)
        results.append((username, pkg, post_result))
        print("[result] %s -> %s\n" % (username, post_result))
        time.sleep(5)

    print("=== IG SUMMARY ===")
    for u, p, o in results:
        print("  %-25s | %-20s | %s" % (u, p.split(".")[-1], o))

    with open(RESULTS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["username", "clone", "result"])
        for u, p, o in results:
            w.writerow([u, p, o])
    print("\nResults saved to", RESULTS)

if __name__ == "__main__":
    main()
