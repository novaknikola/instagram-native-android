# -*- coding: utf-8 -*-
# run_ig_farm.py - Instagram farm runner on the EXISTING Threads spine.
# Discovers phones + unique IG packages, plans one account per clone per device,
# launches run_ig_device.py per phone (parallel by default, staggered starts).
#
#   python run_ig_farm.py [--country us] [--start 0] [--per-device 1] [--limit 0]
#                         [--devices s1,s2,...] [--model tiana] [--dry] [--allow-cold]
#                         [--allow-unproven] [--serial] [--format feed|reel|carousel|story]
#                         [--formats reel,feed,story] [--story-link URL] [--highlight TITLE]
#                         [--stagger 2] [--no-record] [--login-only] [--warmup]
#
# Default: PARALLEL phones (Threads-style). Pass --serial for one phone at a time.
# Screen recordings ON by default (per device recordings/<stamp>/). Escape: --no-record.
# Stagger is 2s so ~20 phones are all running within a minute. Clones on one phone
# still run sequentially inside run_ig_device.
#
# Skip phones that already hit IG LOGIN_META_ERROR unless --allow-cold.
import os
from farm_root import ROOT
import sys, csv, json, subprocess, os, tempfile, collections, time
import ig_loop as t
import drive_content_ig as dc
import ig_account_clone as acc_clone
import ig_pkg

try:
    import ig_error_shots as _es
    _es.ensure_default_env()
except Exception:
    pass

MODELS_ORDER = ["ig"]
DEFAULT_MODEL = "ig"
VALID_FORMATS = ("feed", "story", "carousel", "reel")

COUNTRY  = "us"
START    = 0
PER_DEV  = 1  # native: always 1 account per phone
LIMIT    = 0
DEVICES  = None
FORCE_MODEL = None
DRY      = False
PARALLEL = True   # product default — many phones at once
STAGGER  = 2      # seconds between parallel phone process starts (all 20 up quickly)
MAX_INFLIGHT = 0  # 0 = no cap; else max concurrent run_ig_device (GB throttle)
FARM_FMT = "reel"  # single-format default
FARM_FMTS = None   # if set, sequential formats per phone (E2E pack)
STORY_LINK = (os.environ.get("IG_STORY_LINK") or "").strip()
HIGHLIGHT_TITLE = (os.environ.get("IG_HIGHLIGHT_TITLE") or "").strip()
# Default: only Threads-proven accounts (see file header). Escape: --allow-unproven
ALLOW_UNPROVEN = False
USE_CLONE_ALLOW = False  # native farm: no Nomix allowlist
NO_RECORD = False  # screen recordings ON by default (Threads-style)
LOGIN_ONLY = False  # login + force-stop; no warmup/post
FORCE_WARMUP = False  # Reels warmup before post

RESULTS_CSV = os.path.join(ROOT, 'ig_batch_results.csv')
THREADS_RESULTS = os.path.join(ROOT, 'batch_results.csv')

a = sys.argv[1:]
for k in range(len(a)):
    if a[k] == "--dry":                              DRY = True
    if a[k] == "--parallel":                         PARALLEL = True
    if a[k] == "--serial":                           PARALLEL = False
    if a[k] == "--allow-unproven":                   ALLOW_UNPROVEN = True
    if a[k] == "--e2e":                              FARM_FMTS = ["reel", "feed", "story"]
    if a[k] == "--no-record":                        NO_RECORD = True
    if a[k] == "--login-only":                       LOGIN_ONLY = True
    if a[k] == "--warmup":                           FORCE_WARMUP = True
    if k + 1 < len(a):
        if a[k] == "--country":     COUNTRY = a[k + 1]
        if a[k] == "--start":       START   = int(a[k + 1])
        if a[k] == "--per-device":  PER_DEV = int(a[k + 1])
        if a[k] == "--limit":       LIMIT   = int(a[k + 1])
        if a[k] == "--devices":     DEVICES = a[k + 1].split(",")
        if a[k] == "--model":       FORCE_MODEL = a[k + 1].lower()
        if a[k] == "--stagger":     STAGGER = max(0, int(a[k + 1]))
        if a[k] == "--max-inflight": MAX_INFLIGHT = max(0, int(a[k + 1]))
        if a[k] == "--format":
            FARM_FMT = (a[k + 1] or "reel").strip().lower()
        if a[k] == "--formats":
            FARM_FMTS = [
                x.strip().lower() for x in (a[k + 1] or "").split(",") if x.strip()
            ]
        if a[k] == "--story-link":
            STORY_LINK = (a[k + 1] or "").strip()
        if a[k] == "--highlight":
            HIGHLIGHT_TITLE = (a[k + 1] or "").strip()
if FARM_FMT not in VALID_FORMATS:
    FARM_FMT = "reel"
if FARM_FMTS:
    FARM_FMTS = [f for f in FARM_FMTS if f in VALID_FORMATS] or [FARM_FMT]
else:
    FARM_FMTS = [FARM_FMT]
if "story" in FARM_FMTS and not HIGHLIGHT_TITLE:
    HIGHLIGHT_TITLE = "Highlights"
_link_file = os.path.join(ROOT, "ig_story_link.txt")
if not STORY_LINK and os.path.isfile(_link_file):
    try:
        STORY_LINK = open(_link_file, encoding="utf-8").read().strip().splitlines()[0].strip()
    except Exception:
        pass
PER_DEV = 1
# --dry never records (same as Threads)
if DRY:
    NO_RECORD = True
# Parent owns recordings; children must not start a second wave.
if not NO_RECORD:
    os.environ["IG_FARM_RECORDING"] = "1"
else:
    os.environ.pop("IG_FARM_RECORDING", None)
    os.environ["IG_NO_RECORD"] = "1"
_env_inflight = (os.environ.get("IG_MAX_INFLIGHT") or "").strip()
if MAX_INFLIGHT <= 0 and _env_inflight.isdigit():
    MAX_INFLIGHT = max(0, int(_env_inflight))
if "--stagger" not in a:
    _env_stagger = (os.environ.get("IG_STAGGER") or "").strip()
    if _env_stagger.isdigit():
        STAGGER = max(0, int(_env_stagger))

def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout

def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]

def clones(serial):
    """IG packages: any com.instagram.* except Threads barcel*."""
    out = sh("adb", "-s", serial, "shell", "pm list packages")
    return ig_pkg.ig_pkgs_from_pm_list(out)

def unique_on_phone(all_pkgs, shared):
    """Unique farm clones on one phone (not shared/stock)."""
    return [p for p in all_pkgs if p not in shared and not acc_clone.is_stock(p)]

def threads_wins_since(since="2026-07-17"):
    wins = collections.Counter()
    if not os.path.exists(THREADS_RESULTS):
        return wins
    for _r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(_r) > 7 and _r[7] == "LOGGED_IN" and _r[0] >= since:
            wins[_r[1]] += 1
    return wins

def threads_last_login():
    out = {}
    if not os.path.exists(THREADS_RESULTS):
        return out
    for _r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(_r) > 7 and _r[3] and _r[7]:
            out[_r[3]] = _r[7]
    return out

def threads_last_login_time():
    out = {}
    if not os.path.exists(THREADS_RESULTS):
        return out
    for _r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(_r) > 7 and _r[3] and _r[0] and _r[0] != "time":
            out[_r[3]] = _r[0]
    return out

def ig_retry_priority():
    latest = {}
    if not os.path.exists(RESULTS_CSV):
        return set()
    for _r in csv.reader(open(RESULTS_CSV, encoding="utf-8")):
        if len(_r) > 8 and _r[3] and _r[0] != "time":
            latest[_r[3]] = (_r[7], _r[8])
    return {u for u, (login, post) in latest.items()
            if login == "LOGGED_IN" and post != "POST_DONE"}

def group_by_model(serials):
    """Single model: every phone in one pool."""
    return {DEFAULT_MODEL: sorted(serials)}

if LOGIN_ONLY:
    os.environ["IG_LOGIN_ONLY"] = "1"
print("=== Instagram farm (1 unique Nomix clone / phone) ===")
print("farm build=native_e2e_20260829a formats=%s stagger=%ds max_inflight=%s login_only=%s warmup=%s"
      % (",".join(FARM_FMTS), STAGGER, MAX_INFLIGHT if MAX_INFLIGHT > 0 else "all", LOGIN_ONLY, FORCE_WARMUP))
if "story" in FARM_FMTS:
    print("  story link=%s highlight=%s"
          % (STORY_LINK[:60] or "(none — story posts without sticker)",
             HIGHLIGHT_TITLE or "(skip)"))
devs  = DEVICES if DEVICES else devices()
accts = list(csv.DictReader(open(t.CSVPATH, encoding="utf-8")))

TRIED = set()
if os.path.exists(RESULTS_CSV):
    # CAPTCHA / old ALL_IPS_MASKED rows are retryable (Floppy was fine; IG
    # showed a puzzle). Do not treat them as burned accounts.
    DEAD_LOGIN = {"ACCOUNT_SUSPENDED", "ACCOUNT_CHALLENGED",
                  "LOGIN_META_ERROR", "LOGIN_REJECTED", "SIGNUP_LOOP",
                  "LOGIN_STUCK", "ACCOUNT_NOT_FOUND"}
    for _r in csv.reader(open(RESULTS_CSV, encoding="utf-8")):
        if len(_r) > 7 and _r[3]:
            login = _r[7]
            post = _r[8] if len(_r) > 8 else ""
            if login == "PROXY_DEAD":
                continue
            if login in DEAD_LOGIN or (login == "LOGGED_IN" and post == "POST_DONE"):
                TRIED.add(_r[3])
if TRIED:
    print("done-ledger: skipping %d already-tried accounts" % len(TRIED))

T_LAST = threads_last_login()
T_WHEN = threads_last_login_time()
IG_RETRY = ig_retry_priority()
T_SKIP = {"ACCOUNT_SUSPENDED", "ACCOUNT_CHALLENGED"}

proven_by_m = {m: [] for m in MODELS_ORDER}
unproven_by_m = {m: [] for m in MODELS_ORDER}
if FORCE_MODEL and FORCE_MODEL not in proven_by_m:
    proven_by_m[FORCE_MODEL] = []
    unproven_by_m[FORCE_MODEL] = []

for i, ac in enumerate(accts):
    m = DEFAULT_MODEL
    u = ac.get("username") or ""
    if u in TRIED:
        continue
    if T_LAST.get(u) in T_SKIP:
        continue
    if T_LAST.get(u) == "LOGGED_IN":
        proven_by_m[m].append(i)
    else:
        unproven_by_m[m].append(i)

def _sort_rows(rows):
    def key(row_i):
        u = accts[row_i].get("username") or ""
        when = T_WHEN.get(u, "")
        if u in IG_RETRY:
            tier = 0
        else:
            lg = T_LAST.get(u, "")
            if lg == "LOGGED_IN":
                tier = 1
            elif not lg:
                tier = 2
            else:
                tier = 3
        return (tier, when == "", tuple(-ord(c) for c in when), row_i)
    return sorted(rows, key=key)

rows_by_model = {}
n_proven = n_unproven = n_fallback = 0
for m in proven_by_m:
    proven = _sort_rows(proven_by_m[m])
    unproven = _sort_rows(unproven_by_m[m])
    n_proven += len(proven)
    n_unproven += len(unproven)
    if ALLOW_UNPROVEN:
        rows_by_model[m] = proven + unproven
    elif proven:
        rows_by_model[m] = proven
    elif unproven:
        rows_by_model[m] = unproven
        n_fallback += len(unproven)
        print("account pool [%s]: 0 Threads-proven left after ledger — "
              "falling back to %d unproven (or pass --allow-unproven always)"
              % (m, len(unproven)))
    else:
        rows_by_model[m] = []

if ALLOW_UNPROVEN:
    print("account pool: PROVEN+UNPROVEN (--allow-unproven) | "
          "Threads-LOGGED_IN=%d unproven=%d | IG mid-flight retries=%d"
          % (n_proven, n_unproven, len(IG_RETRY)))
else:
    print("account pool: PROVEN-ONLY (Threads LAST=LOGGED_IN) | "
          "using=%d excluded_unproven=%d | IG mid-flight retries=%d"
          % (n_proven + n_fallback, max(0, n_unproven - n_fallback), len(IG_RETRY)))
    print("  (Threads win != IG account exists; proven-only still cuts low-odds CSV. "
          "Pass --allow-unproven to include the rest.)")
if IG_RETRY:
    sample = sorted(IG_RETRY)[:5]
    print("  IG retry first: %s%s" % (", ".join(sample),
          "..." if len(IG_RETRY) > 5 else ""))
for m in MODELS_ORDER:
    print("  [%s] pool=%d" % (m, len(rows_by_model.get(m, []))))

all_clones = {d: clones(d) for d in devs}
_cnt = collections.Counter(c for cl in all_clones.values() for c in cl)
SHARED = {c for c, n in _cnt.items() if n > 1}
unique_n = {d: len(unique_on_phone(all_clones.get(d) or [], SHARED)) for d in devs}
if SHARED:
    print("skipping %d shared clone(s) (same identity on >1 phone): %s"
          % (len(SHARED), ", ".join(sorted(s.split(".")[-1] for s in SHARED))))
missing = [d for d in devs if unique_n.get(d, 0) <= 0]
if missing:
    print("unique IG clone missing on %d phone(s): %s"
          % (len(missing), ", ".join(x[-8:] for x in missing[:8])
             + ("..." if len(missing) > 8 else "")))

CLONE_ALLOW_ON = False

TWINS = threads_wins_since()
ALLOW_COLD = "--allow-cold" in a
META_BAD = set()
if os.path.exists(RESULTS_CSV):
    for _r in csv.reader(open(RESULTS_CSV, encoding="utf-8")):
        if len(_r) > 7 and _r[7] == "LOGIN_META_ERROR" and _r[1]:
            META_BAD.add(_r[1])
groups = group_by_model(devs)
for m in groups:
    groups[m] = sorted(groups[m], key=lambda s: (-TWINS.get(s, 0), s))
_cold = [d for d in devs if TWINS.get(d, 0) == 0]
if _cold:
    print("phone health: %d device(s) with 0 Threads LOGGED_IN since 2026-07-17 "
          "(deprioritized): %s"
          % (len(_cold), ", ".join(_cold[:5]) + ("..." if len(_cold) > 5 else "")))
if META_BAD and not ALLOW_COLD:
    print("IG META_ERROR denylist (skipped): %s" % ", ".join(sorted(META_BAD)))

plans = {}
for m, mdevs in groups.items():
    mrows = rows_by_model.get(m, [])[START:]
    if LIMIT:
        mrows = mrows[:LIMIT]
    used_n = collections.Counter()
    for row in mrows:
        u = (accts[row].get("username") or "").strip()
        if not u:
            continue
        under = set()
        for s in mdevs:
            if unique_n.get(s, 0) and used_n[s] < 1:
                under.add(s)
        live = []
        for d in mdevs:
            if not ALLOW_COLD and d in META_BAD:
                continue
            for c in all_clones.get(d) or []:
                if c in SHARED or acc_clone.is_stock(c):
                    continue
                live.append((d, c))
        serial, pkg, err = acc_clone.pick(u, live, new_only_serials=under)
        if err or not serial or not pkg:
            print("     skip %s: %s" % (u, err or "NO_CLONE"))
            continue
        if not ALLOW_COLD and serial in META_BAD:
            print("     skip %s: serial META_ERROR denylist" % u)
            continue
        cap = 1 if unique_n.get(serial, 0) else 0
        if cap <= 0:
            print("     skip %s: phone %s has no unique IG clone"
                  % (u, serial[-8:]))
            continue
        if used_n[serial] >= cap:
            print("     skip %s: phone already has an account this run"
                  % u)
            continue
        used_n[serial] += 1
        for fmt in FARM_FMTS:
            rec = {
                "clone": pkg, "row": row, "session": "a%04d" % row, "model": DEFAULT_MODEL,
                "format": fmt,
                "retry": u in IG_RETRY and fmt == FARM_FMTS[0],
                "mark": "RETRY" if (u in IG_RETRY and fmt == FARM_FMTS[0]) else "",
                "warmup": True if FORCE_WARMUP else False,
            }
            if fmt == "story":
                rec["story_link"] = STORY_LINK
                rec["highlight_title"] = HIGHLIGHT_TITLE
            plans.setdefault(serial, []).append(rec)

total = sum(len(v) for v in plans.values())
print("Country: %s | 1 account/phone | formats=%s | %d jobs across %d phones"
      % (COUNTRY, ",".join(FARM_FMTS), total, len(plans)))
for m, mdevs in groups.items():
    mt = sum(len(plans.get(d, [])) for d in mdevs)
    print("  [%s] %d phones, %d accounts (pool has %d)"
          % (m, len(mdevs), mt, len(rows_by_model.get(m, []))))
    for d in mdevs:
        p = plans.get(d)
        if p:
            bits = []
            for x in p:
                u = accts[x["row"]].get("username") or "?"
                bits.append("%s/%s row%d fmt=%s" % (
                    x["clone"].split(".")[-1], u, x["row"], x.get("format", FARM_FMT)))
            print("     %s (unique=%d): %s" % (
                d, unique_n.get(d, 0), ", ".join(bits)))
        else:
            print("     %s: (no unique IG clone / no account matched)" % d)
if not total:
    print("Nothing to do (no unique IG clones / accounts)."); sys.exit(0)

if DRY:
    print("\n[DRY RUN] IG routing verified. Nothing posted. Remove --dry to run for real.")
    sys.exit(0)

try:
    import iproyal_balance
    if not iproyal_balance.ensure_balance():
        print("[balance] IPRoyal gate failed/warned - continuing (proxy_pool uses Geonode).")
except Exception as e:
    print("[balance] skip (%s) - continuing with Geonode relay." % type(e).__name__)

import proxy_pool
missing = []
for d in plans:
    port = proxy_pool.port_for_serial(d)
    if port is None:
        missing.append(d); continue
    subprocess.run(["adb", "-s", d, "reverse", "tcp:1080", "tcp:%d" % port],
                   capture_output=True, text=True)
if missing:
    print("PROXY RELAY not ready for: %s" % ", ".join(missing))
    print("Start it first:  python proxy_pool.py   (or start_proxy_pool.bat)")
    sys.exit(1)
print("proxy relay up + USB tunnels re-asserted for %d phones" % len(plans))
try:
    import ig_tunnel
    n_vpn = ig_tunnel.enable_many(sorted(plans.keys()), label="pre-run")
    if n_vpn < len(plans):
        print("[vpn] tun0 missing on %d planned phone(s) — logins may PROXY_DEAD"
              % (len(plans) - n_vpn))
except Exception as e:
    print("[vpn] enable skip: %s" % e)
print("run mode: %s" % ("PARALLEL" if PARALLEL else "SERIAL (--serial)"))

try:
    import ig_pkg
    # Cold start: stop IG on EVERY phone (including planned). During stagger,
    # phones not launched yet must not sit with yesterday's IG burning CDN/GB.
    n_q = ig_pkg.quiet_idle_ig(keep_serials=None)
    if n_q:
        print("[proxy-save] pre-run quiet: stopped IG on %d phone(s)" % n_q)
except Exception as e:
    print("[proxy-save] idle IG stop skip: %s" % e)

_PHONE_LOG_FHS = []

# Full-run screen recordings (Threads-style). Default ON unless --no-record / --dry.
_REC = None
try:
    import device_record as _drec
    _REC = _drec.start(sorted(plans.keys()), enabled=not NO_RECORD)
    if _REC:
        print("[rec] recordings ON -> %s" % _REC.run_dir)
    elif NO_RECORD:
        print("[rec] recordings OFF (--no-record or --dry)")
except Exception as e:
    print("[rec] start skip: %s" % e)
    _REC = None


def _launch_device(serial, plan_items):
    pf = os.path.join(tempfile.gettempdir(), "ig_plan_%s.json" % serial)
    json.dump(plan_items, open(pf, "w", encoding="utf-8"))
    kwargs = {}
    try:
        import ig_phone_logs
        fh, lp = ig_phone_logs.open_phone_log(serial)
        if fh:
            _PHONE_LOG_FHS.append(fh)
            kwargs["stdout"] = fh
            kwargs["stderr"] = subprocess.STDOUT
            print("[farm] phone log %s -> %s" % (serial[-8:], lp))
    except Exception as e:
        print("[farm] phone log skip %s: %s" % (serial[-8:], e))
    cmd = [sys.executable, "-u", "run_ig_device.py", serial, pf, COUNTRY, COUNTRY]
    if LOGIN_ONLY:
        cmd.append("--login-only")
    if FORCE_WARMUP:
        cmd.append("--warmup")
    return subprocess.Popen(cmd, **kwargs)

def _reap(procs):
    """Remove finished Popen entries; return count still running."""
    for d in list(procs):
        if procs[d].poll() is not None:
            del procs[d]
    return len(procs)

if PARALLEL:
    plan_serials = sorted(plans.keys())
    if MAX_INFLIGHT > 0:
        print("[farm] max_inflight=%d (GB throttle — raise or 0 for full parallel)"
              % MAX_INFLIGHT)
        pending = collections.deque(plan_serials)
        procs = {}
        launched = 0
        while pending or procs:
            _reap(procs)
            while pending and len(procs) < MAX_INFLIGHT:
                if launched > 0 and STAGGER > 0:
                    print("[farm] stagger %ds before phone (%d active, %d queued)"
                          % (STAGGER, len(procs), len(pending)))
                    time.sleep(STAGGER)
                d = pending.popleft()
                launched += 1
                print("[farm] launch serial=%s accounts=%d (active=%d/%s)"
                      % (d, len(plans[d]), len(procs) + 1,
                         MAX_INFLIGHT if MAX_INFLIGHT > 0 else "all"))
                procs[d] = _launch_device(d, plans[d])
            if procs:
                time.sleep(1.5)
    else:
        procs = {}
        for i, d in enumerate(plan_serials):
            if i > 0 and STAGGER > 0:
                print("[farm] stagger %ds before phone %d/%d (%s…)"
                      % (STAGGER, i + 1, len(plan_serials), d[-8:]))
                time.sleep(STAGGER)
            print("[farm] launch %d/%d serial=%s accounts=%d"
                  % (i + 1, len(plan_serials), d, len(plans[d])))
            procs[d] = _launch_device(d, plans[d])
        while _reap(procs):
            time.sleep(1.5)
else:
    for i, (d, p) in enumerate(sorted(plans.items())):
        print("\n--- serial device %s (%d/%d) ---" % (d, i + 1, len(plans)))
        proc = _launch_device(d, p)
        proc.wait()

for fh in _PHONE_LOG_FHS:
    try:
        fh.close()
    except Exception:
        pass

try:
    import device_record as _drec
    _drec.stop(_REC)
except Exception as e:
    print("[rec] stop skip: %s" % e)

try:
    import ig_pkg
    n_end = ig_pkg.quiet_idle_ig(keep_serials=None)
    if n_end:
        print("[proxy-save] post-run quiet: stopped IG on %d phone(s)" % n_end)
except Exception as e:
    print("[proxy-save] post-run quiet skip: %s" % e)

try:
    import ig_tunnel
    ig_tunnel.disable_many(devices(), label="post-run")
except Exception as e:
    print("[vpn] disable skip: %s" % e)

proxy_dead = []
if os.path.exists(RESULTS_CSV):
    last_login = {}
    for _r in csv.reader(open(RESULTS_CSV, encoding="utf-8")):
        if len(_r) > 7 and _r[1] in plans:
            last_login[_r[1]] = _r[7]
    proxy_dead = [d for d, login in last_login.items() if login == "PROXY_DEAD"]

print("\nALL PHONES DONE. Results -> ig_batch_results.csv")
if proxy_dead:
    print("=" * 60)
    print("FIX NEEDED — PROXY_DEAD phones (accounts NOT burned; will retry next run):")
    for d in proxy_dead:
        print("  %s  → NekoBox ON + SOCKS 127.0.0.1:1080  (or USB/proxy_pool)" % d)
    print("=" * 60)
