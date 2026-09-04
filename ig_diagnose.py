# -*- coding: utf-8 -*-
# ig_diagnose.py - evidence-based IG proof picker (not blind CSV walking).
#
# Why this exists (2026-07-23):
#   We burned three IG attempts on 988a5745584f554c4230 (proxy OK, UI OK,
#   proven accounts, androif). Same Meta "Unable to log in / unexpected error".
#   Threads batch_results on THAT phone: 0 LOGGED_IN since 2026-07-18; recent
#   outcomes are ALL_IPS_MASKED / UNKNOWN_STUCK. So we were proving IG on a
#   Meta-sick device. This tool ranks phones by recent Threads wins + unique
#   IG Nomix clones, then optionally runs one controlled login.
#
#   python ig_diagnose.py                         # inventory + recommendation
#   python ig_diagnose.py --threads-control       # 1 Threads login on best/forced phone
#   python ig_diagnose.py --run                   # 1 IG login+post path on best phone
#   python ig_diagnose.py --serial SERIAL [...]   # force phone
#
# Prereq: start_proxy_pool.bat running; NekoBox on phone pointed at 127.0.0.1:1080.
import os
from farm_root import ROOT
import sys, os, csv, json, subprocess, tempfile, collections, datetime

SINCE = "2026-07-17"
THREADS_RESULTS = os.path.join(ROOT, 'batch_results.csv')
IG_RESULTS = os.path.join(ROOT, 'ig_batch_results.csv')
CSVPATH = os.path.join(ROOT, 'Instagram_farm_accounts.csv')

SERIAL = None
DO_RUN = False
DO_THREADS = False
MODEL = "ig"
COUNTRY = "us"

a = sys.argv[1:]
for k in range(len(a)):
    if a[k] == "--run":
        DO_RUN = True
    if a[k] == "--threads-control":
        DO_THREADS = True
    if k + 1 < len(a):
        if a[k] == "--serial":
            SERIAL = a[k + 1]
        if a[k] == "--model":
            MODEL = a[k + 1].lower()
        if a[k] == "--country":
            COUNTRY = a[k + 1]
        if a[k] == "--since":
            SINCE = a[k + 1]


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True).stdout


def devices():
    out = sh("adb", "devices")
    return [ln.split()[0] for ln in out.splitlines()[1:] if "\tdevice" in ln]


def ig_clones(serial):
    import ig_pkg

    out = sh("adb", "-s", serial, "shell", "pm list packages")
    return ig_pkg.ig_pkgs_from_pm_list(out)


def threads_clones(serial):
    out = sh("adb", "-s", serial, "shell", "pm list packages")
    return sorted(
        ln.split(":", 1)[1].strip()
        for ln in out.splitlines()
        if "com.instagram.barcel" in ln
    )


def prefer_ig(pkgs):
    import ig_account_clone as ac
    return [p for p in pkgs if not ac.is_stock(p)]


def threads_wins_since(since):
    """serial -> count of LOGGED_IN rows on/after since."""
    wins = collections.Counter()
    if not os.path.exists(THREADS_RESULTS):
        return wins
    for r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[7] == "LOGGED_IN" and r[0] >= since:
            wins[r[1]] += 1
    return wins


def threads_last_login():
    out = {}
    if not os.path.exists(THREADS_RESULTS):
        return out
    for r in csv.reader(open(THREADS_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[3] and r[7]:
            out[r[3]] = r[7]
    return out


def ig_tried():
    dead = {
        "ACCOUNT_SUSPENDED",
        "ACCOUNT_CHALLENGED",
        "ALL_IPS_MASKED",
        "LOGIN_META_ERROR",
        "LOGIN_REJECTED",
        "SIGNUP_LOOP",
    }
    tried = set()
    if not os.path.exists(IG_RESULTS):
        return tried
    for r in csv.reader(open(IG_RESULTS, encoding="utf-8")):
        if len(r) > 7 and r[3]:
            login = r[7]
            post = r[8] if len(r) > 8 else ""
            if login in dead or (login == "LOGGED_IN" and post == "POST_DONE"):
                tried.add(r[3])
    return tried


def pick_account_row(accts, model, tried, t_last):
    """Prefer Threads-LOGGED_IN, skip challenged/suspended + already IG-tried."""
    skip = {"ACCOUNT_SUSPENDED", "ACCOUNT_CHALLENGED"}
    ranked = []
    for i, ac in enumerate(accts):
        if (ac.get("model") or "").strip().lower() != model:
            continue
        u = ac.get("username") or ""
        if u in tried or t_last.get(u) in skip:
            continue
        lg = t_last.get(u, "")
        rank = 0 if lg == "LOGGED_IN" else (1 if not lg else 2)
        ranked.append((rank, i, u, lg or "NEVER"))
    ranked.sort()
    return ranked[0] if ranked else None


print("=== IG diagnose (evidence-based phone pick) ===")
print("Threads win window: LOGGED_IN on/after %s" % SINCE)
devs = [SERIAL] if SERIAL else devices()
if not devs:
    print("No adb devices.")
    sys.exit(1)

wins = threads_wins_since(SINCE)
all_ig = {d: ig_clones(d) for d in devs}
_cnt = collections.Counter(c for cl in all_ig.values() for c in cl)
SHARED = {c for c, n in _cnt.items() if n > 1}

rows = []
for d in devs:
    uniq = [c for c in all_ig[d] if c not in SHARED]
    pref = prefer_ig(uniq)
    w = wins.get(d, 0)
    # Score: need at least one unique IG clone; then prefer Threads health.
    score = (0 if pref else -1, w, len(pref))
    rows.append((score, d, pref, w, all_ig[d]))

rows.sort(key=lambda x: x[0], reverse=True)

print("\nPhone rank (native Play Store IG required; Threads wins break ties):")
print("  %-22s %5s %5s  %s" % ("serial", "wins", "uniq", "IG packages"))
for score, d, pref, w, allp in rows:
    mark = (
        " <-- BEST"
        if score[0] >= 0 and pref and d == rows[0][1] and rows[0][0][0] >= 0
        else ""
    )
    if score[0] < 0:
        mark = " (no unique IG clone)"
    if w == 0 and pref:
        mark += "  ** Meta-cold recently (0 Threads LOGGED_IN) **"
    print(
        "  %-22s %5d %5d  %s%s"
        % (
            d,
            w,
            len(pref),
            ",".join(c.split(".")[-1] for c in (pref or allp)) or "-",
            mark,
        )
    )

if SHARED:
    print(
        "\nShared IG pkgs skipped as unique (on >1 phone): %s"
        % ", ".join(sorted(s.split(".")[-1] for s in SHARED))
    )

best = None
for score, d, pref, w, allp in rows:
    if pref:
        best = (d, pref[0], w)
        break

if not best:
    print(
        "\nNo phone has a unique IG Nomix clone."
    )
    sys.exit(2)

bserial, bclone, bwins = best
print(
    "\nRECOMMEND: serial=%s clone=%s (Threads wins since %s: %d)"
    % (bserial, bclone.split(".")[-1], SINCE, bwins)
)
if bwins == 0:
    print(
        "WARNING: recommended phone also has 0 recent Threads wins - "
        "Meta may be sick on all scanned phones, or inventory is thin."
    )
    print("         Still better than forcing a known-cold serial if others have wins.")

# Flag the previously burned proof phone explicitly
COLD = "988a5745584f554c4230"
if COLD in [d for _, d, _, _, _ in rows]:
    cw = wins.get(COLD, 0)
    print(
        "NOTE: %s has %d Threads LOGGED_IN since %s - do NOT use it for IG proof."
        % (COLD, cw, SINCE)
    )

if not DO_RUN and not DO_THREADS:
    print("\nNext:")
    print("  python -u ig_diagnose.py --threads-control   # prove phone Meta-healthy")
    print("  python -u ig_diagnose.py --run               # 1 IG attempt on RECOMMEND")
    print("  (or pass --serial SERIAL to force)")
    sys.exit(0)

# Ensure proxy relay for chosen phone
import proxy_pool

port = proxy_pool.port_for_serial(bserial)
if port is None:
    print("PROXY RELAY missing for %s - start start_proxy_pool.bat" % bserial)
    sys.exit(1)
subprocess.run(
    ["adb", "-s", bserial, "reverse", "tcp:1080", "tcp:%d" % port],
    capture_output=True,
    text=True,
)
print("proxy reverse ok -> 127.0.0.1:%d" % port)

accts = list(csv.DictReader(open(CSVPATH, encoding="utf-8")))
t_last = threads_last_login()
tried = ig_tried()
pick = pick_account_row(accts, MODEL, tried, t_last)
if not pick:
    print("No eligible %s accounts left." % MODEL)
    sys.exit(3)
_rank, row_i, user, tlg = pick
print("account: %s (csv row %d, Threads last=%s)" % (user, row_i, tlg))

if DO_THREADS:
    import threads_loop as th

    th.SERIAL = bserial
    barcels = threads_clones(bserial)
    if not barcels:
        print("No Threads barcel* on this phone - cannot run threads-control.")
        sys.exit(4)
    # Prefer a unique-looking suffix; any local barcel is fine for health check
    tclone = sorted(barcels)[0]
    print("\n=== THREADS CONTROL %s -> %s ===" % (user, tclone.split(".")[-1]))
    session = "diag%04d" % row_i
    exit_ip = th.set_proxy_session(session + "r0001", country=COUNTRY)
    if not exit_ip:
        print("FAIL: proxy exit not verified")
        sys.exit(5)
    a = accts[row_i]
    th.adb("shell", "pm", "clear", tclone)
    login = th.do_login(tclone, a["username"], a["password"], a["tfa_secret"])
    print("THREADS_CONTROL login=%s exit=%s" % (login, exit_ip))
    if login != "LOGGED_IN":
        print(
            "INTERPRET: phone/proxy path failing for Meta on Threads too - "
            "do not blame IG Nomix yet; pick another phone or fix Neko."
        )
        sys.exit(6)
    print(
        "INTERPRET: Threads login works here - phone+proxy OK. IG META_ERROR "
        "on this phone would mean IG package/app issue."
    )
    if not DO_RUN:
        sys.exit(0)

if DO_RUN:
    plan = [{"clone": bclone, "row": row_i, "session": "a%04d" % row_i, "model": MODEL}]
    pf = os.path.join(tempfile.gettempdir(), "ig_diag_%s.json" % bserial)
    json.dump(plan, open(pf, "w", encoding="utf-8"))
    print("\n=== IG RUN %s -> %s ===" % (user, bclone.split(".")[-1]))
    rc = subprocess.call(
        [sys.executable, "-u", "run_ig_device.py", bserial, pf, COUNTRY]
    )
    print("run_ig_device exit=%d  (see ig_batch_results.csv)" % rc)
    sys.exit(rc)
