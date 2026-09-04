# -*- coding: utf-8 -*-
# ig_clone_probe.py - open unique IG clones, classify UI, never uninstall.
#
# Parallel phones, serial clones on one phone. No login, no post, no pm uninstall.
# Loading on EVERY unique clone on a serial = phone/proxy (do not delete).
# Loading on one clone while a sibling is OPEN_OK = clone candidate only.
#
#   python -u ig_clone_probe.py
#   python -u ig_clone_probe.py --serial SERIAL --run-dir DIR --pkgs pkg1,pkg2
from farm_root import ROOT
import collections
import csv
import datetime
import json
import os
import subprocess
import sys
import time

import ig_loop as t
import ig_pkg

LOADING_S = 12
POLL_S = 2
STAGGER = 2

# App reached real Instagram chrome (not spinner).
OPEN_OK = frozenset((
    "FEED", "LOGIN_SCREEN", "LOGIN_LANDING", "SIGNUP_GATE",
    "CAPTCHA", "HUMAN_CHECK", "CHALLENGE", "CONTACT_VERIFY",
    "2FA_CHOOSE", "ACCOUNT_SUSPENDED", "ACCOUNT_NOT_FOUND",
    "LOGIN_ERROR", "LOGIN_ERROR_DIALOG", "ACTION_LIMIT",
    "TIP_SHEET", "NOTIF_PROMPT", "SAVE_INFO", "FOLLOW_SUGGESTIONS",
    "BIRTHDAY", "ADD_PHONE", "EMAIL_SECURITY", "SYS_PERMISSION",
    "CAPTION_SCREEN", "CREATE_PICKER", "CREATE_CAMERA", "CREATE_CHOOSER",
    "EDIT_SCREEN", "GOOGLE_SAVE",
))

CSV_HEADER = (
    "time", "serial", "pkg", "suffix", "state", "verdict",
    "phone_verdict", "snippet", "xml_path",
)


def sh(*args, timeout=60):
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace",
    ).stdout or ""


def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def farm_busy():
    if ig_pkg._process_match("run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler"):
        return "IG farm runner is active"
    if ig_pkg._process_match("run_farm\\.py|run_device\\.py|threads_loop"):
        return "Threads farm runner is active"
    return ""


def unique_ig_by_phone(serials):
    all_ig = {}
    for s in serials:
        out = sh("adb", "-s", s, "shell", "pm", "list", "packages")
        all_ig[s] = ig_pkg.ig_pkgs_from_pm_list(out)
    cnt = collections.Counter(p for ps in all_ig.values() for p in ps)
    shared = {p for p, n in cnt.items() if n > 1}
    uniq = {s: [p for p in all_ig[s] if p not in shared] for s in serials}
    return uniq, shared


def _snippet(xml, n=140):
    s = (t.text_block(xml) or "").replace("\n", " ").strip()
    return s[:n]


def _save_xml(run_dir, serial, suffix, verdict, xml):
    d = os.path.join(run_dir, "xml", serial)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s_%s.xml" % (suffix, verdict))
    try:
        with open(path, "w", encoding="utf-8", errors="replace") as fh:
            fh.write(xml or "")
    except Exception as e:
        print("[probe] xml write skip: %s" % e)
        return ""
    return path


def classify_open(pkg):
    """Launch pkg, poll detect_state, force-stop. Returns dict."""
    suf = pkg.rsplit(".", 1)[-1]
    t.CURRENT_PKG = pkg
    opened = t.launch(pkg)
    xml = t.dump()
    if not opened:
        return {
            "state": "APP_WONT_OPEN",
            "verdict": "APP_WONT_OPEN",
            "snippet": _snippet(xml),
            "xml": xml,
            "suffix": suf,
        }
    deadline = time.time() + LOADING_S
    state = t.detect_state(xml)
    last_xml = xml
    while True:
        if state in OPEN_OK:
            break
        if state not in ("LOADING", "UNKNOWN") and state not in OPEN_OK:
            break
        if time.time() >= deadline:
            break
        time.sleep(POLL_S)
        last_xml = t.dump()
        state = t.detect_state(last_xml)
    if state in OPEN_OK:
        verdict = "OPEN_OK"
    elif state == "LOADING":
        verdict = "LOADING"
    elif state in ("WRONG_APP", "SYS_SETTINGS", "ANDROID_SHARE"):
        verdict = "WRONG_APP"
    elif state == "UNKNOWN":
        if t._ui_blank(last_xml):
            verdict = "BLANK"
        else:
            verdict = "UNKNOWN"
    else:
        verdict = state or "UNKNOWN"
    try:
        t.force_stop(pkg)
    except Exception:
        pass
    return {
        "state": state,
        "verdict": verdict,
        "snippet": _snippet(last_xml),
        "xml": last_xml,
        "suffix": suf,
    }


def phone_verdict_for(rows):
    """rows: list of probe dicts for one serial."""
    if not rows:
        return "EMPTY"
    vset = {r["verdict"] for r in rows}
    n_ok = sum(1 for r in rows if r["verdict"] == "OPEN_OK")
    n_load = sum(1 for r in rows if r["verdict"] == "LOADING")
    if n_ok == len(rows):
        return "ALL_OK"
    if n_ok == 0 and n_load == len(rows):
        return "PHONE_LOADING"
    if n_ok == 0:
        return "PHONE_DEAD"
    if n_ok and (n_load or (vset - {"OPEN_OK"})):
        return "MIXED"
    return "OTHER"


def probe_serial(serial, pkgs, run_dir):
    t.SERIAL = serial
    print("[probe] serial=%s clones=%d" % (serial, len(pkgs)), flush=True)
    rows = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for pkg in pkgs:
        print("[probe] open %s %s" % (serial[-8:], pkg.rsplit(".", 1)[-1]), flush=True)
        hit = classify_open(pkg)
        xml_path = _save_xml(run_dir, serial, hit["suffix"], hit["verdict"], hit["xml"])
        rec = {
            "time": now,
            "serial": serial,
            "pkg": pkg,
            "suffix": hit["suffix"],
            "state": hit["state"],
            "verdict": hit["verdict"],
            "snippet": hit["snippet"],
            "xml_path": xml_path,
        }
        rows.append(rec)
        print("[probe] %s %s -> %s (%s)" % (
            serial[-8:], rec["suffix"], rec["verdict"], rec["state"]), flush=True)
    pv = phone_verdict_for(rows)
    for r in rows:
        r["phone_verdict"] = pv
    print("[probe] %s phone_verdict=%s" % (serial[-8:], pv), flush=True)
    return rows


def write_csv(path, rows):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(CSV_HEADER)
        for r in rows:
            w.writerow([r.get(k, "") for k in CSV_HEADER])


def write_summary(path, rows, shared):
    by = collections.defaultdict(list)
    for r in rows:
        by[r["serial"]].append(r)
    lines = []
    lines.append("IG clone probe — open only, no uninstall")
    lines.append("time %s" % datetime.datetime.now().isoformat(timespec="seconds"))
    lines.append("shared skipped: %s" % (", ".join(sorted(
        p.rsplit(".", 1)[-1] for p in shared)) or "(none)"))
    lines.append("")
    c = collections.Counter(r["verdict"] for r in rows)
    pv = collections.Counter(by[s][0]["phone_verdict"] for s in by)
    lines.append("clone verdicts: %s" % dict(c))
    lines.append("phone verdicts: %s" % dict(pv))
    lines.append("")
    lines.append("%-24s %6s %6s %6s %6s %s" % (
        "SERIAL", "OK", "LOAD", "WONT", "OTHER", "PHONE"))
    cand = []
    phone_load = []
    for s in sorted(by):
        rs = by[s]
        n_ok = sum(1 for r in rs if r["verdict"] == "OPEN_OK")
        n_ld = sum(1 for r in rs if r["verdict"] == "LOADING")
        n_wo = sum(1 for r in rs if r["verdict"] == "APP_WONT_OPEN")
        n_ot = len(rs) - n_ok - n_ld - n_wo
        ph = rs[0]["phone_verdict"]
        lines.append("%-24s %6d %6d %6d %6d %s" % (
            s, n_ok, n_ld, n_wo, n_ot, ph))
        if ph == "PHONE_LOADING":
            phone_load.append(s)
        if ph == "MIXED":
            for r in rs:
                if r["verdict"] != "OPEN_OK":
                    cand.append("%s %s %s" % (s, r["suffix"], r["verdict"]))
    lines.append("")
    lines.append("PHONE_LOADING (do not delete clones): %d" % len(phone_load))
    for s in phone_load:
        lines.append("  %s" % s)
    lines.append("MIXED clone candidates (sibling opened OK): %d" % len(cand))
    for x in cand:
        lines.append("  %s" % x)
    lines.append("")
    lines.append("No packages were uninstalled.")
    text = "\n".join(lines) + "\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text, flush=True)


def _live_ports():
    """Port map from this repo, or Threads farm if that pool is the live one."""
    try:
        import proxy_pool
        data, path = proxy_pool.load_device_ports()
        if data:
            return data, path
    except Exception:
        pass
    paths = [
        os.path.join(ROOT, "proxy_state", "device_ports.json"),
        os.path.join(os.path.dirname(ROOT), "threads-farm", "proxy_state", "device_ports.json"),
    ]
    for path in paths:
        try:
            data = json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data:
            return data, path
    return {}, ""


def reassert_reverse(serials):
    ports, src = _live_ports()
    if src:
        print("[probe] proxy map: %s" % src)
    missing = []
    for s in serials:
        port = ports.get(s)
        if port is None:
            missing.append(s)
            continue
        subprocess.run(
            ["adb", "-s", s, "reverse", "tcp:1080", "tcp:%d" % int(port)],
            capture_output=True, text=True, timeout=20,
        )
    return missing


def worker_main(serial, run_dir, pkgs):
    rows = probe_serial(serial, pkgs, run_dir)
    csv_path = os.path.join(run_dir, "%s.csv" % serial)
    write_csv(csv_path, rows)
    return 0


def orchestrate():
    busy = farm_busy()
    if busy:
        print("[probe] abort: %s" % busy)
        return 1
    serials = devices()
    if not serials:
        print("[probe] no ADB devices")
        return 1
    uniq, shared = unique_ig_by_phone(serials)
    n_u = sum(len(v) for v in uniq.values())
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(ROOT, "diagnostics", "clone_probe", stamp)
    os.makedirs(run_dir, exist_ok=True)
    print("[probe] run_dir=%s" % run_dir)
    print("[probe] phones=%d unique_ig=%d shared_skip=%s" % (
        len(serials), n_u, ",".join(sorted(p.rsplit(".", 1)[-1] for p in shared))))
    missing = reassert_reverse(serials)
    if missing:
        print("[probe] PROXY RELAY not ready for: %s" % ", ".join(missing))
        print("[probe] start start_proxy_pool.bat first")
        return 1
    try:
        ig_pkg.quiet_idle_ig(keep_serials=None)
    except Exception as e:
        print("[probe] quiet skip: %s" % e)

    here = os.path.dirname(os.path.abspath(__file__))
    procs = {}
    launched = 0
    for s in sorted(serials):
        pkgs = uniq.get(s) or []
        if not pkgs:
            print("[probe] skip %s: no unique IG" % s)
            continue
        if launched > 0 and STAGGER:
            time.sleep(STAGGER)
        cmd = [
            sys.executable, "-u", os.path.join(here, "ig_clone_probe.py"),
            "--serial", s, "--run-dir", run_dir,
            "--pkgs", ",".join(pkgs),
        ]
        log_path = os.path.join(run_dir, "%s.txt" % s)
        fh = open(log_path, "w", encoding="utf-8", buffering=1)
        procs[s] = (subprocess.Popen(cmd, cwd=here, stdout=fh, stderr=subprocess.STDOUT), fh)
        launched += 1
        print("[probe] launch %s n=%d" % (s[-8:], len(pkgs)), flush=True)

    rc = 0
    for s, (p, fh) in procs.items():
        c = p.wait()
        try:
            fh.close()
        except Exception:
            pass
        if c != 0:
            rc = c
            print("[probe] worker fail %s rc=%s" % (s[-8:], c))
    rows = []
    for s in sorted(procs):
        part = os.path.join(run_dir, "%s.csv" % s)
        if not os.path.isfile(part):
            continue
        with open(part, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
    csv_path = os.path.join(run_dir, "probe.csv")
    if rows:
        write_csv(csv_path, rows)
    write_summary(os.path.join(run_dir, "summary.txt"), rows, shared)
    print("PROBE DONE %s" % run_dir, flush=True)
    return rc


def main(argv):
    serial = run_dir = pkgs = None
    a = argv[1:]
    for i, x in enumerate(a):
        if x == "--serial" and i + 1 < len(a):
            serial = a[i + 1]
        if x == "--run-dir" and i + 1 < len(a):
            run_dir = a[i + 1]
        if x == "--pkgs" and i + 1 < len(a):
            pkgs = [p.strip() for p in a[i + 1].split(",") if p.strip()]
    if serial and run_dir and pkgs:
        return worker_main(serial, run_dir, pkgs)
    if serial or run_dir or pkgs:
        print("worker mode needs --serial --run-dir --pkgs")
        return 2
    return orchestrate()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
