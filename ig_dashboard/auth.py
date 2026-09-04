# -*- coding: utf-8 -*-
"""HTTP basic auth for the IG console (same password file pattern as Threads dash)."""
from __future__ import annotations

import secrets
from functools import wraps

from flask import Response, request

from . import config

_MISSING_PASS_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>IG Console - setup</title>
<style>
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:#03060a;color:#d7e6f5;font-family:system-ui,sans-serif}
.box{max-width:480px;padding:28px;border:1px solid rgba(240,113,120,.4);
background:rgba(10,16,26,.95)}
h1{margin:0 0 12px;font-size:18px;color:#f07178;letter-spacing:.08em}
p{margin:0 0 8px;line-height:1.5;color:#8aa0b8;font-size:14px}
code{color:#b9dcff}
</style></head><body><div class="box">
<h1>PASSWORD NOT CONFIGURED</h1>
<p>Create <code>C:\\threads-android\\dashboard_password.txt</code> with one line
(the password), or set env <code>DASH_PASS</code>, then restart the console.</p>
<p>Same file as the Threads dashboard.</p>
</div></body></html>"""


def check_auth(username: str, password: str) -> bool:
    if not config.DASH_PASS:
        return False
    try:
        u_ok = secrets.compare_digest(username or "", config.DASH_USER)
        p_ok = secrets.compare_digest(password or "", config.DASH_PASS)
        return u_ok and p_ok
    except Exception:
        return False


def require_auth(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        try:
            if not config.DASH_PASS:
                return Response(_MISSING_PASS_HTML, 503, mimetype="text/html")
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return Response(
                    "Login required",
                    401,
                    {"WWW-Authenticate": 'Basic realm="IG Console"'},
                )
            return view(*args, **kwargs)
        except Exception as e:
            return Response(
                "Auth error: %s" % e,
                500,
                mimetype="text/plain",
            )

    return wrapped
