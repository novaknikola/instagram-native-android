# -*- coding: utf-8 -*-
# ig_media_names.py — single naming rules for IG farm media (PC + phone).
#
# Why: Drive names like "image (101).jpg" break:
#   - Windows paths / adb edge cases
#   - Android MediaStore file:// scan URIs
#   - IG Recents tile matching
# Reel already sanitized on push; feed/carousel must follow the same contract.
#
# Contract:
#   * Local download basename: only [A-Za-z0-9._-], unique via Drive id prefix
#   * On-device remote basename: prefix + serial + ms + safe stem + ext
#   * Drive display "name" in results stays the ORIGINAL Drive filename
import os
import re
import time


def sanitize_stem(stem, fallback="media"):
    safe = re.sub(r"[^\w.\-]+", "_", (stem or "").strip()).strip("._") or fallback
    return safe[:60]


def normalize_ext(ext, default=".jpg"):
    ext = (ext or default).lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext == ".jpeg":
        return ".jpg"
    return ext


def safe_local_filename(drive_name, file_id=""):
    """PC workdir basename after Drive download (no spaces/parens).

    Includes a short Drive id so two 'image (1).jpg' files never collide.
    """
    stem, ext = os.path.splitext(os.path.basename(drive_name or "") or "media")
    safe = sanitize_stem(stem)
    ext = normalize_ext(ext)
    id8 = re.sub(r"[^\w]+", "", (file_id or ""))[:8]
    if id8:
        return "igdl_%s_%s%s" % (id8, safe, ext)
    return "igdl_%s%s" % (safe, ext)


def remote_basename(local_path, prefix="ig", serial=""):
    """On-device filename under /sdcard/Pictures|Movies|DCIM — unique per push."""
    raw = os.path.basename(local_path or "") or "media"
    stem, ext = os.path.splitext(raw)
    # Strip our own igdl_/ig_/reel_ prefixes from stem for shorter Recents labels
    for pfx in ("igdl_", "ig_", "reel_"):
        if stem.lower().startswith(pfx):
            # keep after first two underscores if id-prefixed: igdl_ID_name
            parts = stem.split("_", 2)
            if len(parts) >= 3 and pfx == "igdl_":
                stem = parts[2]
            break
    safe = sanitize_stem(stem)
    ext = normalize_ext(ext, default=".mp4" if prefix == "reel" else ".jpg")
    tag = "%d" % int(time.time() * 1000)
    ser = re.sub(r"[^\w]+", "", (serial or "")[-6:]) or "dev"
    return "%s_%s_%s_%s%s" % (prefix, ser, tag, safe[:40], ext)


def is_safe_basename(name):
    """True if basename has no whitespace or shell/MediaStore-hostile chars."""
    base = os.path.basename(name or "")
    if not base or any(c.isspace() for c in base):
        return False
    return re.fullmatch(r"[\w.\-]+", base) is not None
