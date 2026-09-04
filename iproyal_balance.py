# -*- coding: utf-8 -*-
# iproyal_balance.py - pre-run balance gate for the farm.
#
# Why this exists: 2026-07-04 the whole farm's proxy died at once mid-testing
# because the IPRoyal residential GB balance drained to zero. Every phone
# dropped simultaneously and it LOOKED like 20 broken phones for an evening.
# This checks the remaining traffic BEFORE a run and aborts early with a clear
# message instead of letting jobs silently fail against a dead proxy.
#
# Uses IPRoyal's residential API (works even when the proxy tunnel is down,
# because it hits their API server directly, not through the proxy):
#     GET https://resi-api.iproyal.com/v1/me   ->  {"available_traffic": <GB>, ...}
#     Authorization: Bearer <api_token>
#
# The API token is generated in the IPRoyal dashboard (Settings -> API) and
# saved to iproyal_token.txt (one line, just the token). That file is NOT the
# proxy password - it's a read-only account API key, and it stays out of any
# shared script. If the file is missing, this SKIPS the check with a warning
# rather than hard-blocking, so a run is never stuck just because the token
# isn't set up yet.
from farm_root import ROOT
import os, json, urllib.request

TOKEN_FILE = os.path.join(ROOT, 'iproyal_token.txt')
API_URL    = "https://resi-api.iproyal.com/v1/me"
# available_traffic is reported in GB (dashboard shows e.g. "32.6"). Abort a run
# if remaining GB is at/below this. Tune after first real reading confirms unit.
MIN_GB     = 0.5

def _read_token():
    if not os.path.exists(TOKEN_FILE):
        return None
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        t = fh.read().strip()
    return t or None

def get_available_gb():
    """Returns (gb: float|None, raw: dict|str). gb=None means the check could
    not run (no token / network / api error) - caller decides how to treat that."""
    token = _read_token()
    if not token:
        return None, "no-token"
    req = urllib.request.Request(API_URL, headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, "api-error:%s" % e
    gb = data.get("available_traffic")
    try:
        return float(gb), data
    except (TypeError, ValueError):
        return None, data

def ensure_balance(min_gb=MIN_GB):
    """Pre-run gate. Returns True if OK to proceed. Prints a clear line either way.
    Missing token / api error -> WARN + proceed (never hard-block on the check
    itself failing). Real reading at/below threshold -> BLOCK."""
    gb, raw = get_available_gb()
    if gb is None:
        if raw == "no-token":
            print("[balance] no API token set (iproyal_token.txt) - skipping check, proceeding.")
        else:
            print("[balance] WARN: could not read balance (%s) - proceeding without gate." % raw)
        return True
    print("[balance] IPRoyal available traffic: %.2f GB" % gb)
    if gb <= min_gb:
        print("[balance] *** ABORT: only %.2f GB left (<= %.2f). Top up before running "
              "or the whole farm will drop mid-run. ***" % (gb, min_gb))
        return False
    return True

if __name__ == "__main__":
    ok = ensure_balance()
    print("RESULT:", "OK to run" if ok else "BLOCKED - top up first")
