# -*- coding: utf-8 -*-
# drive_content_ig_account.py — IG content from Drive.
#
# Native farm: ONE Drive folder PER PHONE (serial).
#   ig_phone_drive_map.json  { "SERIAL": "FOLDER_ID", ... }
#   or parent folder ig_phones_drive_folder.txt whose children are named
#   by serial / last-8.
# Layout under each phone folder:
#   Posts/     → format=feed
#   Reels/     → format=reel
#   Stories/   → format=story
#   Carousel/  → format=carousel (optional)
# Optional captions.txt in the phone folder root.
#
# Share each phone folder (or the parent) with service_account.json client_email.
from farm_root import ROOT
import os
import json
import re
from googleapiclient.http import MediaIoBaseDownload

import drive_content_ig as dc
import ig_media_names as mn

FORMATS = ("feed", "story", "carousel", "reel")
# Client Drive names first; legacy lowercase second. feed == Posts (not a separate thing).
FORMAT_FOLDER_ALIASES = {
    "feed": ("posts", "post", "feed", "images"),
    "reel": ("reels", "reel"),
    "carousel": ("carousel", "carousels"),
    "story": ("stories", "story"),
}
# When creating missing subfolders under a phone folder.
FORMAT_CREATE_NAMES = {
    "feed": "Posts",
    "reel": "Reels",
    "carousel": "Carousel",
    "story": "Stories",
}
_BASE = os.environ.get("IG_FARM_BASE", ROOT)
MAP_JSON = os.path.join(_BASE, "ig_account_drive_map.json")
PHONE_MAP_JSON = os.path.join(_BASE, "ig_phone_drive_map.json")
SHARED_FOLDER_FILE = os.path.join(_BASE, "ig_shared_drive_folder.txt")
SHARED_FOLDER_ENV = "IG_SHARED_DRIVE_FOLDER"
PHONES_ROOT_FILE = os.path.join(_BASE, "ig_phones_drive_folder.txt")
PHONES_ROOT_ENV = "IG_PHONES_DRIVE_FOLDER"
# Optional override: Reels videos live in a dedicated Drive folder (not under shared root).
# Env IG_REELS_FOLDER or one-line file ig_reels_folder.txt
REELS_FOLDER_FILE = os.path.join(_BASE, "ig_reels_folder.txt")
REELS_FOLDER_ENV = "IG_REELS_FOLDER"
# Optional per-user override Sheet / JSON (only if you need exceptions)
ACCOUNT_MAP_SHEET = os.environ.get("IG_ACCOUNT_MAP_SHEET", "").strip()
USED_DIR = os.path.join(_BASE, "used_ig_accounts")
# Farm-wide claim ledger when shared Drive is on — one file per format so
# accounts do NOT all soft-claim the same first Reel/Post (client 2026-08-11).
SHARED_POOL_USED = os.path.join(USED_DIR, "shared_pool_%s.txt")
SHARED_POOL_LOCK = os.path.join(USED_DIR, "shared_pool_%s.lock")

try:
    import ig_content_line as cl
except Exception:
    cl = None


def _safe_name(username):
    return re.sub(r"[^\w.\-]+", "_", (username or "").strip()) or "unknown"


class _ClaimLock(object):
    """Exclusive lock so parallel phones cannot claim the same Drive file."""

    def __init__(self, path, timeout=90.0):
        self.path = path
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        import time
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + self.timeout
        while True:
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                try:
                    os.write(self.fd, ("%s %s\n" % (os.getpid(), time.time())).encode("utf-8"))
                except Exception:
                    pass
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                    if age > 180:
                        os.remove(self.path)
                        continue
                except Exception:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError("claim lock timeout: %s" % self.path)
                time.sleep(0.25)

    def __exit__(self, exc_type, exc, tb):
        try:
            if self.fd is not None:
                os.close(self.fd)
        except Exception:
            pass
        self.fd = None
        try:
            os.remove(self.path)
        except Exception:
            pass
        return False


def shared_folder_id():
    """Farm-wide content Drive folder — every account pulls from here."""
    env = (os.environ.get(SHARED_FOLDER_ENV) or "").strip()
    if env:
        return env
    if os.path.isfile(SHARED_FOLDER_FILE):
        try:
            for ln in open(SHARED_FOLDER_FILE, encoding="utf-8"):
                s = ln.split("#")[0].strip()
                if s:
                    return s
        except Exception as e:
            print("[drive-acct] shared folder file read fail: %s" % e)
    return ""


def set_shared_folder_id(folder_id):
    fid = (folder_id or "").strip()
    parent = os.path.dirname(SHARED_FOLDER_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(SHARED_FOLDER_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Shared IG content Drive folder ID (all accounts)\n")
        fh.write(fid + "\n")
    return fid


def phones_root_id():
    env = (os.environ.get(PHONES_ROOT_ENV) or "").strip()
    if env:
        return env
    if os.path.isfile(PHONES_ROOT_FILE):
        try:
            for ln in open(PHONES_ROOT_FILE, encoding="utf-8"):
                s = ln.split("#")[0].strip()
                if s:
                    return s
        except Exception as e:
            print("[drive-acct] phones root file read fail: %s" % e)
    return ""


def set_phones_root_id(folder_id):
    fid = (folder_id or "").strip()
    parent = os.path.dirname(PHONES_ROOT_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(PHONES_ROOT_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Parent Drive folder: one child folder per phone (named by serial or last-8)\n")
        fh.write(fid + "\n")
    return fid


def load_phone_map():
    """serial (or last-8) -> phone Drive folder ID."""
    out = {}
    if not os.path.exists(PHONE_MAP_JSON):
        return out
    try:
        raw = json.load(open(PHONE_MAP_JSON, encoding="utf-8"))
        if not isinstance(raw, dict):
            return out
        for k, v in raw.items():
            if k and v and not str(k).startswith("_"):
                out[str(k).strip()] = str(v).strip()
    except Exception as e:
        print("[drive-acct] phone map json read fail: %s" % e)
    return out


def save_phone_map(mapping):
    parent = os.path.dirname(PHONE_MAP_JSON)
    if parent:
        os.makedirs(parent, exist_ok=True)
    clean = {}
    for k, v in (mapping or {}).items():
        k, v = str(k).strip(), str(v).strip()
        if k and v and not k.startswith("_"):
            clean[k] = v
    tmp = PHONE_MAP_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, PHONE_MAP_JSON)
    return clean


def _lookup_serial(mapping, serial):
    serial = (serial or "").strip()
    if not serial or not mapping:
        return ""
    if serial in mapping:
        return mapping[serial]
    tail8 = serial[-8:] if len(serial) >= 8 else serial
    if tail8 in mapping:
        return mapping[tail8]
    hits = []
    for k, v in mapping.items():
        ks = (k or "").strip()
        if not ks:
            continue
        if serial.endswith(ks) or ks.endswith(tail8) or ks == tail8:
            hits.append(v)
    uniq = list(dict.fromkeys(hits))
    if len(uniq) == 1:
        return uniq[0]
    return ""


_ROOT_KIDS_CACHE = {}  # parent_id -> {name.lower(): id}


def _phones_root_children(drive, parent_id):
    if not drive or not parent_id:
        return {}
    cached = _ROOT_KIDS_CACHE.get(parent_id)
    if cached is not None:
        return cached
    kids = _child_folders(drive, parent_id)
    _ROOT_KIDS_CACHE[parent_id] = kids
    return kids


def folder_from_phones_root(drive, serial):
    """Match a child of the phones parent folder to this serial."""
    root = phones_root_id()
    serial = (serial or "").strip()
    if not root or not serial or drive is None:
        return ""
    kids = _phones_root_children(drive, root)
    tail8 = serial[-8:].lower() if len(serial) >= 8 else serial.lower()
    sl = serial.lower()
    for name, fid in kids.items():
        n = (name or "").lower().strip()
        if n in (sl, tail8) or n.endswith(tail8) or sl.endswith(n):
            return fid
    return ""


def has_phone_drive():
    return bool(load_phone_map() or phones_root_id())


def reels_folder_id():
    """Legacy farm-wide Reels folder. Unused when a phone folder is resolved."""
    env = (os.environ.get(REELS_FOLDER_ENV) or "").strip()
    if env:
        return env
    if os.path.isfile(REELS_FOLDER_FILE):
        try:
            for ln in open(REELS_FOLDER_FILE, encoding="utf-8"):
                s = ln.split("#")[0].strip()
                if s:
                    return s
        except Exception as e:
            print("[drive-acct] reels folder file read fail: %s" % e)
    return ""


def set_reels_folder_id(folder_id):
    fid = (folder_id or "").strip()
    parent = os.path.dirname(REELS_FOLDER_FILE)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(REELS_FOLDER_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Dedicated IG Reels Drive folder ID (videos)\n")
        fh.write(fid + "\n")
    return fid


def _used_path(username, fmt, serial=""):
    """Prefer per-phone ledger; else content-line / per-user."""
    serial = (serial or "").strip()
    if serial:
        os.makedirs(USED_DIR, exist_ok=True)
        return os.path.join(USED_DIR, "phone_%s_%s.txt" % (_safe_name(serial), fmt))
    if cl is not None:
        try:
            return cl.used_path(username, fmt)
        except Exception:
            pass
    os.makedirs(USED_DIR, exist_ok=True)
    return os.path.join(USED_DIR, "%s_%s.txt" % (_safe_name(username), fmt))


def _pool_used_path(fmt):
    """Farm-wide used/claim ledger for shared Posts/Reels/Carousel."""
    os.makedirs(USED_DIR, exist_ok=True)
    return SHARED_POOL_USED % ((fmt or "feed").lower())


def _pool_lock_path(fmt):
    os.makedirs(USED_DIR, exist_ok=True)
    return SHARED_POOL_LOCK % ((fmt or "feed").lower())


def _append_ids(path, ids):
    if not ids:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for i in ids:
            fh.write(i + "\n")


def _remove_ids(path, ids):
    """Drop ids from a ledger file so a failed reel can be claimed again."""
    want = set(i for i in (ids or []) if i)
    if not want or not os.path.isfile(path):
        return
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except Exception:
        return
    kept = [ln for ln in lines if ln.strip() and ln.strip() not in want]
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            for ln in kept:
                fh.write(ln + "\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass


def release_claim(username, fmt, ids, serial=""):
    """Un-claim Drive ids after a failed post/download so the phone folder can retry."""
    fmt = (fmt or "feed").lower()
    ids = [i for i in (ids or []) if i]
    if not ids:
        return
    lock_path = os.path.join(
        USED_DIR, "%s_%s.lock" % (_safe_name((serial or username or "x")), fmt)
    )
    try:
        with _ClaimLock(lock_path):
            _remove_ids(_used_path(username, fmt, serial=serial), ids)
        print("[drive-acct] RELEASE %s/%s %d id(s) (post not live — retry allowed)"
              % (serial or username, fmt, len(ids)))
    except Exception as e:
        print("[drive-acct] RELEASE skip: %s" % e)


def cleanup_local_media(paths):
    """Delete PC workdir copies after the job (success or fail). Drive stays."""
    n = 0
    for p in paths or []:
        p = os.path.abspath(p or "")
        if not p or not os.path.isfile(p):
            continue
        try:
            os.remove(p)
            n += 1
        except Exception:
            pass
    if n:
        print("[drive-acct] deleted %d local file(s) after job" % n)
    return n


def _media_sort_key(f):
    """Stable order-by-order: preferred ext first, then Drive name."""
    n = (f.get("name") or "").lower()
    if n.endswith((".png", ".jpg", ".jpeg", ".mp4", ".mov")):
        rank = 0
    elif n.endswith(".webp"):
        rank = 2
    else:
        rank = 1
    return (rank, n, f.get("id") or "")


def load_map(sheets=None):
    """Optional username -> folder_id overrides (rarely needed)."""
    out = {}
    if os.path.exists(MAP_JSON):
        try:
            raw = json.load(open(MAP_JSON, encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if k and v and not str(k).startswith("_"):
                        out[str(k).strip()] = str(v).strip()
        except Exception as e:
            print("[drive-acct] map json read fail: %s" % e)
    if ACCOUNT_MAP_SHEET and sheets is not None:
        try:
            rows = sheets.spreadsheets().values().get(
                spreadsheetId=ACCOUNT_MAP_SHEET, range="A2:B5000"
            ).execute().get("values", [])
            for r in rows:
                if len(r) >= 2 and r[0].strip() and r[1].strip():
                    out[r[0].strip()] = r[1].strip()
        except Exception as e:
            print("[drive-acct] map sheet read fail: %s" % e)
    return out


def save_map_local(mapping):
    parent = os.path.dirname(MAP_JSON)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = MAP_JSON + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(mapping, fh, indent=2, sort_keys=True)
    os.replace(tmp, MAP_JSON)
    if cl is not None:
        try:
            cl.bootstrap_from_drive_map(mapping)
        except Exception as e:
            print("[drive-acct] content-line bootstrap: %s" % e)


def folder_for(username, sheets=None, serial="", drive=None):
    """Resolve this phone's Drive root. Username map is legacy fallback only."""
    serial = (serial or "").strip()
    if serial:
        fid = _lookup_serial(load_phone_map(), serial)
        if fid:
            return fid
        if drive is None:
            try:
                drive, _ = dc.services()
            except Exception:
                drive = None
        fid = folder_from_phones_root(drive, serial)
        if fid:
            return fid
    u = (username or "").strip()
    if cl is not None and u:
        try:
            fid, _lid = cl.drive_folder_for(u)
            if fid:
                return fid
        except Exception:
            pass
    if u:
        m = load_map(sheets)
        return m.get(u) or ""
    return ""


def has_content_drive(username="", sheets=None, serial=""):
    return bool(folder_for(username, sheets, serial=serial))


def _child_folders(drive, parent_id):
    """Map lowercase folder name -> id for direct children."""
    q = ("'%s' in parents and trashed=false and "
         "mimeType='application/vnd.google-apps.folder'" % parent_id)
    out = {}
    for f in dc._drive_list(drive, q, fields="nextPageToken,files(id,name)"):
        name = (f.get("name") or "").strip()
        if name:
            out[name.lower()] = f["id"]
    return out


def _find_subfolder(drive, parent_id, name):
    """Find child folder by name (case-insensitive)."""
    want = name.lower()
    kids = _child_folders(drive, parent_id)
    return kids.get(want)


def _find_format_subfolder(drive, parent_id, fmt):
    """Resolve format → Drive subfolder. Posts == feed; Reels == reel."""
    fmt = (fmt or "feed").lower()
    aliases = FORMAT_FOLDER_ALIASES.get(fmt, (fmt,))
    kids = _child_folders(drive, parent_id)
    for alias in aliases:
        fid = kids.get(alias)
        if fid:
            return fid, alias
    return None, None


def format_folder_id(drive, username, fmt, sheets=None, serial=""):
    fmt = (fmt or "feed").lower()
    if fmt not in FORMATS:
        fmt = "feed"
    root = folder_for(username, sheets, serial=serial, drive=drive)
    if not root:
        return None
    sub, matched = _find_format_subfolder(drive, root, fmt)
    if sub:
        if matched and matched != fmt:
            print("[drive-acct] %s → folder %r (phone=%s)"
                  % (fmt, matched, (serial or "")[-8:]))
        return sub
    print("[drive-acct] missing %s subfolder (Posts/Reels/Stories) under phone folder %s"
          % (fmt, (serial or username or "")[-12:]))
    return None


def list_media(drive, username, fmt, sheets=None, serial=""):
    fid = format_folder_id(drive, username, fmt, sheets, serial=serial)
    if not fid:
        return []
    return _list_media_in_folder(drive, fid, fmt)


def _list_media_in_folder(drive, fid, fmt):
    q = ("'%s' in parents and trashed=false and "
         "(mimeType contains 'image/' or mimeType contains 'video/' "
         "or mimeType = 'application/octet-stream')" % fid)
    files = dc._drive_list(drive, q)
    out = []
    for f in files:
        mt = (f.get("mimeType") or "").lower()
        name = (f.get("name") or "").lower()
        if fmt == "reel":
            if ("video/" in mt or name.endswith((".mp4", ".mov", ".webm", ".mkv"))):
                out.append({"id": f["id"], "name": f["name"], "mime": mt})
        else:
            if ("image/" in mt or name.endswith(
                    (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"))):
                out.append({"id": f["id"], "name": f["name"], "mime": mt})
            elif fmt == "story" and "video/" in mt:
                out.append({"id": f["id"], "name": f["name"], "mime": mt})
    return out


def _union_format_ledgers(fmt):
    """Merge per-line/per-user ledgers so old same-file soft-claims stay skipped."""
    out = set()
    fmt = (fmt or "feed").lower()
    suffix = "_%s.txt" % fmt
    try:
        for name in os.listdir(USED_DIR):
            if not name.endswith(suffix):
                continue
            if name.startswith("shared_pool_"):
                continue
            if name.endswith(".lock"):
                continue
            out |= dc._used_ids(os.path.join(USED_DIR, name))
    except Exception:
        pass
    return out


def next_media(drive, username, fmt, workdir, count=1, sheets=None, serial=""):
    """Download next unused media from THIS PHONE's Drive folder.

    Ledger: used_ig_accounts/phone_{serial}_{fmt}.txt — not a farm-wide pool.
    """
    fmt = (fmt or "feed").lower()
    serial = (serial or "").strip()
    want = max(1, count if fmt == "carousel" else 1)
    files = list_media(drive, username, fmt, sheets, serial=serial)
    if not files:
        print("[drive-acct] %s/%s: Drive folder empty (phone=%s)"
              % (username, fmt, serial[-8:] if serial else "?"))
        return None if want <= 1 else []

    claim_path = _used_path(username, fmt, serial=serial)
    lock_path = os.path.join(
        USED_DIR, "%s_%s.lock" % (_safe_name(serial or username or "x"), fmt)
    )

    take = []
    try:
        with _ClaimLock(lock_path):
            used = dc._used_ids(claim_path)
            unused = [f for f in files if f["id"] not in used]
            unused.sort(key=_media_sort_key)
            if not unused:
                print("[drive-acct] %s/%s: no unused media (folder=%d claimed=%d phone=%s)"
                      % (username, fmt, len(files), len(used),
                         serial[-8:] if serial else "?"))
                return None if want <= 1 else []
            take = unused[:want]
            ids = [f["id"] for f in take]
            _append_ids(claim_path, ids)
            for i, f in enumerate(take):
                ordinal = len(used) + i + 1
                print("[drive-acct] CLAIM %s #%d/%d → %s phone=%s | %s | id=%s…"
                      % (fmt, ordinal, len(files), username,
                         serial[-8:] if serial else "?",
                         f.get("name"), (f.get("id") or "")[:10]))
    except TimeoutError as e:
        print("[drive-acct] CLAIM lock fail: %s" % e)
        return None if want <= 1 else []

    os.makedirs(workdir, exist_ok=True)
    out = []
    failed_ids = []
    min_bytes = 50 * 1024 if fmt == "reel" else 1000
    for f in take:
        local_name = mn.safe_local_filename(f["name"], f["id"])
        path = os.path.join(workdir, local_name)
        print("[drive-acct] download %s/%s %s -> %s"
              % (username, fmt, f["name"], path))
        try:
            req = drive.files().get_media(fileId=f["id"])
            with open(path, "wb") as fh:
                dl, done = MediaIoBaseDownload(fh, req), False
                while not done:
                    _, done = dl.next_chunk()
        except Exception as e:
            print("[drive-acct] FAIL download: %s: %s" % (type(e).__name__, e))
            failed_ids.append(f["id"])
            continue
        if not os.path.isfile(path) or os.path.getsize(path) < min_bytes:
            print("[drive-acct] FAIL download empty/tiny: %s" % path)
            failed_ids.append(f["id"])
            try:
                os.remove(path)
            except Exception:
                pass
            continue
        out.append({"id": f["id"], "name": f["name"], "path": path,
                    "local_name": local_name})

    if failed_ids:
        try:
            with _ClaimLock(lock_path):
                _remove_ids(claim_path, failed_ids)
            print("[drive-acct] download failed for %d id(s) — unclaimed for retry"
                  % len(failed_ids))
        except Exception as e:
            print("[drive-acct] download failed for %d claimed id(s) — unclaim skip: %s"
                  % (len(failed_ids), e))

    if not out:
        return None if want <= 1 else []
    if fmt == "carousel" or want > 1:
        return out
    return out[0]


def caption_for(username, sheets=None, fmt="feed", serial=""):
    """Prefer captions.txt in this phone's Drive folder root."""
    root = folder_for(username, sheets, serial=serial)
    if not root:
        return ""
    try:
        drive, _ = dc.services() if sheets is None else (None, sheets)
        if drive is None:
            drive, _ = dc.services()
        # Look for captions.txt in root
        q = ("'%s' in parents and trashed=false and name='captions.txt'" % root)
        files = dc._drive_list(drive, q, fields="nextPageToken,files(id,name)")
        if not files:
            return ""
        # Download to temp and read first non-empty line (rotate by hash of fmt+user)
        import tempfile
        path = os.path.join(tempfile.gettempdir(), "ig_cap_%s.txt" % _safe_name(username))
        req = drive.files().get_media(fileId=files[0]["id"])
        with open(path, "wb") as fh:
            dl, done = MediaIoBaseDownload(fh, req), False
            while not done:
                _, done = dl.next_chunk()
        lines = [ln.strip() for ln in open(path, encoding="utf-8", errors="replace")
                 if ln.strip()]
        if not lines:
            return ""
        idx = abs(hash(username + fmt)) % len(lines)
        return lines[idx]
    except Exception as e:
        print("[drive-acct] caption_for fail: %s" % e)
        return ""


def mark_used(drive, file_id, username, fmt, serial=""):
    """After verified POST_DONE: lock phone ledger. Drive file stays."""
    fmt = (fmt or "feed").lower()
    ids = [file_id] if file_id else []
    if not ids:
        return
    line_path = _used_path(username, fmt, serial=serial)
    _append_ids(line_path, ids)
    print("[drive-acct] USED %s/%s phone=%s %s (Drive file kept; ledger skip next claim)"
          % (username, fmt, (serial or "")[-8:], (file_id or "")[:12]))


def ensure_subfolders(drive, username, sheets=None, serial=""):
    """Create Posts/Reels/Stories (and Carousel) under the phone folder."""
    root = folder_for(username, sheets, serial=serial, drive=drive)
    if not root:
        return False, "no folder_id for phone %s / %s" % (serial[-8:] if serial else "?", username)
    created = []
    kids = _child_folders(drive, root)
    for fmt in ("feed", "reel", "story", "carousel"):
        aliases = FORMAT_FOLDER_ALIASES.get(fmt, (fmt,))
        if any(a in kids for a in aliases):
            continue
        name = FORMAT_CREATE_NAMES.get(fmt, fmt)
        meta = drive.files().create(
            body={
                "name": name,
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [root],
            },
            fields="id,name",
            supportsAllDrives=True,
        ).execute()
        created.append(name)
        kids[name.lower()] = meta.get("id")
        print("[drive-acct] created subfolder %s/%s id=%s"
              % (serial or username, name, meta.get("id")))
    return True, "ok created=%s" % created


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    drive, sheets = dc.services()
    m = load_map(sheets)
    shared = shared_folder_id()
    print("shared_folder=%s" % (shared or "NONE"))
    print("account map entries: %d (json=%s sheet=%s)"
          % (len(m), os.path.exists(MAP_JSON), bool(ACCOUNT_MAP_SHEET)))
    if args and args[0] == "--ensure" and len(args) > 1:
        for u in args[1:]:
            ok, msg = ensure_subfolders(drive, u, sheets)
            print("[%s] %s %s" % (u, ok, msg))
    elif args and args[0] == "--diagnose":
        root = shared or (args[1] if len(args) > 1 else "")
        if not root:
            print("usage: drive_content_ig_account.py --diagnose [FOLDER_ID]")
            raise SystemExit(1)
        kids = _child_folders(drive, root)
        print("children folders: %s" % sorted(kids.keys()))
        for fmt in FORMATS:
            sub, matched = _find_format_subfolder(drive, root, fmt)
            print("  format=%-9s → %s (%s)" % (fmt, matched or "ROOT_FALLBACK", sub or root))
            # Peek media count without username ledger
            q = ("'%s' in parents and trashed=false" % (sub or root))
            n = len(dc._drive_list(drive, q, fields="nextPageToken,files(id,name,mimeType)"))
            claimed = len(dc._used_ids(_pool_used_path(fmt))) if shared else 0
            print("             files_in_folder=%d  shared_pool_claimed=%d  ledger=%s"
                  % (n, claimed, _pool_used_path(fmt) if shared else "(per-account)"))
    elif args:
        for u in args:
            fid = folder_for(u, sheets)
            print("[%s] root=%s" % (u, fid or "NONE"))
            if fid:
                for fmt in FORMATS:
                    files = list_media(drive, u, fmt, sheets)
                    used = dc._used_ids(_used_path(u, fmt))
                    print("  %s: %d files, %d unused"
                          % (fmt, len(files), len([f for f in files if f["id"] not in used])))
    else:
        for u, fid in list(m.items())[:20]:
            print("  %s -> %s" % (u, fid))
