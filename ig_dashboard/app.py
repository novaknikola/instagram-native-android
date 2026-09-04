# -*- coding: utf-8 -*-
"""IG Console Flask application factory - global error handlers, no debug crash pages."""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from . import config
from .routes import api, pages


def create_app() -> Flask:
    root = Path(__file__).resolve().parent
    app = Flask(
        __name__,
        template_folder=str(root / "templates"),
        static_folder=str(root / "static"),
        static_url_path="/static",
    )
    app.secret_key = config.DASH_PASS or "ig-console-dev"
    app.config["PROPAGATE_EXCEPTIONS"] = False
    app.register_blueprint(pages)
    app.register_blueprint(api)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    def _fallback_nav():
        return {
            "active": "overview",
            "run_label": "IDLE",
            "proxy": {
                "ok": False,
                "label": "PROXY ?",
                "msg": "",
                "detail": "",
            },
        }

    @app.errorhandler(HTTPException)
    def http_err(e):
        if e.code in (401, 404, 405):
            return e
        try:
            return (
                render_template(
                    "error.html",
                    nav=_fallback_nav(),
                    msg="%s %s" % (e.code, e.name),
                    ok="0",
                ),
                e.code,
            )
        except Exception:
            return Response_plain("%s" % e, e.code or 500)

    @app.errorhandler(Exception)
    def unhandled(e):
        app.logger.exception("IG Console unhandled: %s", e)
        try:
            from .routes import _nav

            nav = _nav("overview")
        except Exception:
            nav = _fallback_nav()
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
            return Response_plain("Console error: %s" % e, 500)

    return app


def Response_plain(text, code):
    from flask import Response

    return Response(text, code, mimetype="text/plain; charset=utf-8")


def main():
    app = create_app()
    print(
        "IG Console → http://127.0.0.1:%d  (farm base: %s)"
        % (config.PORT, config.BASE)
    )
    # use_reloader=False avoids double-process on Windows / farm hosts
    app.run(
        host=config.HOST,
        port=config.PORT,
        debug=False,
        threaded=True,
        use_reloader=False,
    )
