# -*- coding: utf-8 -*-
# Download unique IG Nomix APKs from the long-term Drive archive.
#
# Folder: https://drive.google.com/drive/folders/1bSyrZKxt9tSkBhEoFmgXHRoEeZZUBKJA
# Share that folder with the farm service_account.json client_email (Viewer).
#
#   python -u download_ig_from_drive.py            # list
#   python -u download_ig_from_drive.py --get 20   # download 20 unused APKs
from farm_root import ROOT
import os
import sys
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

FOLDER_FILE = os.path.join(ROOT, "ig_clones_drive_folder.txt")
DEFAULT_FOLDER = "1bSyrZKxt9tSkBhEoFmgXHRoEeZZUBKJA"
DEST = os.path.join(ROOT, "nomix_api", "downloaded_ig")
SA_CANDIDATES = [
    os.environ.get("IG_SA_FILE") or "",
    os.path.join(ROOT, "service_account.json"),
    r"C:\farm\threads-farm\service_account.json",
    r"C:\threads-android\service_account.json",
]
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MIN_APK = 80 * 1024 * 1024


def folder_id():
    if os.path.isfile(FOLDER_FILE):
        try:
            fid = open(FOLDER_FILE, encoding="utf-8").read().strip().split()[0]
            if fid:
                return fid
        except Exception:
            pass
    return DEFAULT_FOLDER


def sa_path():
    for p in SA_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return ""


def drive_svc():
    path = sa_path()
    if not path:
        raise SystemExit("No service_account.json (native / threads-farm / threads-android)")
    creds = Credentials.from_service_account_file(path, scopes=SCOPES)
    return build("drive", "v3", credentials=creds), path


def list_files(drive, fid, prefix=""):
    """Recursive file list: (path, id, size, mime)."""
    out, tok = [], None
    q = "'%s' in parents and trashed=false" % fid
    while True:
        resp = drive.files().list(
            q=q, pageSize=1000, pageToken=tok,
            fields="nextPageToken,files(id,name,mimeType,size)",
            supportsAllDrives=True, includeItemsFromAllDrives=True,
            orderBy="name",
        ).execute()
        for f in resp.get("files") or []:
            name = f.get("name") or ""
            path = ("%s/%s" % (prefix, name)).strip("/")
            mime = f.get("mimeType") or ""
            if mime == "application/vnd.google-apps.folder":
                out.extend(list_files(drive, f["id"], path))
            else:
                try:
                    sz = int(f.get("size") or 0)
                except ValueError:
                    sz = 0
                out.append({
                    "path": path, "id": f["id"], "size": sz,
                    "name": name, "mime": mime,
                })
        tok = resp.get("nextPageToken")
        if not tok:
            break
    return out


def is_apk(row):
    n = (row.get("name") or "").lower()
    return n.endswith(".apk") and row.get("size", 0) >= MIN_APK


def download_one(drive, file_id, dest_path):
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    req = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    with open(dest_path, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req, chunksize=8 * 1024 * 1024)
        done = False
        while not done:
            status, done = dl.next_chunk()
            if status:
                print("    %d%%" % int(status.progress() * 100), flush=True)


def main():
    want = 0
    args = sys.argv[1:]
    for i, x in enumerate(args):
        if x == "--get" and i + 1 < len(args):
            want = max(0, int(args[i + 1]))
        if x == "--get" and (i + 1 >= len(args) or args[i + 1].startswith("-")):
            want = 20
    if "--get" in args and want == 0:
        want = 20

    fid = folder_id()
    print("folder=%s" % fid)
    drive, sap = drive_svc()
    email = ""
    try:
        import json
        email = (json.load(open(sap, encoding="utf-8")).get("client_email") or "")
    except Exception:
        pass
    print("sa=%s  email=%s" % (os.path.basename(sap), email))
    try:
        meta = drive.files().get(
            fileId=fid, fields="id,name,mimeType,shared",
            supportsAllDrives=True,
        ).execute()
        print("folder_get OK name=%r shared=%s" % (meta.get("name"), meta.get("shared")))
    except Exception as e:
        print("folder_get FAIL: %s" % e)
        print("Share the Drive folder with: %s  (Viewer)" % (email or "the farm SA email"))
        sys.exit(2)

    rows = list_files(drive, fid)
    apks = [r for r in rows if is_apk(r)]
    other = [r for r in rows if not is_apk(r)]
    print("files=%d  full_apks=%d  other=%d" % (len(rows), len(apks), len(other)))
    for r in apks[:12]:
        print("  %6.0f MB  %s" % (r["size"] / 1e6, r["path"]))
    if len(apks) > 12:
        print("  … +%d more" % (len(apks) - 12))
    if other[:8]:
        print("non-apk sample:")
        for r in other[:8]:
            print("  %s  %s" % (r["mime"][:40], r["path"]))

    if want <= 0:
        print("List only. Download: python -u download_ig_from_drive.py --get 20")
        return

    os.makedirs(DEST, exist_ok=True)
    have = set()
    for f in os.listdir(DEST):
        if f.lower().endswith(".apk"):
            have.add(f.lower())
    picked = []
    for r in apks:
        base = os.path.basename(r["name"])
        if base.lower() in have:
            continue
        dest = os.path.join(DEST, base)
        if os.path.isfile(dest) and os.path.getsize(dest) >= MIN_APK:
            have.add(base.lower())
            continue
        picked.append(r)
        if len(picked) >= want:
            break
    print("download dest=%s  already=%d  picking=%d" % (DEST, len(have), len(picked)))
    if not picked:
        print("Nothing new to download (enough APKs already on disk, or folder empty).")
        return
    ok = fail = 0
    for i, r in enumerate(picked, 1):
        base = os.path.basename(r["name"])
        dest = os.path.join(DEST, base)
        tmp = dest + ".part"
        print("[%d/%d] %s (%.0f MB)" % (i, len(picked), base, r["size"] / 1e6), flush=True)
        try:
            download_one(drive, r["id"], tmp)
            os.replace(tmp, dest)
            ok += 1
            print("  saved %s" % dest)
        except Exception as e:
            fail += 1
            print("  FAIL %s: %s" % (base, e))
            try:
                os.remove(tmp)
            except OSError:
                pass
    print("done ok=%d fail=%d  dir=%s" % (ok, fail, DEST))
    print("Next: python -u install_ig_unique.py --per-phone 1")


if __name__ == "__main__":
    main()
