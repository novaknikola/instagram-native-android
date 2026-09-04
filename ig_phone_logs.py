# -*- coding: utf-8 -*-
# Per-phone run logs so the Console can show N monitors for N phones.
# Parent (farm / scheduler) redirects each run_ig_device stdout here.
import os

def phone_log_dir():
    """Directory of {serial}.txt files for the active run."""
    d = (os.environ.get("IG_PHONE_LOG_DIR") or "").strip()
    if d:
        os.makedirs(d, exist_ok=True)
        return d
    run_log = (os.environ.get("IG_RUN_LOG") or "").strip()
    if run_log:
        d = os.path.splitext(run_log)[0] + "_phones"
        os.makedirs(d, exist_ok=True)
        os.environ["IG_PHONE_LOG_DIR"] = d
        return d
    return ""


def open_phone_log(serial):
    """Open append handle for this serial. Caller must keep the handle alive."""
    d = phone_log_dir()
    serial = (serial or "").strip()
    if not d or not serial:
        return None, ""
    path = os.path.join(d, "%s.txt" % serial)
    fh = open(path, "a", encoding="utf-8", buffering=1)
    return fh, path
