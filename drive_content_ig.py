# -*- coding: utf-8 -*-
# drive_content_ig.py - Instagram content from NEW Drive/Sheets (from scratch).
# Same API as drive_content.py so run_ig_device can call it. Threads ledgers untouched.
#
# BEFORE first real run: create Drive image folders + Captions sheets, share with the
# farm service account, then replace PLACEHOLDER_* IDs below.
from farm_root import ROOT
import os, time
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

import ig_media_names as mn

SA_FILE = os.environ.get("IG_SA_FILE") or os.path.join(ROOT, 'service_account.json')
DL_DIR  = os.path.join(ROOT, 'content')

# Captions: https://docs.google.com/spreadsheets/d/1hcKQBFxiMc893NDM2EUTohMHoTDa4TcjxZPqlUBATKY/
# Images folder: https://drive.google.com/drive/folders/1_r1Rnom8XldOIQ_VVXFu-5kWiy2ZdIcI
# Share BOTH with the farm service_account.json client_email (Viewer+ for images).
MODELS = {
    "ig": {
        "images":   "1_r1Rnom8XldOIQ_VVXFu-5kWiy2ZdIcI",
        "captions": "1hcKQBFxiMc893NDM2EUTohMHoTDa4TcjxZPqlUBATKY",
        "used":     os.path.join(ROOT, 'used_ig.txt'),
    },
}

def _model(model):
    m = (model or "ig").strip().lower()
    return m if m in MODELS else "ig"


SCOPES = [
    "https://www.googleapis.com/auth/drive",                  # read + trash images
    "https://www.googleapis.com/auth/spreadsheets.readonly",  # read captions
]


def services():
    creds = Credentials.from_service_account_file(SA_FILE, scopes=SCOPES)
    return build("drive", "v3", credentials=creds), build("sheets", "v4", credentials=creds)


def _used_ids(used_file):
    try:
        return set(open(used_file, encoding="utf-8").read().split())
    except FileNotFoundError:
        return set()


def _drive_list(drive, q, fields="nextPageToken,files(id,name,mimeType)"):
    """List with Shared-Drive flags. Without includeItemsFromAllDrives, SA often
    sees folder=0 for folders shared from a personal Drive (even when Share UI shows Editor)."""
    files, tok = [], None
    while True:
        resp = drive.files().list(
            q=q, orderBy="name", pageSize=1000, fields=fields, pageToken=tok,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        files += resp.get("files", [])
        tok = resp.get("nextPageToken")
        if not tok:
            break
    return files


def diagnose_folder(drive, model):
    """Print why images are invisible to the SA. Safe / read-only."""
    cfg = MODELS[_model(model)]
    fid = cfg["images"]
    print("[%s] folder_id=%s" % (model, fid))
    try:
        meta = drive.files().get(
            fileId=fid, fields="id,name,mimeType,driveId,owners,shared",
            supportsAllDrives=True).execute()
        print("[%s] folder_get OK | name=%r mime=%s driveId=%s shared=%s"
              % (model, meta.get("name"), meta.get("mimeType"),
                 meta.get("driveId"), meta.get("shared")))
    except Exception as e:
        print("[%s] folder_get FAIL: %s: %s" % (model, type(e).__name__, e))
        print("[%s] -> SA cannot open this folder. Re-share the FOLDER (not only files)"
              " with tik-batch-factory@… as Viewer/Editor." % model)
        return

    kids = _drive_list(drive, "'%s' in parents and trashed=false" % fid)
    print("[%s] children_any=%d" % (model, len(kids)))
    for f in kids[:10]:
        print("   - %s | %s | %s" % (f.get("name"), f.get("mimeType"), f.get("id")))
    if not kids:
        print("[%s] -> Folder opens but has 0 children for SA. Share the folder again;"
              " wait ~1 min; confirm Manage access lists the SA on the FOLDER." % model)


def list_images(drive, model):
    """All non-trashed images in the model folder (id, name). Does not touch ledger."""
    cfg = MODELS[_model(model)]
    # Prefer image/* ; also accept common upload types if mime is odd
    q = ("'%s' in parents and trashed=false and "
         "(mimeType contains 'image/' or mimeType = 'application/octet-stream')"
         % cfg["images"])
    files = _drive_list(drive, q)
    # Keep only real images / known extensions
    out = []
    for f in files:
        mt = (f.get("mimeType") or "").lower()
        name = (f.get("name") or "").lower()
        if "image/" in mt or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic")):
            out.append({"id": f["id"], "name": f["name"]})
    return out


RESULTS_CSV = os.path.join(ROOT, 'ig_batch_results.csv')
ACCOUNTS_CSV = os.path.join(ROOT, 'Instagram_farm_accounts.csv')


def _usernames_for_model(model):
    """Usernames assigned to this model in Instagram_farm_accounts.csv."""
    out = set()
    if not os.path.exists(ACCOUNTS_CSV):
        return out
    try:
        for row in __import__("csv").DictReader(open(ACCOUNTS_CSV, encoding="utf-8")):
            if (row.get("model") or "").strip().lower() == model:
                u = (row.get("username") or "").strip()
                if u:
                    out.add(u)
    except Exception:
        pass
    return out


def _posted_image_names(model=None, results_csv=RESULTS_CSV):
    """Filenames with verified POST_DONE.

    If model is set, only count posts by that model's usernames (shared Drive
    folder otherwise marks every model's ledger 'done' after one model posts —
    2026-07-29 diana starved while karly/tiana POST_DONE names filled the set).
    """
    names = set()
    if not os.path.exists(results_csv):
        return names
    allow = _usernames_for_model(model) if model else None
    try:
        for r in __import__("csv").reader(open(results_csv, encoding="utf-8")):
            if len(r) > 9 and r[8] == "POST_DONE" and r[9]:
                if allow is not None and r[3] not in allow:
                    continue
                names.add(r[9].strip())
    except Exception:
        pass
    return names


def _rewrite_used(used_file, ids):
    with open(used_file, "w", encoding="utf-8") as fh:
        for i in sorted(ids):
            fh.write(i + "\n")


def next_image(drive, model, workdir):
    """Lowest-named unused image in THIS model's folder, downloaded into workdir.

    Soft-claims into used_ledger on download so parallel phones don't grab the same
    file. Reclaims soft-claims that never got a POST_DONE *for this model*.
    """
    cfg = MODELS[_model(model)]
    used = _used_ids(cfg["used"])
    files = list_images(drive, model)
    unused = [f for f in files if f["id"] not in used]
    if not unused and files and used:
        posted = _posted_image_names(model=model)
        reclaim = [f for f in files if f["id"] in used and f["name"] not in posted]
        if reclaim:
            keep = used - {f["id"] for f in reclaim}
            _rewrite_used(cfg["used"], keep)
            print("[drive] %s: reclaimed %d soft-claimed image(s) never POST_DONE "
                  "for this model (ledger %d -> %d)"
                  % (model, len(reclaim), len(used), len(keep)))
            used = keep
            unused = reclaim
    if not unused:
        posted = _posted_image_names(model=model)
        posted_here = [f for f in files if f["name"] in posted]
        soft_only = [f for f in files if f["id"] in used and f["name"] not in posted]
        print("[drive] %s: no unused images (folder=%d, used_ledger=%d, "
              "POST_DONE_names=%d, soft_claim_only=%d, file=%s)"
              % (model, len(files), len(used), len(posted_here), len(soft_only),
                 cfg["used"]))
        if files:
            print("[drive] folder inventory:")
            for f in files[:12]:
                if f["name"] in posted:
                    flag = "POSTED"
                elif f["id"] in used:
                    flag = "SOFT"
                else:
                    flag = "free"
                print("   [%s] %s  id=%s" % (flag, f["name"], f["id"]))
            if soft_only:
                print("[drive] soft-claims never POST_DONE — reclaim should have run;"
                      " try: python -u drive_content_ig.py --reset-used %s" % model)
            if posted_here and not soft_only:
                print("[drive] all folder images already POST_DONE for %s — "
                      "ADD NEW IMAGES to the Drive folder, then re-run." % model)
            elif not posted_here and not soft_only:
                print("[drive] reclaim for proof:  python -u drive_content_ig.py "
                      "--reset-used %s" % model)
                print("[drive] or add new images to the Drive folder, then re-run.")
        return None
    def _rank(f):
        n = (f.get("name") or "").lower()
        if n.endswith((".png", ".jpg", ".jpeg")):
            return 0
        if n.endswith(".webp"):
            return 2
        return 1
    unused.sort(key=_rank)
    non_webp = [f for f in unused if not (f.get("name") or "").lower().endswith(".webp")]
    f = non_webp[0] if non_webp else unused[0]
    if (f.get("name") or "").lower().endswith(".webp"):
        print("[drive] only webp left (%s) — ig_loop will convert to PNG on push" % f["name"])
    # Claim immediately so parallel run_ig_device workers don't collide
    with open(cfg["used"], "a", encoding="utf-8") as fh:
        fh.write(f["id"] + "\n")
    os.makedirs(workdir, exist_ok=True)
    local_name = mn.safe_local_filename(f["name"], f["id"])
    path = os.path.join(workdir, local_name)
    print("[drive] download %s -> %s (claimed, was %s)"
          % (f["name"], path, f["name"]))
    req = drive.files().get_media(fileId=f["id"])
    with open(path, "wb") as fh:
        dl, done = MediaIoBaseDownload(fh, req), False
        while not done:
            _, done = dl.next_chunk()
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        print("[drive] FAIL download empty/tiny: %s" % path)
        return None
    return {"id": f["id"], "name": f["name"], "path": path, "local_name": local_name}


def captions(sheets, model):
    """All non-empty captions from column A of THIS model's Captions sheet (multi-line,
    emojis preserved). Each already funnels to that model's own handle."""
    last_err = None
    for attempt in range(1, 5):
        try:
            rows = sheets.spreadsheets().values().get(
                spreadsheetId=MODELS[_model(model)]["captions"],
                range="A1:A2000").execute().get("values", [])
            return [r[0] for r in rows if r and r[0].strip()]
        except Exception as e:
            last_err = e
            print("[drive] captions fetch %s (try %d/4) — retry"
                  % (type(e).__name__, attempt))
            time.sleep(1.5 * attempt)
    print("[drive] captions unavailable after retries: %s — post with empty caption"
          % last_err)
    return []


def pick_caption(caps, idx):
    """Rotate through the reusable caption pool."""
    return caps[idx % len(caps)] if caps else ""


def mark_used(drive, file_id, model):
    """After a VERIFIED post: record the image in THIS model's ledger so it never reposts,
    and best-effort trash it in Drive to free space (403 expected on owner-only files -
    the local ledger is what actually guarantees no re-use)."""
    with open(MODELS[_model(model)]["used"], "a", encoding="utf-8") as fh:
        fh.write(file_id + "\n")
    try:
        drive.files().update(fileId=file_id, body={"trashed": True},
                             supportsAllDrives=True).execute()
        print("[drive] %s trashed (freed space): %s" % (model, file_id))
    except Exception as e:
        print("[drive] %s kept in Drive (owner-only delete) - marked used locally [%s]"
              % (model, type(e).__name__))


def reset_used(model):
    """Clear local used ledger so folder images can be tried again (proof / recover)."""
    path = MODELS[_model(model)]["used"]
    n = len(_used_ids(path))
    open(path, "w", encoding="utf-8").close()
    print("[drive] reset used ledger %s (%d ids cleared) -> %s" % (model, n, path))


if __name__ == "__main__":
    # Peek / diagnose / reset — peek does NOT claim/mark used.
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    if args and args[0] == "--reset-used":
        which = args[1:] or list(MODELS)
        for m in which:
            if m not in MODELS:
                print("[%s] unknown model" % m); continue
            reset_used(m)
        sys.exit(0)

    drive, sheets = services()
    which = args or list(MODELS)
    for m in which:
        try:
            caps = captions(sheets, m)
            diagnose_folder(drive, m)
            files = list_images(drive, m)
            used = _used_ids(MODELS[m]["used"])
            unused = [f for f in files if f["id"] not in used]
            sample = unused[0]["name"] if unused else "NONE"
            print("[%s] OK | folder=%d used=%d unused=%d | next~: %-14s | captions: %d | sample: %s"
                  % (m, len(files), len(used), len(unused), sample, len(caps),
                     repr(caps[0][:48]) if caps else "-"))
            if files and not unused:
                print("[%s] HINT: python -u drive_content_ig.py --reset-used %s" % (m, m))
        except Exception as e:
            print("[%s] FAIL: %s: %s" % (m, type(e).__name__, e))
