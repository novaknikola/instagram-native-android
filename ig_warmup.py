# -*- coding: utf-8 -*-
# ig_warmup.py - Reels warm-up before posting.
#
# Actions (client 2026-07-30):
#   1) Scroll Reels feed
#   2) Open specific profiles' Reels and scroll
#   3) Save Reels from those profiles (in-app Save)
#
# Target usernames: dashboard list and/or .txt file (one @user per line).
#
#   python ig_warmup.py --serial SERIAL --clone PKG --profiles nike,adidas
#   python ig_warmup.py --serial SERIAL --clone PKG --file warmup_profiles.txt
from farm_root import ROOT
import os
import sys

DEFAULT_TXT = os.path.join(ROOT, 'ig_warmup_profiles.txt')


def load_profiles(path=None, extra=None):
    """Merge .txt file + explicit list. Deduped, order preserved."""
    out = []
    seen = set()

    def _add(u):
        u = (u or "").strip().lstrip("@")
        if not u or u.startswith("#") or u.lower() in seen:
            return
        seen.add(u.lower())
        out.append(u)

    for u in extra or []:
        _add(u)
    path = path or DEFAULT_TXT
    if path and os.path.isfile(path):
        try:
            for ln in open(path, encoding="utf-8", errors="replace"):
                _add(ln.split("#")[0])
        except Exception as e:
            print("[warmup] read %s fail: %s" % (path, e))
    return out


def save_profiles_txt(profiles, path=None):
    path = path or DEFAULT_TXT
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# IG warm-up target profiles (one per line)\n")
        for p in profiles or []:
            p = (p or "").strip().lstrip("@")
            if p:
                fh.write(p + "\n")
    return path


def run_warmup(
    serial,
    clone,
    profiles=None,
    file_path=None,
    scroll_feed=8,
    scroll_profile=5,
    saves_per_profile=2,
):
    """Drive ig_loop warm-up on one clone. Returns status string."""
    import ig_loop as t

    t.SERIAL = serial
    if not clone.startswith("com.instagram."):
        clone = "com.instagram." + clone
    targets = load_profiles(file_path or DEFAULT_TXT, profiles or [])
    print("[warmup] serial=%s clone=%s targets=%s" % (serial, clone, targets))
    if not t.launch(clone):
        return "APP_WONT_OPEN"
    return t.do_warmup(
        pkg=clone,
        profiles=targets,
        scroll_feed=scroll_feed,
        scroll_profile=scroll_profile,
        saves_per_profile=saves_per_profile,
    )


def main():
    args = sys.argv[1:]
    serial = ""
    clone = ""
    profiles = []
    file_path = DEFAULT_TXT
    no_record = "--no-record" in args
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--serial" and i + 1 < len(args):
            serial = args[i + 1]
            i += 2
            continue
        if a == "--clone" and i + 1 < len(args):
            clone = args[i + 1]
            i += 2
            continue
        if a == "--profiles" and i + 1 < len(args):
            profiles = [p.strip() for p in args[i + 1].split(",") if p.strip()]
            i += 2
            continue
        if a == "--file" and i + 1 < len(args):
            file_path = args[i + 1]
            i += 2
            continue
        if a == "--no-record":
            i += 1
            continue
        i += 1
    if not serial or not clone:
        print(
            "Usage: ig_warmup.py --serial SERIAL --clone PKG [--profiles a,b] [--file path]"
        )
        sys.exit(2)

    rec = None
    if not no_record:
        try:
            import device_record as drec
            rec = drec.start([serial], enabled=True)
            if rec:
                print("[%s] recording -> %s" % (serial, rec.run_dir))
            else:
                print("[%s] recording failed to start" % serial)
        except Exception as e:
            print("[%s] recording start skip: %s" % (serial, e))

    try:
        st = run_warmup(serial, clone, profiles=profiles, file_path=file_path)
        print("[warmup] result=%s" % st)
        rc = 0 if st == "WARMUP_DONE" else 1
    finally:
        if rec is not None:
            try:
                import device_record as drec
                drec.stop(rec)
                print("[%s] recording saved -> %s" % (serial, rec.run_dir))
            except Exception as e:
                print("[%s] recording stop skip: %s" % (serial, e))
        # Always kill IG after warmup so mirror/phone is not left on Reels black screen.
        try:
            import ig_loop as t
            t.SERIAL = serial
            pkg = clone if clone.startswith("com.instagram.") else "com.instagram." + clone
            t.force_stop(pkg)
            print("[%s] force-stop %s (warmup done)" % (serial, pkg.split(".")[-1]))
        except Exception as e:
            print("[%s] force-stop skip: %s" % (serial, e))

    sys.exit(rc)


if __name__ == "__main__":
    main()
