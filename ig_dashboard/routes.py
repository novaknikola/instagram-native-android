# -*- coding: utf-8 -*-
"""Flask blueprints: pages + JSON API. Soft-fail everything; UI never crashes."""
from __future__ import annotations

import json
import time
from pathlib import Path

from flask import (
    Blueprint,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    stream_with_context,
    url_for,
)

from . import config
from .auth import require_auth
from .services import (
    AccountPool,
    BindLedger,
    ContentPool,
    DeviceFleet,
    EcosystemControl,
    FarmRunner,
    ProxyPoolHealth,
    ResultLedger,
    ScheduleView,
    ScreenService,
    StickyDriveHealth,
    SystemHealth,
)
from .services.screens import safe_serial
from .util import normalize_per_page, paginate, safe_int

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__, url_prefix="/api")

_ledger = ResultLedger()
_accounts = AccountPool(_ledger)
_binds = BindLedger()
_devices = DeviceFleet()
_content = ContentPool()
_runner = FarmRunner()
_proxy = ProxyPoolHealth()
_system = SystemHealth()
_schedule = ScheduleView()
_sticky = StickyDriveHealth()
_eco = EcosystemControl()
_screens = ScreenService(farm_active_fn=_runner.is_process_active)


def _nav(active: str):
    try:
        run = _runner.status()
    except Exception:
        run = {"label": "IDLE"}
    try:
        proxy = _proxy.status()
    except Exception:
        proxy = {
            "ok": False,
            "label": "PROXY ?",
            "msg": "Proxy check failed",
            "detail": "",
        }
    return {
        "active": active,
        "run_label": run.get("label") or "IDLE",
        "proxy": proxy,
    }


def _safe_page(active, render_fn):
    try:
        return render_fn()
    except Exception as e:
        try:
            nav = _nav(active)
        except Exception:
            nav = {
                "active": active,
                "run_label": "IDLE",
                "proxy": {
                    "ok": False,
                    "label": "PROXY ?",
                    "msg": "Could not check proxy pool.",
                    "detail": "health check failed",
                },
            }
        try:
            return (
                render_template(
                    "error.html",
                    nav=nav,
                    msg="%s: %s" % (type(e).__name__, e),
                    ok="0",
                ),
                500,
            )
        except Exception:
            from flask import Response

            return Response("Console error: %s" % e, 500)


@pages.route("/")
@require_auth
def overview():
    def _go():
        fleet = _devices.summary()
        ledger = _ledger.summary()
        pool = _accounts.stats()
        run = _runner.status()
        health = _system.snapshot()
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        dev_rows, dev_total, dev_page, dev_pages, _ = paginate(
            fleet.get("devices") or [], request.args.get("dev_page"), per_page
        )
        recent_all = _ledger.load(limit=200)
        recent, recent_total, recent_page, recent_pages, _ = paginate(
            recent_all, request.args.get("recent_page"), per_page
        )
        fleet = dict(fleet)
        fleet["devices"] = dev_rows
        return render_template(
            "overview.html",
            nav=_nav("overview"),
            fleet=fleet,
            ledger=ledger,
            pool=pool,
            run=run,
            recent=recent,
            health=health,
            per_page=per_page,
            dev_page=dev_page,
            dev_pages=dev_pages,
            dev_total=dev_total,
            recent_page=recent_page,
            recent_pages=recent_pages,
            recent_total=recent_total,
        )

    return _safe_page("overview", _go)


@pages.route("/content")
@require_auth
def content():
    def _go():
        snap = _content.phone_snapshot()
        mapping = {}
        try:
            import drive_content_ig_account as dca
            mapping = dca.load_phone_map()
        except Exception:
            mapping = {}
        import json
        map_text = json.dumps(mapping, indent=2, sort_keys=True) if mapping else ""
        return render_template(
            "content.html",
            nav=_nav("content"),
            snap=snap,
            map_text=map_text,
            msg=request.args.get("msg", ""),
            ok=request.args.get("ok", ""),
        )

    return _safe_page("content", _go)


@pages.route("/content/reset", methods=["POST"])
@require_auth
def content_reset():
    try:
        serial = request.form.get("serial", "")
        ok, msg = _content.reset_ledger(serial=serial)
        return redirect(url_for("pages.content", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.content", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/content/phone-map", methods=["POST"])
@require_auth
def content_phone_map():
    try:
        ok, msg = _content.save_phone_map_text(request.form.get("map_text") or "")
        return redirect(url_for("pages.content", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.content", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/content/phones-root", methods=["POST"])
@require_auth
def content_phones_root():
    try:
        import drive_content_ig_account as dca

        fid = (request.form.get("folder_id") or "").strip()
        if not fid:
            return redirect(
                url_for("pages.content", msg="Paste parent Drive folder ID.", ok="0")
            )
        dca.set_phones_root_id(fid)
        return redirect(
            url_for(
                "pages.content",
                msg="Phones parent folder saved. Child folders named by serial.",
                ok="1",
            )
        )
    except Exception as e:
        return redirect(
            url_for("pages.content", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/run")
@require_auth
def run_page():
    def _go():
        fleet = _devices.summary()
        proxy = _proxy.status()
        health = _system.snapshot()
        return render_template(
            "run.html",
            nav=_nav("run"),
            run=_runner.status(),
            fleet=fleet,
            proxy=proxy,
            health=health,
            log=_runner.tail_log(100),
            recent_logs=_ledger.enrich_run_logs(_runner.list_run_logs(15)),
            msg=request.args.get("msg", ""),
            ok=request.args.get("ok", ""),
        )

    return _safe_page("run", _go)


@pages.route("/run/start", methods=["POST"])
@require_auth
def run_start():
    try:
        proxy = _proxy.status()
        if not proxy.get("ok"):
            return redirect(
                url_for(
                    "pages.run_page",
                    msg=proxy.get("msg")
                    or "Proxy pool is not running. Start start_proxy_pool.bat first.",
                    ok="0",
                )
            )
        health = _system.snapshot()
        if health.get("blockers"):
            return redirect(
                url_for(
                    "pages.run_page",
                    msg=health["blockers"][0],
                    ok="0",
                )
            )
        ok, msg = _runner.start(
            country=request.form.get("country", "us"),
            per_device=1,
            model=request.form.get("model", ""),
            devices=request.form.get("devices", ""),
            allow_unproven=bool(request.form.get("allow_unproven")),
            parallel=not bool(request.form.get("serial")),
            limit=safe_int(request.form.get("limit"), 0, lo=0, hi=500),
            farm_format=(request.form.get("format") or "reel").strip().lower(),
            max_inflight=safe_int(request.form.get("max_inflight"), 0, lo=0, hi=20),
            e2e=bool(request.form.get("e2e")),
            story_link=(request.form.get("story_link") or "").strip(),
            highlight=(request.form.get("highlight") or "").strip(),
        )
        return redirect(url_for("pages.run_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.run_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/run/stop", methods=["POST"])
@require_auth
def run_stop():
    try:
        ok, msg = _runner.stop()
        nxt = request.form.get("next") or "run"
        endpoint = "pages.schedule_page" if nxt == "schedule" else "pages.run_page"
        return redirect(url_for(endpoint, msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.run_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/history")
@require_auth
def history():
    def _go():
        login = request.args.get("login", "")
        post = request.args.get("post", "")
        fmt = request.args.get("format", "")
        mark = request.args.get("mark", "")
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        all_rows = _ledger.load(
            limit=5000, login=login, post=post, format=fmt, mark=mark
        )
        rows, total, page, pages_n, per_page = paginate(
            all_rows, request.args.get("page"), per_page
        )
        return render_template(
            "history.html",
            nav=_nav("history"),
            rows=rows,
            login=login,
            post=post,
            fmt=fmt,
            mark=mark,
            summary=_ledger.summary(),
            page=page,
            pages_n=pages_n,
            total=total,
            per_page=per_page,
        )

    return _safe_page("history", _go)


@pages.route("/schedule")
@require_auth
def schedule_page():
    def _go():
        snap = _schedule.load()
        sticky = _sticky.sticky_snapshot()
        drive = _sticky.drive_map_snapshot(peek_folders=False)
        shared = drive.get("shared") or _sticky.shared_drive_snapshot(peek=False)
        try:
            from .services.schedule import sheet_config_snapshot

            sheet_cfg = sheet_config_snapshot()
        except Exception:
            sheet_cfg = {
                "sheet_id": snap.get("sheet_id") or "",
                "path": str(getattr(config, "SCHEDULE_SHEET_FILE", "")),
                "ok": bool(snap.get("sheet_id")),
                "from_env": False,
                "error": snap.get("error") or "",
            }
        retry_ready = 0
        try:
            import sys

            for _p in (Path(config.BASE), Path(__file__).resolve().parents[1]):
                s = str(_p)
                if s not in sys.path:
                    sys.path.insert(0, s)
            import ig_scheduler as sch

            sched_rows = (
                list(snap.get("due") or [])
                + list(snap.get("upcoming") or [])
                + list(snap.get("pending") or [])
            )
            retry_ready = int((sch.preview_retry_queue(rows=sched_rows).get("ready") or 0))
        except Exception:
            retry_ready = 0
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        due_rows, due_total, due_page, due_pages, _ = paginate(
            snap.get("due") or [], request.args.get("due_page"), per_page
        )
        upcoming_rows, upcoming_total, upcoming_page, upcoming_pages, _ = paginate(
            snap.get("upcoming") or [], request.args.get("upcoming_page"), per_page
        )
        recent_rows, recent_total, recent_page, recent_pages, _ = paginate(
            snap.get("recent") or [], request.args.get("recent_page"), per_page
        )
        sticky_rows, sticky_total, sticky_page, sticky_pages, _ = paginate(
            sticky.get("rows") or [], request.args.get("sticky_page"), per_page
        )
        snap = dict(snap)
        snap["due"] = due_rows
        snap["upcoming"] = upcoming_rows
        snap["recent"] = recent_rows
        sticky = dict(sticky)
        sticky["rows"] = sticky_rows
        return render_template(
            "schedule.html",
            nav=_nav("schedule"),
            sched=snap,
            sticky=sticky,
            shared=shared,
            sheet_cfg=sheet_cfg,
            run=_runner.status(),
            retry_ready=retry_ready,
            msg=request.args.get("msg", ""),
            ok=request.args.get("ok", ""),
            per_page=per_page,
            due_page=due_page,
            due_pages=due_pages,
            due_total=due_total,
            upcoming_page=upcoming_page,
            upcoming_pages=upcoming_pages,
            upcoming_total=upcoming_total,
            recent_page=recent_page,
            recent_pages=recent_pages,
            recent_total=recent_total,
            sticky_page=sticky_page,
            sticky_pages=sticky_pages,
            sticky_total=sticky_total,
        )

    return _safe_page("schedule", _go)


@pages.route("/schedule/queue-retries", methods=["POST"])
@require_auth
def schedule_queue_retries():
    """Queue recoverable fails as due-now jobs. Does not start the farm."""
    try:
        import sys

        for _p in (Path(config.BASE), Path(__file__).resolve().parents[1]):
            s = str(_p)
            if s not in sys.path:
                sys.path.insert(0, s)
        import ig_scheduler as sch

        info = sch.queue_recoverable_retries()
        if not info.get("ok"):
            err = info.get("error") or "Could not queue retries."
            if "HttpError" in err or "429" in err or "Quota" in err:
                err = sch.friendly_sheets_error(err)
            return redirect(
                url_for(
                    "pages.schedule_page",
                    msg=err,
                    ok="0",
                )
            )
        n = int(info.get("queued") or 0)
        skip = int(info.get("skipped_pending") or 0)
        if n <= 0:
            extra = (" (%d already pending)" % skip) if skip else ""
            msg = "No new retries to queue%s. Eligible: LOGGED_IN/POST_BLOCKED, LOGGED_IN/POST_TIMEOUT, LOGIN_TIMEOUT/SKIPPED." % extra
        else:
            msg = "Queued %d retry job(s) as due now (mark=RETRY). Then click Run due now.%s" % (
                n,
                (" %d already had a pending row." % skip) if skip else "",
            )
        return redirect(url_for("pages.schedule_page", msg=msg, ok="1"))
    except Exception as e:
        try:
            msg = sch.friendly_sheets_error(e)
        except Exception:
            msg = "Could not queue retries. Wait a minute and try once."
        return redirect(
            url_for(
                "pages.schedule_page",
                msg=msg,
                ok="0",
            )
        )


@pages.route("/schedule/run", methods=["POST"])
@require_auth
def schedule_run():
    try:
        proxy = _proxy.status()
        if not proxy.get("ok"):
            return redirect(
                url_for(
                    "pages.schedule_page",
                    msg=proxy.get("msg")
                    or "Proxy pool is not running. Start start_proxy_pool.bat first.",
                    ok="0",
                )
            )
        dry = bool(request.form.get("dry"))
        ok, msg = _runner.start_schedule(dry=dry)
        if dry:
            return redirect(url_for("pages.schedule_page", msg=msg, ok="1" if ok else "0"))
        return redirect(url_for("pages.run_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        try:
            import sys
            for _p in (Path(config.BASE), Path(__file__).resolve().parents[1]):
                s = str(_p)
                if s not in sys.path:
                    sys.path.insert(0, s)
            import ig_scheduler as sch

            msg = sch.friendly_sheets_error(e)
        except Exception:
            msg = "Schedule run failed. Wait a minute and try once."
        return redirect(
            url_for(
                "pages.schedule_page",
                msg=msg,
                ok="0",
            )
        )


@pages.route("/schedule/sheet", methods=["POST"])
@require_auth
def schedule_sheet_save():
    try:
        import sys

        for _p in (Path(config.BASE), Path(__file__).resolve().parents[1]):
            s = str(_p)
            if s not in sys.path:
                sys.path.insert(0, s)
        import ig_scheduler as sch

        sid = (request.form.get("sheet_id") or "").strip()
        if not sid:
            return redirect(
                url_for(
                    "pages.schedule_page",
                    msg="Paste a Google Sheet ID (or spreadsheet URL) first.",
                    ok="0",
                )
            )
        saved = sch.set_schedule_sheet_id(sid)
        return redirect(
            url_for(
                "pages.schedule_page",
                msg="Schedule Sheet saved (%s…). Refresh if counts look empty."
                % (saved[:12] if saved else "?"),
                ok="1",
            )
        )
    except Exception as e:
        return redirect(
            url_for(
                "pages.schedule_page",
                msg="%s: %s" % (type(e).__name__, e),
                ok="0",
            )
        )


@pages.route("/content/shared-drive", methods=["POST"])
@require_auth
def shared_drive_save():
    try:
        import sys
        from pathlib import Path

        for _p in (Path(config.BASE), Path(__file__).resolve().parents[1]):
            s = str(_p)
            if s not in sys.path:
                sys.path.insert(0, s)
        import drive_content_ig_account as dca

        fid = (request.form.get("folder_id") or "").strip()
        if not fid:
            return redirect(
                url_for(
                    "pages.schedule_page",
                    msg="Paste a Google Drive folder ID first.",
                    ok="0",
                )
            )
        dca.set_shared_folder_id(fid)
        return redirect(
            url_for(
                "pages.schedule_page",
                msg="Shared Drive saved. All accounts use this folder.",
                ok="1",
            )
        )
    except Exception as e:
        return redirect(
            url_for(
                "pages.schedule_page",
                msg="%s: %s" % (type(e).__name__, e),
                ok="0",
            )
        )


@pages.route("/lines")
@require_auth
def lines_page():
    def _go():
        lines = _eco.lines_snapshot()
        statuses = _eco.status_snapshot()
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        line_rows, line_total, line_page, line_pages, _ = paginate(
            lines.get("rows") or [], request.args.get("line_page"), per_page
        )
        status_rows, status_total, status_page, status_pages, _ = paginate(
            statuses.get("rows") or [], request.args.get("status_page"), per_page
        )
        lines = dict(lines)
        lines["rows"] = line_rows
        statuses = dict(statuses)
        statuses["rows"] = status_rows
        return render_template(
            "lines.html",
            nav=_nav("lines"),
            lines=lines,
            statuses=statuses,
            warmup=_eco.warmup_snapshot(),
            msg=request.args.get("msg", ""),
            ok=request.args.get("ok", ""),
            per_page=per_page,
            line_page=line_page,
            line_pages=line_pages,
            line_total=line_total,
            status_page=status_page,
            status_pages=status_pages,
            status_total=status_total,
        )

    return _safe_page("lines", _go)


@pages.route("/lines/ensure", methods=["POST"])
@require_auth
def lines_ensure():
    try:
        ok, msg = _eco.ensure_line(
            request.form.get("username", ""),
            request.form.get("folder_id", ""),
            request.form.get("warmup_profiles", ""),
        )
        return redirect(url_for("pages.lines_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.lines_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/lines/bootstrap", methods=["POST"])
@require_auth
def lines_bootstrap():
    try:
        ok, msg = _eco.bootstrap_map()
        return redirect(url_for("pages.lines_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.lines_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/lines/replace", methods=["POST"])
@require_auth
def lines_replace():
    try:
        ok, msg = _eco.replace(
            request.form.get("dead", ""),
            request.form.get("new", ""),
        )
        return redirect(url_for("pages.lines_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.lines_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/lines/warmup", methods=["POST"])
@require_auth
def lines_warmup_save():
    try:
        ok, msg = _eco.save_warmup_text(request.form.get("profiles", ""))
        return redirect(url_for("pages.lines_page", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.lines_page", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/accounts")
@require_auth
def accounts():
    def _go():
        model = request.args.get("model", "")
        status = request.args.get("status", "")
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        page = safe_int(request.args.get("page"), 1, lo=1)
        rows, total = _accounts.list_accounts(
            model=model, status=status, page=page, per_page=per_page
        )
        pages_n = max(1, (total + per_page - 1) // per_page) if total else 1
        if page > pages_n:
            page = pages_n
            rows, total = _accounts.list_accounts(
                model=model, status=status, page=page, per_page=per_page
            )
        return render_template(
            "accounts.html",
            nav=_nav("accounts"),
            rows=rows,
            total=total,
            page=page,
            pages_n=pages_n,
            per_page=per_page,
            model=model,
            status=status,
            pool=_accounts.stats(),
            msg=request.args.get("msg", ""),
            ok=request.args.get("ok", ""),
        )

    return _safe_page("accounts", _go)


@pages.route("/binds")
@require_auth
def binds_page():
    def _go():
        snap = _binds.snapshot()
        per_page = normalize_per_page(request.args.get("per_page"), 25)
        rows, total, page, pages_n, per_page = paginate(
            snap.get("rows") or [], request.args.get("page"), per_page
        )
        return render_template(
            "binds.html",
            nav=_nav("binds"),
            rows=rows,
            total=total,
            page=page,
            pages_n=pages_n,
            per_page=per_page,
            snap=snap,
        )

    return _safe_page("binds", _go)


@pages.route("/accounts/add", methods=["POST"])
@require_auth
def accounts_add():
    try:
        ok, msg = _accounts.add_account(
            request.form.get("username", ""),
            request.form.get("password", ""),
            request.form.get("tfa_secret", ""),
            request.form.get("model", "ig") or "ig",
        )
        return redirect(url_for("pages.accounts", msg=msg, ok="1" if ok else "0"))
    except Exception as e:
        return redirect(
            url_for("pages.accounts", msg="%s: %s" % (type(e).__name__, e), ok="0")
        )


@pages.route("/phones")
@require_auth
def phones_page():
    def _go():
        return render_template(
            "phones.html",
            nav=_nav("phones"),
            pillow_ok=_screens.pillow_ok(),
            stale_secs=int(config.SCREEN_STALE_SECS),
            farm_active=_runner.is_process_active(),
        )

    return _safe_page("phones", _go)


@api.route("/status")
@require_auth
def api_status():
    try:
        fleet = _devices.summary()
        return jsonify(
            {
                "ok": True,
                "run": _runner.status(),
                "proxy": _proxy.status(),
                "health": _system.snapshot(),
                "ledger": _ledger.summary(),
                "pool": _accounts.stats(),
                "fleet": {
                    "phones": fleet.get("phones", 0),
                    "ready": fleet.get("ready", 0),
                    "unique_clones": fleet.get("unique_clones", 0),
                    "open_ok_clones": fleet.get("open_ok_clones", 0),
                    "ignored_clones": fleet.get("ignored_clones", 0),
                    "allowlist_on": fleet.get("allowlist_on", False),
                    "error": fleet.get("error", ""),
                },
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)})


@api.route("/proxy")
@require_auth
def api_proxy():
    try:
        return jsonify(_proxy.status())
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e), "label": "PROXY ?"})


@api.route("/health")
@require_auth
def api_health():
    try:
        return jsonify(_system.snapshot())
    except Exception as e:
        return jsonify({"ready": False, "blockers": [str(e)], "warnings": [], "checks": []})


@api.route("/log")
@require_auth
def api_log():
    try:
        lines = safe_int(request.args.get("lines"), 120, lo=20, hi=500)
        st = _runner.status()
        return jsonify(
            {
                "text": _runner.tail_log(lines),
                "active": bool(st.get("active")),
                "log_name": st.get("log_name") or "",
                "log_path": st.get("log_path") or "",
                "logs_dir": st.get("logs_dir") or "",
                "error_shot_dir": st.get("error_shot_dir") or "",
            }
        )
    except Exception as e:
        return jsonify({"text": "(log error: %s)" % e, "active": False})


@api.route("/log/phones")
@require_auth
def api_log_phones():
    """Per-phone log tails for the N-monitor grid."""
    try:
        lines = safe_int(request.args.get("lines"), 80, lo=20, hi=400)
        return jsonify(_runner.phone_logs_snapshot(lines=lines))
    except Exception as e:
        return jsonify({"ok": False, "count": 0, "phones": [], "error": str(e)})


@api.route("/log/device")
@require_auth
def api_log_device():
    """Latest automation log lines for one ADB serial."""
    try:
        serial = (request.args.get("serial") or "").strip()
        lines = safe_int(request.args.get("lines"), 250, lo=40, hi=2000)
        return jsonify(_runner.device_log(serial, lines=lines))
    except Exception as e:
        return jsonify(
            {
                "ok": False,
                "serial": "",
                "text": "",
                "error": "%s" % e,
                "match_count": 0,
            }
        )


@api.route("/fleet/clones")
@require_auth
def api_fleet_clones():
    """Per-phone unique IG clone map + even-spread plan (Overview modal)."""
    try:
        force = (request.args.get("fresh") or "").strip() in ("1", "true", "yes")
        return jsonify(_devices.clone_map(force=force))
    except Exception as e:
        return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e), "devices": []})


@api.route("/fleet/board")
@require_auth
def api_fleet_board():
    """Per-phone live status during a farm run."""
    try:
        return jsonify(_runner.fleet_board())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "phones": [], "active": False})


@api.route("/log/list")
@require_auth
def api_log_list():
    try:
        return jsonify(
            {
                "ok": True,
                "logs_dir": str(_runner.logs_dir),
                "current": _runner.log_path.name if _runner.log_path.exists() else "",
                "logs": _ledger.enrich_run_logs(
                    _runner.list_run_logs(
                        safe_int(request.args.get("limit"), 30, lo=1, hi=100)
                    )
                ),
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e, "logs": []})


@api.route("/error-shots")
@require_auth
def api_error_shots():
    """List error screenshots for current (or named) run folder."""
    try:
        import ig_error_shots as es

        st = _runner.status()
        directory = (request.args.get("dir") or st.get("error_shot_dir") or "").strip()
        if not directory:
            # newest run folder under logs
            logs = Path(st.get("logs_dir") or config.LOGS_DIR)
            runs = []
            if logs.is_dir():
                runs = sorted(
                    [p for p in logs.iterdir() if p.is_dir() and p.name.startswith("ig_run_")],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            directory = str(runs[0]) if runs else str(es.shot_dir())
        rows = es.list_shots(
            limit=safe_int(request.args.get("limit"), 40, lo=1, hi=200),
            directory=directory,
        )
        return jsonify(
            {
                "ok": True,
                "dir": directory,
                "count": len(rows),
                "shots": rows,
            }
        )
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e, "shots": [], "dir": ""})


@pages.route("/run/error-shot")
@require_auth
def run_error_shot_file():
    """Download/view a single error shot PNG or XML (safe basename)."""
    try:
        import ig_error_shots as es

        name = (request.args.get("name") or "").strip()
        directory = (request.args.get("dir") or "").strip()
        st = _runner.status()
        if not directory:
            directory = st.get("error_shot_dir") or ""
        path = es.resolve_shot_file(name, directory=directory or None)
        if not path:
            return redirect(
                url_for("pages.run_page", msg="Error shot not found", ok="0")
            )
        mime = "image/png" if path.suffix.lower() == ".png" else "text/plain; charset=utf-8"
        as_att = request.args.get("download") == "1" or path.suffix.lower() != ".png"
        return send_file(
            path,
            mimetype=mime,
            as_attachment=as_att,
            download_name=path.name,
        )
    except Exception as e:
        return redirect(
            url_for("pages.run_page", msg="Error shot failed: %s" % e, ok="0")
        )


@pages.route("/run/log/download")
@require_auth
def run_log_download():
    """Download current or named per-run txt (safe basename only)."""
    try:
        name = (request.args.get("name") or "").strip()
        path = _runner.resolve_log_file(name)
        if not path or not path.exists():
            return redirect(
                url_for(
                    "pages.run_page",
                    msg="Log file not found",
                    ok="0",
                )
            )
        return send_file(
            path,
            mimetype="text/plain; charset=utf-8",
            as_attachment=True,
            download_name=path.name,
        )
    except Exception as e:
        return redirect(
            url_for(
                "pages.run_page",
                msg="Download failed: %s" % e,
                ok="0",
            )
        )


@api.route("/history")
@require_auth
def api_history():
    try:
        rows = _ledger.load(
            limit=safe_int(request.args.get("limit"), 100, lo=1, hi=1000),
            login=request.args.get("login", ""),
            post=request.args.get("post", ""),
            format=request.args.get("format", ""),
        )
        return jsonify([r.as_dict() for r in rows])
    except Exception as e:
        return jsonify([])


@api.route("/analytics/overview")
@require_auth
def api_analytics_overview():
    """Cross-run BI from ig_batch_results.csv (last N days)."""
    try:
        days = safe_int(request.args.get("days"), 14, lo=1, hi=90)
        return jsonify(_ledger.analytics_overview(days=days))
    except Exception as e:
        return jsonify(
            {
                "days": 14,
                "attempts": 0,
                "post_done": 0,
                "success_rate": 0.0,
                "daily": [],
                "login_counts": {},
                "post_counts": {},
                "format_counts": {},
                "top_fails": [],
                "error": str(e),
            }
        )


@api.route("/analytics/run")
@require_auth
def api_analytics_run():
    """Per-run BI: CSV rows in this log's time window."""
    try:
        name = (request.args.get("name") or "").strip()
        if not name or not _runner.resolve_log_file(name):
            return jsonify({"error": "unknown run", "attempts": 0, "rows": []})
        logs = _runner.list_run_logs(100)
        next_name = ""
        for i, item in enumerate(logs):
            if item.get("name") == name:
                # newer run is previous index (newest-first)
                if i > 0:
                    next_name = logs[i - 1].get("name") or ""
                break
        data = _ledger.analytics_for_run(name, next_name)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "attempts": 0, "rows": []})


@api.route("/schedule")
@require_auth
def api_schedule():
    try:
        return jsonify(_schedule.load())
    except Exception as e:
        return jsonify({"error": str(e), "due": [], "counts": {}})


@api.route("/sticky")
@require_auth
def api_sticky():
    try:
        return jsonify(
            {
                "sticky": _sticky.sticky_snapshot(),
                "shared": _sticky.shared_drive_snapshot(peek=False),
                "drive": _sticky.drive_map_snapshot(peek_folders=False),
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)})


@api.route("/screens")
@require_auth
def api_screens():
    try:
        snap = _screens.snapshot(farm_active=_runner.is_process_active())
        try:
            board = _runner.fleet_board()
            by_serial = {p["serial"]: p for p in (board.get("phones") or [])}
            for p in snap.get("phones") or []:
                b = by_serial.get(p.get("serial") or "") or {}
                p["job_phase"] = b.get("phase") or ""
                p["job_login"] = b.get("login") or ""
                p["job_post"] = b.get("post") or ""
                p["job_user"] = b.get("username") or ""
            snap["run_mode"] = board.get("run_mode") or ""
        except Exception:
            pass
        return jsonify(snap)
    except Exception as e:
        return jsonify(
            {
                "adb_ok": False,
                "error": "%s: %s" % (type(e).__name__, e),
                "phones": [],
                "counts": {},
                "job": {},
                "pillow_ok": _screens.pillow_ok(),
            }
        )


@api.route("/screens/rescan", methods=["POST"])
@require_auth
def api_screens_rescan():
    try:
        listing = _screens.rescan()
        return jsonify({"ok": True, **listing, **_screens.snapshot()})
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e})


@api.route("/screens/jobs", methods=["POST"])
@require_auth
def api_screens_jobs():
    try:
        body = request.get_json(silent=True) or {}
        mode = (body.get("mode") or request.form.get("mode") or "stale").strip()
        wake = bool(body.get("wake") or request.form.get("wake"))
        serials = body.get("serials") or []
        if isinstance(serials, str):
            serials = [s.strip() for s in serials.split(",") if s.strip()]
        if mode == "one" and serials:
            return jsonify(_screens.enqueue_one(serials[0], wake=wake))
        return jsonify(_screens.start_mode(mode, serials=serials, wake=wake))
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e})


@api.route("/screens/jobs/cancel", methods=["POST"])
@require_auth
def api_screens_cancel():
    try:
        return jsonify(_screens.cancel())
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e})


@api.route("/screens/job")
@require_auth
def api_screens_job():
    try:
        return jsonify(_screens.job_status())
    except Exception as e:
        return jsonify({"active": False, "error": "%s" % e})


@api.route("/screens/<serial>/refresh", methods=["POST"])
@require_auth
def api_screens_refresh_one(serial):
    try:
        s = safe_serial(serial)
        if not s:
            return jsonify({"ok": False, "error": "invalid serial"}), 400
        wake = bool((request.get_json(silent=True) or {}).get("wake"))
        return jsonify(_screens.enqueue_one(s, wake=wake))
    except Exception as e:
        return jsonify({"ok": False, "error": "%s" % e})


@api.route("/screens/<serial>/thumb")
@require_auth
def api_screens_thumb(serial):
    try:
        s = safe_serial(serial)
        if not s:
            return Response("bad serial", 400)
        path = _screens.paths(s)["thumb"]
        if not path.exists():
            return Response("no thumb", 404)
        resp = send_file(path, mimetype="image/jpeg", conditional=True)
        resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp
    except Exception as e:
        return Response("error: %s" % e, 500)


@api.route("/screens/<serial>/full")
@require_auth
def api_screens_full(serial):
    try:
        s = safe_serial(serial)
        if not s:
            return Response("bad serial", 400)
        path = _screens.paths(s)["full"]
        if not path.exists():
            return Response("no shot", 404)
        resp = send_file(path, mimetype="image/png", conditional=True)
        resp.headers["Cache-Control"] = "no-cache, max-age=0"
        return resp
    except Exception as e:
        return Response("error: %s" % e, 500)


@api.route("/screens/events")
@require_auth
def api_screens_events():
    """SSE stream for Fleet Screens job progress."""
    q = _screens.subscribe()

    def gen():
        try:
            yield "event: hello\ndata: {}\n\n"
            # snapshot push
            try:
                yield "event: snapshot\ndata: %s\n\n" % json.dumps(
                    _screens.snapshot(farm_active=_runner.is_process_active())
                )
            except Exception:
                pass
            last_ping = time.time()
            while True:
                try:
                    msg = q.get(timeout=12.0)
                    yield "event: %s\ndata: %s\n\n" % (
                        msg.get("event") or "message",
                        json.dumps(msg.get("data") or {}),
                    )
                except Exception:
                    now = time.time()
                    if now - last_ping >= 12:
                        yield ": ping\n\n"
                        last_ping = now
        finally:
            _screens.unsubscribe(q)

    return Response(
        stream_with_context(gen()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
