# -*- coding: utf-8 -*-
# run_ig_device.py - Instagram posts on ONE phone (existing farm pattern).
#   python run_ig_device.py <serial> <plan.json> [country] [fallback_country]
#     [--no-ip-rotate] [--warmup] [--no-auto-replace] [--no-record] [--login-only]
#
# plan.json items may include:
#   clone, row, session, model, format (feed|story|carousel|reel),
#   caption, media_paths, story_link, highlight_title, warmup (bool),
#   content_line / drive_folder_id
#
# Sticky IP: reuse by default; controlled rotate AFTER failure (client 2026-07-30).
# Pass --no-ip-rotate to freeze sticky forever. Results -> ig_batch_results.csv.
# Screen recordings ON by default for standalone runs (skip if farm already records
# via IG_FARM_RECORDING=1, or pass --no-record / IG_NO_RECORD=1).
import os
from farm_root import ROOT
import sys, os, csv, json, time, datetime, shutil, random
import ig_loop as t
import drive_content_ig as dc
import ig_sticky_ip as sticky

try:
    import ig_media_names as mn
except Exception:
    mn = None
try:
    import ig_content_line as cl
except Exception:
    cl = None
try:
    import ig_account_status as aast
except Exception:
    aast = None
try:
    import ig_warmup as wu
except Exception:
    wu = None

SERIAL   = sys.argv[1]
PLAN     = json.load(open(sys.argv[2], encoding="utf-8"))
COUNTRY  = sys.argv[3] if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else "us"
FALLBACK = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith("-") else "us"
_ARGS = sys.argv[3:]
# Controlled rotate ON by default (client ask). Opt out with --no-ip-rotate.
ALLOW_IP_ROTATE = "--no-ip-rotate" not in _ARGS
FORCE_WARMUP = "--warmup" in _ARGS
LOGIN_ONLY = (
    "--login-only" in _ARGS
    or (os.environ.get("IG_LOGIN_ONLY") or "").strip() in ("1", "true", "yes")
)
AUTO_REPLACE = "--no-auto-replace" not in _ARGS
NO_RECORD = (
    "--no-record" in _ARGS
    or (os.environ.get("IG_NO_RECORD") or "").strip() in ("1", "true", "yes")
    or (os.environ.get("IG_FARM_RECORDING") or "").strip() in ("1", "true", "yes")
)
for i, a in enumerate(_ARGS):
    if a == "--country" and i + 1 < len(_ARGS):
        COUNTRY = _ARGS[i + 1]
    if a == "--fallback" and i + 1 < len(_ARGS):
        FALLBACK = _ARGS[i + 1]

t.SERIAL = SERIAL

# Error screenshots ON by default → logs/error_shots/loose (or IG_ERROR_SHOT_DIR).
try:
    import ig_error_shots as _boot_shots
    _shot_home = _boot_shots.ensure_default_env()
    print("[%s] error shots → %s" % (SERIAL, _shot_home))
except Exception as e:
    print("[%s] error shots init skip: %s" % (SERIAL, e))

# Standalone screen recording (farm parent already records when IG_FARM_RECORDING=1).
_REC = None
try:
    import device_record as _drec
    _REC = _drec.start([SERIAL], enabled=not NO_RECORD)
    if _REC:
        print("[%s] recording → %s" % (SERIAL, _REC.run_dir))
except Exception as e:
    print("[%s] recording start skip: %s" % (SERIAL, e))
    _REC = None

try:
    import ig_pkg as _igpkg
except Exception:
    _igpkg = None

PROXY_SAVE_BUILD = "quiet_ig_20260819b"
PROXY_SAVE_QUIET_SLEEP = 2.0  # seconds after force-stop for CDN/chat to die


def _quiet_ig():
    """Stop IG clones on this phone after shots/results. Does not pm clear."""
    if _igpkg is None:
        return
    try:
        _igpkg.force_stop_ig_farm(SERIAL)
    except Exception as e:
        print("[proxy-save] force-stop skip: %s" % e)

RESULTS = os.path.join(ROOT, 'ig_batch_results.csv')

IP_MASK = {"CAPTCHA", "CONTACT_VERIFY", "LOGIN_REJECTED", "HUMAN_BLOCKED"}

try:
    import ig_error_shots as _errshots
except Exception:
    _errshots = None


def _shot(result, username="", note="", force=False):
    """Runner-level safety net for fails outside ig_loop (proxy, media, etc.)."""
    if _errshots is None:
        return
    try:
        _errshots.capture(
            serial=SERIAL,
            result=result,
            username=username or "",
            note=note or "run_ig_device",
            include_xml=True,
            force=bool(force),
        )
    except Exception as e:
        print("[shot] runner skip: %s" % e)


CURRENT_MARK = ""  # RETRY when this plan item is a queued recoverable retry


def _story_tail(item=None):
    meta = {}
    try:
        meta = t.last_post_meta()
    except Exception:
        pass
    wu = ""
    if item and (item.get("warmup") or item.get("do_warmup")):
        wu = "1"
    return [
        meta.get("story_link") or "",
        meta.get("link_ok") or "",
        meta.get("highlight_ok") or "",
        wu,
    ]


def log_result(cols):
    """cols: time,serial,clone,username,session,country,exit_ip,login,post,image
            [,format[,mark[,story_link[,link_ok[,highlight_ok[,warmup]]]]]]
    """
    cols = list(cols)
    while len(cols) < 11:
        cols.append("")
    if len(cols) == 11:
        cols.append(CURRENT_MARK or "")
    elif len(cols) >= 12 and not (cols[11] or "").strip():
        cols[11] = CURRENT_MARK or ""
    while len(cols) < 16:
        cols.append("")
    new = not os.path.exists(RESULTS)
    with open(RESULTS, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["time", "serial", "clone", "username", "session",
                        "country", "exit_ip", "login", "post", "image", "format",
                        "mark", "story_link", "link_ok", "highlight_ok", "warmup"])
        w.writerow(cols)


def _already_logged_in(clone, username):
    """Reuse session only if Feed AND the planned username is on Profile."""
    if not t.launch(clone):
        return False
    time.sleep(2.0)
    t._drain_post_login_tips(max_steps=8)
    st = t.detect_state(t.dump())
    if st not in ("FEED", "CREATE_PICKER", "CAPTION_SCREEN"):
        return False
    if t.session_matches_user(username):
        print("[session] already LOGGED_IN as %s (state=%s) — skip cold login"
              % (username, st))
        return True
    print("[session] clone has a session but not %s — pm clear + cold login"
          % username)
    return False


def login_with_sticky(clone, u, p, s, base_session, primary, fallback):
    """Sticky first (per clone); on IP_MASK do ONE controlled rotate if allowed.

    Prove farm SOCKS exit BEFORE opening Instagram (CDN + edge-chat).
    Any exit country is accepted; never return LOGGED_IN without a live exit IP.
    """
    token, sticky_cc, reused = sticky.make_session_token(base_session, SERIAL, clone)
    country = sticky_cc or primary or "us"

    def _verify_exit(sess, cc):
        exit_ip = t.set_proxy_session(sess, country=cc)
        if exit_ip:
            return exit_ip
        print("[%s] %s | IP not verified (%s/%s) — retry same sticky"
              % (SERIAL, u, cc, sess))
        time.sleep(2.0)
        return t.set_proxy_session(sess, country=cc)

    print("[%s] %s | sticky clone=%s session=%s country=%s reused=%s rotate=%s"
          % (SERIAL, u, clone.split(".")[-1], token, country, reused, ALLOW_IP_ROTATE))

    exit_ip = _verify_exit(token, country)
    if not exit_ip:
        print("=" * 60)
        print("[FIX NEEDED] PROXY_DEAD on %s (account %s NOT burned — sticky kept)"
              % (SERIAL, u))
        print("=" * 60)
        return "PROXY_DEAD", country, token, None

    if _already_logged_in(clone, u):
        sticky.bind(SERIAL, clone, token, country, exit_ip, username=u)
        return "LOGGED_IN", country, token, exit_ip

    def _one_try(sess, cc, clear_app=True, known_ip=None):
        ip = known_ip
        if not ip:
            ip = t.set_proxy_session(sess, country=cc)
            if not ip:
                print("[%s] %s | IP not verified (%s/%s)" % (SERIAL, u, cc, sess))
                return None, None
        if clear_app:
            t.adb("shell", "pm", "clear", clone)
            time.sleep(1.5)
        login = t.do_login(clone, u, p, s)
        return login, ip

    login, exit_ip = _one_try(token, country, clear_app=True, known_ip=exit_ip)
    if login is None and exit_ip is None:
        print("[%s] %s | PROXY retry same sticky session" % (SERIAL, u))
        time.sleep(2.0)
        login, exit_ip = _one_try(token, country, clear_app=True)
        if exit_ip is None:
            print("=" * 60)
            print("[FIX NEEDED] PROXY_DEAD on %s (account %s NOT burned — sticky kept)"
                  % (SERIAL, u))
            print("=" * 60)
            return "PROXY_DEAD", country, token, None

    if login == "LOGGED_IN":
        sticky.bind(SERIAL, clone, token, country, exit_ip or "", username=u)
        return login, country, token, exit_ip

    if login in IP_MASK:
        sticky.bind(SERIAL, clone, token, country, exit_ip or "", username=u)
        if not ALLOW_IP_ROTATE:
            print("[%s] %s | %s on sticky — NOT rotating (--no-ip-rotate)"
                  % (SERIAL, u, login))
            return login, country, token, exit_ip
        rot, cc = sticky.controlled_rotate_token(
            SERIAL, clone, country=fallback or primary or country
        )
        print("[%s] %s | controlled rotate after %s -> %s" % (SERIAL, u, login, rot))
        login2, exit2 = _one_try(rot, cc, clear_app=True)
        if login2 == "LOGGED_IN":
            sticky.bind(SERIAL, clone, rot, cc, exit2 or "", username=u)
            return login2, cc, rot, exit2
        if exit2:
            sticky.bind(SERIAL, clone, rot, cc, exit2, username=u)
        # Keep CAPTCHA / CONTACT_VERIFY / etc. Floppy exit is live; this is
        # not the old Threads ALL_IPS_MASKED (dead lookup / exhausted IP budget).
        return login2 or login, cc, rot, exit2

    # Bind sticky on Meta/login fails too — clone keeps same exit next run.
    if exit_ip:
        sticky.bind(SERIAL, clone, token, country, exit_ip, username=u)
    return login, country, token, exit_ip


def _ensure_content_line(u, item):
    if cl is None:
        return
    fid = (item.get("drive_folder_id") or "").strip()
    if not fid:
        try:
            import drive_content_ig_account as dca
            fid = dca.folder_for(u, serial=SERIAL) or ""
            # folder_for may already resolve via content line — also check map
            if not fid:
                fid = (dca.load_map() or {}).get(u) or ""
        except Exception:
            fid = ""
    if fid:
        cl.ensure_line(u, fid, warmup_profiles=item.get("warmup_profiles"))


def _normalize_local_media(paths, workdir):
    """Copy any unsafe local basenames (spaces/parens) into workdir with safe names.

    Drive download already sanitizes; schedule media_paths may still be dirty.
    Push renames on-device too — this keeps PC paths clean for adb/logging.
    """
    out = []
    os.makedirs(workdir, exist_ok=True)
    for p in paths:
        p = os.path.abspath(p)
        if not os.path.isfile(p):
            print("[%s] WARN missing media path: %s" % (SERIAL, p))
            continue
        base = os.path.basename(p)
        if mn is None or mn.is_safe_basename(base):
            out.append(p)
            continue
        dest_name = mn.safe_local_filename(base, file_id="%d" % int(time.time() * 1000))
        dest = os.path.join(workdir, dest_name)
        try:
            shutil.copy2(p, dest)
            print("[%s] normalized media %s -> %s" % (SERIAL, base, dest_name))
            out.append(dest)
        except Exception as e:
            print("[%s] normalize copy fail (%s) — using original: %s" % (SERIAL, e, base))
            out.append(p)
    return out


accts = list(csv.DictReader(open(t.CSVPATH, encoding="utf-8")))

MODEL   = (PLAN[0].get("model") if PLAN else None) or "ig"
WORKDIR = os.path.join(dc.DL_DIR, "ig", SERIAL)

try:
    import drive_content_ig_account as dca
    HAS_ACCOUNT_DRIVE = bool(dca.folder_for("", serial=SERIAL) or dca.has_phone_drive())
    if cl is not None and dca.load_map():
        try:
            n = cl.bootstrap_from_drive_map(dca.load_map())
            print("[%s] content-lines bootstrapped from Drive map (%d)" % (SERIAL, n))
        except Exception as e:
            print("[%s] content-line bootstrap: %s" % (SERIAL, e))
    phone_fid = dca.folder_for("", serial=SERIAL)
    if phone_fid:
        print("[%s] phone Drive folder=%s…" % (SERIAL, phone_fid[:12]))
    elif dca.has_phone_drive():
        print("[%s] WARN no Drive folder mapped for this serial — skip media"
              % SERIAL)
except Exception:
    dca = None
    HAS_ACCOUNT_DRIVE = False

drive, sheets = dc.services()
try:
    caps = dc.captions(sheets, MODEL)
except Exception as e:
    print("[%s] captions fetch failed (%s) — continue with empty pool" % (SERIAL, e))
    caps = []
print("[%s] IG model=%s (%d captions) sticky=on rotate=%s content_drive=%s proxy-save=%s"
      % (SERIAL, MODEL, len(caps), ALLOW_IP_ROTATE, HAS_ACCOUNT_DRIVE, PROXY_SAVE_BUILD))

_SKIP_USERS = set()  # usernames that hit LOGIN_META_ERROR this run — no more formats

for item in PLAN:
    local_paths = []
    media_ids = []
    post = "SKIPPED"
    fmt = "reel"
    u = ""
    try:
        CURRENT_MARK = "RETRY" if item.get("retry") or str(item.get("mark") or "").strip().upper() == "RETRY" else ""
        clone = item.get("clone") or ""
        if not clone.startswith("com.instagram."):
            clone = "com.instagram." + clone if clone else "com.instagram.android"
        row, session = item["row"], item["session"]
        primary  = item.get("country", COUNTRY)
        fallback = item.get("fallback", FALLBACK)
        fmt = (item.get("format") or "reel").strip().lower()
        if fmt not in ("feed", "story", "carousel", "reel"):
            fmt = "reel"
        a = accts[row]
        u, p, s = a["username"], a["password"], a["tfa_secret"]
        if (u or "").strip().lower() in _SKIP_USERS:
            print("[%s] skip %s format=%s — earlier LOGIN_META_ERROR this run"
                  % (SERIAL, u, fmt))
            log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                        SERIAL, clone, u, "", primary, "",
                        "LOGIN_META_ERROR", "SKIPPED", "meta_no_retry", fmt])
            continue
        _ensure_content_line(u, item)

        try:
            import ig_account_clone as _acbind
        except Exception:
            _acbind = None

        def _honor_bind():
            if _acbind is None:
                return ""
            b = _acbind.lookup(u)
            if b:
                want_s = (b.get("serial") or "").strip()
                if want_s and want_s != SERIAL:
                    print("[%s] skip %s - bind phone %s (this is %s)"
                          % (SERIAL, u, want_s[-8:], SERIAL[-8:]))
                    return "BIND_WRONG_PHONE"
                want_c = _acbind.full_pkg(b.get("clone") or "")
                if want_c:
                    if want_c != clone:
                        print("[%s] bind %s -> %s (plan had %s)"
                              % (SERIAL, u, want_c.split(".")[-1], clone.split(".")[-1]))
                    return want_c
                return ""
            _acbind.assign_new(u, SERIAL, clone)
            return ""

        # Skip known-dead occupants (unless this plan already swapped)
        if aast and not aast.is_usable(u):
            st = (aast.get(u) or {}).get("status")
            if st in ("dead", "banned", "disabled", "replaced"):
                print("[%s] skip unusable %s status=%s" % (SERIAL, u, st))
                if AUTO_REPLACE and aast:
                    ok, neu = aast.auto_replace(u, reason=st or "unusable")
                    if ok:
                        print("[%s] auto-replaced %s -> %s (same content line)"
                              % (SERIAL, u, neu))
                        # Remap row to new username if present in CSV
                        for i2, r2 in enumerate(accts):
                            if (r2.get("username") or "").strip() == neu:
                                row, a = i2, r2
                                u, p, s = a["username"], a["password"], a["tfa_secret"]
                                break
                        else:
                            log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                                        SERIAL, clone, u, "", primary, "",
                                        "REPLACED_NO_CREDS", "SKIPPED", neu, fmt])
                            continue
                    else:
                        log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                                    SERIAL, clone, u, "", primary, "",
                                    "DEAD", "SKIPPED", str(neu), fmt])
                        continue

        bound = _honor_bind()
        if bound == "BIND_WRONG_PHONE":
            log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                        SERIAL, clone, u, "", primary, "",
                        "BIND_WRONG_PHONE", "SKIPPED", "", fmt])
            continue
        if bound:
            clone = bound

        print("\n=== [%s] IG %s -> %s format=%s (%s, sticky) ==="
              % (SERIAL, u, clone.split('.')[-1], fmt, primary))

        try:
            import ig_step_watch as _swdev
            _swdev.configure(
                serial=SERIAL,
                username=u,
                pkg=clone,
                goal="login + %s post for %s" % (fmt, u),
                fmt=fmt,
            )
        except Exception as e:
            print("[%s] step-watch init skip: %s" % (SERIAL, e))

        _quiet_ig()
        login, country, used_session, exit_ip = login_with_sticky(
            clone, u, p, s, session, primary, fallback)

        if login == "APP_WONT_OPEN":
            print("[%s] APP_WONT_OPEN -> force-stop + one more login on same sticky IP"
                  % SERIAL)
            t.force_stop(clone)
            time.sleep(1.5)
            if t.launch(clone):
                login = t.do_login(clone, u, p, s)
                if login == "LOGGED_IN":
                    sticky.bind(
                        SERIAL, clone, used_session, country, exit_ip or "", username=u
                    )
            else:
                print("[%s] soft relaunch failed to open %s"
                      % (SERIAL, clone.split(".")[-1]))

        if login and login != "LOGGED_IN":
            # ig_loop already shots most codes; catch proxy-level + leftovers
            if login == "LOGIN_META_ERROR":
                _SKIP_USERS.add((u or "").strip().lower())
            if login in ("PROXY_DEAD", "ALL_IPS_MASKED", "DEAD", "REPLACED_NO_CREDS"):
                _shot(login, username=u, note="runner_login")
            elif _errshots is not None and login not in getattr(
                _errshots, "SHOT_RESULTS", set()
            ):
                _shot(login, username=u, note="runner_login_extra", force=True)

        post = "SKIPPED"
        img_name = ""
        media_paths = item.get("media_paths") or []
        caption = (item.get("caption") or "").strip()
        story_link = (item.get("story_link") or item.get("link") or "").strip()
        # Empty highlight_title = skip highlights (fail-closed when set).
        highlight_title = (item.get("highlight_title") or "").strip()
        do_wu = FORCE_WARMUP or bool(item.get("warmup"))

        if LOGIN_ONLY:
            do_wu = False
            if login == "LOGGED_IN":
                print("[%s] LOGIN_ONLY — session kept, skip warmup/post, force-stop"
                      % SERIAL)

        if login == "LOGGED_IN" and not LOGIN_ONLY:
            is_retry = bool(
                CURRENT_MARK == "RETRY" or item.get("retry")
                or str(item.get("mark") or "").strip().upper() == "RETRY")
            if do_wu:
                profiles = item.get("warmup_profiles") or []
                if cl is not None and not profiles:
                    profiles = cl.warmup_profiles_for(u)
                if wu is not None:
                    wres = t.do_warmup(
                        pkg=clone,
                        profiles=wu.load_profiles(extra=profiles),
                    )
                else:
                    wres = t.do_warmup(pkg=clone, profiles=profiles)
                print("[%s] warmup=%s" % (SERIAL, wres))
                if wres == "WARMUP_PARTIAL":
                    login = "LOGGED_IN"
                    post = "SKIPPED"
                    _shot("WARMUP_PARTIAL", username=u, note="warmup_no_reels_or_home")
                elif wres in ("CAPTCHA", "HUMAN_CHECK", "CONTACT_VERIFY",
                            "POST_RATE_LIMIT", "ACTION_LIMIT"):
                    if wres in ("POST_RATE_LIMIT", "ACTION_LIMIT"):
                        login = "LOGGED_IN"
                        post = "POST_RATE_LIMIT"
                        _shot("POST_RATE_LIMIT", username=u, note="warmup_action_limit")
                    else:
                        login = wres
                        post = "SKIPPED"
                        _shot(login if login != "HUMAN_CHECK" else "CAPTCHA",
                              username=u, note="warmup_gate_runner")

            if login == "LOGGED_IN" and post != "POST_RATE_LIMIT":
                media_ids = []
                if media_paths and all(os.path.isfile(p) for p in media_paths):
                    paths = media_paths
                    img_name = ",".join(os.path.basename(p) for p in paths)
                elif HAS_ACCOUNT_DRIVE and dca.has_content_drive(u, serial=SERIAL):
                    got = dca.next_media(drive, u, fmt, WORKDIR,
                                         count=3 if fmt == "carousel" else 1,
                                         serial=SERIAL)
                    if not got:
                        print("[%s] no %s media for account %s" % (SERIAL, fmt, u))
                        _shot("NO_IMAGE", username=u, note="no_drive_media_%s" % fmt)
                        log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                                    SERIAL, clone, u, used_session, country, exit_ip or "",
                                    login, "NO_IMAGE", "", fmt])
                        continue
                    if isinstance(got, list):
                        paths = [g["path"] for g in got]
                        media_ids = [g["id"] for g in got]
                        img_name = ",".join(g["name"] for g in got)
                    else:
                        paths = [got["path"]]
                        media_ids = [got["id"]]
                        img_name = got["name"]
                    if not caption:
                        caption = dca.caption_for(u, sheets, fmt, serial=SERIAL) or dc.pick_caption(caps, row)
                else:
                    if fmt:
                        print("[%s] format=%s needs a Drive folder for this phone "
                              "(ig_phone_drive_map.json or ig_phones_drive_folder.txt) — skipping"
                              % (SERIAL, fmt))
                        _shot("NO_ACCOUNT_DRIVE", username=u,
                              note="format_%s_needs_phone_drive" % fmt)
                        log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                                    SERIAL, clone, u, used_session, country, exit_ip or "",
                                    login, "NO_ACCOUNT_DRIVE", "", fmt])
                        continue

                paths = _normalize_local_media(paths, WORKDIR)
                local_paths = list(paths)
                if not paths:
                    print("[%s] no usable local media after normalize - skip post" % SERIAL)
                    _shot("NO_IMAGE", username=u, note="normalize_empty")
                    log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                                SERIAL, clone, u, used_session, country, exit_ip or "",
                                login, "NO_IMAGE", img_name, fmt])
                    continue

                post = t.do_post(
                    caption=caption,
                    image_path=paths[0] if len(paths) == 1 else None,
                    media_paths=paths,
                    username=u,
                    pkg=clone,
                    format=fmt,
                    story_link=story_link if fmt == "story" else "",
                    highlight_title=highlight_title if fmt == "story" else "",
                    retry=is_retry,
                )
                if post == "POST_DONE":
                    if HAS_ACCOUNT_DRIVE and media_ids and dca.has_content_drive(u, serial=SERIAL):
                        for mid in media_ids:
                            dca.mark_used(drive, mid, u, fmt, serial=SERIAL)
                    elif media_ids and fmt == "feed":
                        for mid in media_ids:
                            dc.mark_used(drive, mid, MODEL)
            else:
                print("[%s] %s | login=%s - not posting" % (SERIAL, u, login))

        if aast:
            action = aast.note_outcome(u, login, post)
            if action == "replace" and AUTO_REPLACE:
                ok, neu = aast.auto_replace(u, reason="%s/%s" % (login, post))
                print("[%s] auto-replace after outcome: ok=%s -> %s" % (SERIAL, ok, neu))

        log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                    SERIAL, clone, u, used_session, country, exit_ip or "",
                    login, post, img_name, fmt, CURRENT_MARK or ""] + _story_tail(item))
        print("[%s] %s | country=%s exit=%s login=%s post=%s format=%s"
              % (SERIAL, u, country, exit_ip, login, post, fmt))
    except Exception as e:
        et = type(e).__name__
        print("[%s] account error, skipping to next: %s: %s" % (SERIAL, et, e))
        try:
            _shot("ERROR", username=str(item.get("username") or ""),
                  note="%s: %s" % (et, e), force=True)
        except Exception:
            pass
        try:
            log_result([datetime.datetime.now().isoformat(timespec="seconds"),
                        SERIAL, item.get("clone", ""), "", "", COUNTRY, "",
                        "ERROR", "SKIPPED", et, item.get("format", "reel")])
        except Exception:
            pass
        continue
    finally:
        if post != "POST_DONE" and media_ids and dca is not None:
            try:
                dca.release_claim(u, fmt, media_ids)
            except Exception as e:
                print("[%s] release claim skip: %s" % (SERIAL, e))
        if local_paths:
            try:
                if dca is not None:
                    dca.cleanup_local_media(local_paths)
                else:
                    for p in local_paths:
                        try:
                            if p and os.path.isfile(p):
                                os.remove(p)
                        except Exception:
                            pass
            except Exception as e:
                print("[%s] local media cleanup skip: %s" % (SERIAL, e))
        _quiet_ig()
        time.sleep(PROXY_SAVE_QUIET_SLEEP)

print("[%s] IG device batch done." % SERIAL)
try:
    import device_record as _drec
    _drec.stop(_REC)
except Exception as e:
    print("[%s] recording stop skip: %s" % (SERIAL, e))
