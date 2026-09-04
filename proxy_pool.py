# -*- coding: utf-8 -*-
# proxy_pool.py - PC-SIDE proxy farm. The clean way to give every phone its own
# clean IP WITHOUT touching NekoBox per-account or fighting per-phone tap coords.
#
# Architecture:
#   * For each connected phone we open a LOCAL SOCKS5 listener on the PC
#     (127.0.0.1:108NN). Each listener forwards every connection UP through
#     IPRoyal (geo.iproyal.com:12324) using THAT phone's current session token,
#     so each phone exits on its own sticky residential IP.
#   * `adb -s <serial> reverse tcp:1080 tcp:108NN` bridges the phone's localhost
#     1080 back to the PC listener over USB. Every phone's on-device config is
#     then IDENTICAL: NekoBox -> socks://127.0.0.1:1080 (no credentials).
#   * IP ROTATION IS PC-SIDE: to give a phone's next account a fresh IP, write a
#     new token to session_<port>.txt (see set_session). The phone never knows;
#     no taps, no coordinates, no per-phone UI. Creds never touch the phones.
#
# This is pure Python (no GOST/sing-box binary needed). TCP CONNECT only, which
# is all HTTPS/Threads needs.
#
#   python proxy_pool.py            # start the daemon: listeners + adb reverse for all phones
#   python proxy_pool.py --verify   # also curl ipinfo.io through each phone's chain from the PC
#   python proxy_pool.py --recover  # optional: one hard ADB reset THEN start (not the default)
#
# ADB policy (2026-08-12): quiet happy path — no taskkill on every start.
# Hard recover at most ONCE after a proven hang (timeout / no response).
# "0 phones" with a normal devices listing = USB — do NOT kill adb.
# Explicit repair: repair_adb.bat (or --recover). No Admin required for normal use.
import os
from farm_root import ROOT
import sys, os, socket, struct, threading, subprocess, time, re, json

# PROVIDER selects which upstream residential/mobile proxy the relay dials.
#   "iproyal"    - SOCKS5, modifiers in the PASSWORD field
#   "geonode"    - HTTP CONNECT, modifiers appended to USERNAME (sticky port 10000)
#   "floppydata" - HTTP CONNECT to Floppydata mobile (client 2026-08-07). Creds in
#                  floppydata_proxy.json (or env). Prefer sticky/long session in dashboard;
#                  rotation=15m mid-login will break LOGGED_IN proves.
PROVIDER = os.environ.get("IG_PROXY_PROVIDER", "floppydata").strip().lower() or "floppydata"
# Printed at startup so Windows ops can confirm the deployed file (not an old crashy copy).
POOL_BUILD = "floppy_sess_20260820a"

PROXY_HOST = "geo.iproyal.com"
PROXY_PORT = 12324
PROXY_USER = os.environ.get("IPROYAL_USER", "")
PROXY_PASS = os.environ.get("IPROYAL_PASS", "")          # modifiers (country/session/lifetime) appended per-phone

GEONODE_HOST = "proxy.geonode.io"
GEONODE_PORT = 10000                      # sticky (9000 = rotating, rejects -session-)
GEONODE_USER = os.environ.get("GEONODE_USER", "")
GEONODE_PASS = os.environ.get("GEONODE_PASS", "")

# Floppydata: paste FULL username from dashboard → Proxy strings → Copy all
# (screenshot username is truncated — incomplete user breaks auth).
FLOPPY_CFG_PATHS = [
    os.path.join(os.environ.get("IG_FARM_BASE", ROOT), "floppydata_proxy.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "floppydata_proxy.json"),
]

BASE_PORT  = 10801                        # phone #1 -> 10801, #2 -> 10802, ...
PHONE_LOCAL_PORT = 1080                   # what NekoBox on every phone points at
STATE_DIR  = os.path.join(ROOT, 'proxy_state')   # session_<port>.txt live here

os.makedirs(STATE_DIR, exist_ok=True)


def _load_floppy():
    """Load Floppydata host/port/user/pass. Env overrides file."""
    cfg = {
        "host": os.environ.get("FLOPPYDATA_HOST", "").strip(),
        "port": os.environ.get("FLOPPYDATA_PORT", "").strip(),
        "username": os.environ.get("FLOPPYDATA_USER", "").strip(),
        "password": os.environ.get("FLOPPYDATA_PASS", "").strip(),
        "append_session": True,
    }
    for path in FLOPPY_CFG_PATHS:
        if not os.path.isfile(path):
            continue
        try:
            raw = json.load(open(path, encoding="utf-8"))
            if isinstance(raw, dict):
                for k in ("host", "username", "password"):
                    if raw.get(k) and not cfg[k]:
                        cfg[k] = str(raw[k]).strip()
                if raw.get("port") and not cfg["port"]:
                    cfg["port"] = str(raw["port"]).strip()
                if "append_session" in raw:
                    cfg["append_session"] = bool(raw["append_session"])
                cfg["_path"] = path
                break
        except Exception as e:
            print("[proxy] floppydata cfg read fail %s: %s" % (path, e))
    try:
        cfg["port"] = int(cfg["port"] or 10080)
    except ValueError:
        cfg["port"] = 10080
    return cfg


def _floppy_safe_token(token):
    """Floppydata session ids are alphanumeric; farm tokens have _ / rot suffixes."""
    safe = re.sub(r"[^A-Za-z0-9]", "", (token or "").strip()) or "farm"
    return safe[:24]


def floppy_username(port):
    """Dashboard username with THIS phone's farm session (not Floppydata's baked-in one).

    Floppydata 'Copy all' always embeds `-session-xxxxxxxx`. Leaving that in place
    makes every phone share one exit IP → parallel cold logins all get CAPTCHA.
    Replace (or append) with the per-port farm token so rotate-after-captcha is real.
    """
    cfg = _load_floppy()
    base = (cfg.get("username") or "").strip()
    if not base or base.startswith("PASTE_") or "…" in base or "..." in base:
        raise OSError(
            "Floppydata username missing/truncated — paste FULL user from "
            "Proxy strings → Copy all into floppydata_proxy.json"
        )
    if not cfg.get("append_session", True):
        return base
    _country, token, _ = _split_session(port)
    safe = _floppy_safe_token(token)
    if re.search(r"(?i)-session-[^-]+", base):
        return re.sub(r"(?i)-session-[^-]+", "-session-" + safe, base, count=1)
    return "%s-session-%s" % (base, safe)

# ---------------------------------------------------------------------------
# session token per listener-port (the file is the rotation control surface)
# ---------------------------------------------------------------------------
def session_file(port):
    """Session control file for this listener port.

    Library callers (IG farm) must write next to the *live* proxy map
    (often threads-farm\\proxy_state) so the running daemon picks up the token.
    """
    _, ports_path = load_device_ports()
    d = os.path.dirname(ports_path) if ports_path else STATE_DIR
    return os.path.join(d, "session_%d.txt" % port)

def get_session(port):
    try:
        return open(session_file(port), encoding="utf-8").read().strip()
    except FileNotFoundError:
        return "dev%d" % port            # default sticky token until rotated

def set_session(port, token, country="us", lifetime="24h"):
    """Rotate this phone's exit IP: next connection uses this fresh token."""
    payload = "%s|%s|%s" % (country, token, lifetime)
    open(session_file(port), "w", encoding="utf-8").write(payload)

def _split_session(port):
    raw = get_session(port)
    parts = raw.split("|")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]     # country, token, lifetime
    return "us", raw, "24h"

def upstream_password(port):
    """IPRoyal: modifiers live in the PASSWORD field."""
    country, token, lifetime = _split_session(port)
    return "%s_country-%s_session-%s_lifetime-%s" % (PROXY_PASS, country, token, lifetime)

def geonode_username(port):
    """Geonode: modifiers are appended to the USERNAME (dash-separated), not the
    password. Verified format via direct probe - underscore breaks auth (407),
    dash works. Lifetime has no Geonode equivalent (sticky duration is a per-token
    dashboard/API setting, not a per-request modifier), so it's dropped here."""
    country, token, _ = _split_session(port)
    return "%s-country-%s-session-%s" % (GEONODE_USER, country, token)

# ---------------------------------------------------------------------------
# SOCKS5 upstream dial (IPRoyal)
# ---------------------------------------------------------------------------
def dial_upstream_socks5(dst_host, dst_port, password):
    up = socket.create_connection((PROXY_HOST, PROXY_PORT), timeout=20)
    # greeting: version 5, 1 method, 0x02 = username/password
    up.sendall(b"\x05\x01\x02")
    if up.recv(2) != b"\x05\x02":
        up.close(); raise OSError("upstream refused user/pass auth")
    # auth sub-negotiation
    u = PROXY_USER.encode(); p = password.encode()
    up.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
    if up.recv(2) != b"\x01\x00":
        up.close(); raise OSError("upstream auth rejected")
    # CONNECT to target by domain name (atyp 0x03)
    h = dst_host.encode()
    up.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", dst_port))
    resp = up.recv(4)
    if len(resp) < 2 or resp[1] != 0x00:
        up.close(); raise OSError("upstream CONNECT failed (code %s)" %
                                  (resp[1] if len(resp) > 1 else "?"))
    # drain the bound address that follows
    atyp = resp[3]
    if atyp == 0x01:   up.recv(4 + 2)
    elif atyp == 0x03: ln = up.recv(1)[0]; up.recv(ln + 2)
    elif atyp == 0x04: up.recv(16 + 2)
    return up

# ---------------------------------------------------------------------------
# HTTP CONNECT upstream dial (Geonode - SOCKS5 times out on their residential
# ports, confirmed by direct probe 2026-07-16, so this tunnels via HTTP CONNECT
# with Basic auth instead)
# ---------------------------------------------------------------------------
def dial_upstream_http(dst_host, dst_port, user, password, proxy_host=None, proxy_port=None):
    import base64
    proxy_host = proxy_host or GEONODE_HOST
    proxy_port = int(proxy_port or GEONODE_PORT)
    up = socket.create_connection((proxy_host, proxy_port), timeout=20)
    cred = base64.b64encode(("%s:%s" % (user, password)).encode()).decode()
    req = ("CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n"
           "Proxy-Authorization: Basic %s\r\n\r\n") % (dst_host, dst_port, dst_host, dst_port, cred)
    up.sendall(req.encode())
    # read the HTTP status line + headers (until blank line) without over-reading the tunnel body
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = up.recv(1)
        if not chunk:
            up.close(); raise OSError("upstream closed during CONNECT response")
        buf += chunk
    status_line = buf.split(b"\r\n", 1)[0]
    if b" 200 " not in status_line:
        up.close(); raise OSError("upstream CONNECT failed: %r" % status_line[:100])
    return up

def dial_upstream(dst_host, dst_port, port):
    """Provider-agnostic dial: builds the right creds for the active PROVIDER and
    returns a connected+tunnelled socket to dst_host:dst_port."""
    if PROVIDER == "floppydata":
        cfg = _load_floppy()
        host = cfg.get("host") or "geo.g-w.info"
        pport = cfg.get("port") or 10080
        pw = cfg.get("password") or ""
        if not pw:
            raise OSError("Floppydata password missing in floppydata_proxy.json")
        return dial_upstream_http(
            dst_host, dst_port, floppy_username(port), pw,
            proxy_host=host, proxy_port=pport,
        )
    if PROVIDER == "geonode":
        return dial_upstream_http(dst_host, dst_port, geonode_username(port), GEONODE_PASS)
    return dial_upstream_socks5(dst_host, dst_port, upstream_password(port))

# ---------------------------------------------------------------------------
# local SOCKS5 server (no auth) -> dials upstream per connection
# ---------------------------------------------------------------------------
def pipe(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try: s.shutdown(socket.SHUT_RDWR)
            except OSError: pass

def handle_client(conn, port):
    try:
        # greeting
        head = conn.recv(2)
        if len(head) < 2 or head[0] != 0x05:
            conn.close(); return
        conn.recv(head[1])                  # methods
        conn.sendall(b"\x05\x00")           # no-auth OK
        # request
        req = conn.recv(4)
        if len(req) < 4 or req[1] != 0x01:  # only CONNECT
            conn.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00"); conn.close(); return
        atyp = req[3]
        if atyp == 0x01:
            host = socket.inet_ntoa(conn.recv(4))
        elif atyp == 0x03:
            host = conn.recv(conn.recv(1)[0]).decode()
        elif atyp == 0x04:
            host = socket.inet_ntop(socket.AF_INET6, conn.recv(16))
        else:
            conn.close(); return
        dport = struct.unpack(">H", conn.recv(2))[0]
        # dial upstream (provider = PROVIDER constant) using THIS port's current session
        try:
            up = dial_upstream(host, dport, port)
        except OSError:
            conn.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00"); conn.close(); return
        conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")   # success
        threading.Thread(target=pipe, args=(conn, up), daemon=True).start()
        pipe(up, conn)
    except OSError:
        try: conn.close()
        except OSError: pass

def serve(port):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(64)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handle_client, args=(conn, port), daemon=True).start()

# ---------------------------------------------------------------------------
# device wiring + verification
# ---------------------------------------------------------------------------
# Hard recover at most once per process (happy path never kills adb).
_hard_recover_done = False


def adb(*args, timeout=30):
    """Run adb; never raise TimeoutExpired — hung ADB must not crash the pool."""
    try:
        r = subprocess.run(
            ["adb", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return r.stdout or ""
    except subprocess.TimeoutExpired:
        print("[adb] TIMEOUT after %ss: adb %s" % (timeout, " ".join(args)))
        return ""
    except FileNotFoundError:
        print("[adb] ERROR: 'adb' not on PATH — install platform-tools / fix PATH")
        return ""
    except Exception as e:
        print("[adb] ERROR: %s" % e)
        return ""


def _kill_hung_adb():
    """Windows: force-kill adb.exe. Instant; safe if none running. Rare path only."""
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "adb.exe"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass


def soft_start_server():
    """Ask ADB daemon to start — no kill. Safe when server is already up."""
    print("[adb] soft start-server (no kill)…")
    adb("start-server", timeout=20)
    time.sleep(0.6)


def adb_recover(force=False):
    """Hard reset stuck ADB. At most once per run unless force=True.

    Never call hung `adb kill-server` first — it can block forever on Windows.
    Force-kill adb.exe, then start-server with a hard timeout.
    """
    global _hard_recover_done
    if _hard_recover_done and not force:
        print("[adb] hard recover already used this run — skip (use repair_adb.bat)")
        return False
    print("[adb] HARD recover: taskkill adb.exe → start-server …")
    _kill_hung_adb()
    time.sleep(1.0)
    adb("start-server", timeout=20)
    time.sleep(1.0)
    _hard_recover_done = True
    return True


def _parse_adb_devices(out):
    """Return (serials_device, serials_offline_or_unauth) from `adb devices` text."""
    serials = [
        ln.split()[0]
        for ln in (out or "").splitlines()[1:]
        if "\tdevice" in ln
    ]
    offline = [
        ln.split()[0]
        for ln in (out or "").splitlines()[1:]
        if "\toffline" in ln or "\tunauthorized" in ln
    ]
    return serials, offline


def devices():
    """List authorized phones — quiet first; hard kill only after proven hang (once).

    - Normal listing with phones → return them (no kill).
    - Normal listing with 0 phones → USB/unlock problem; do NOT kill adb.
    - Timeout / empty response → soft start-server, retry; then one hard recover.
    """
    def _from_out(out, label=""):
        if not (out or "").strip():
            return None  # no response / timeout
        serials, offline = _parse_adb_devices(out)
        if serials:
            if offline:
                print("[adb] note: %d offline/unauthorized (ignored)" % len(offline))
            if label:
                print("[adb] %s — %d phone(s)" % (label, len(serials)))
            return serials
        # ADB answered; zero authorized — do not treat as hung daemon
        print("[adb] ADB responded but no authorized phones"
              + (" (%s)" % label if label else ""))
        if offline:
            print("[adb] visible but not ready: %s" % ", ".join(offline))
        print("[adb] Fix: unlock phones → check USB hub cables → then re-run.")
        print("[adb] Not killing adb (daemon looks fine).")
        return []

    # 1) Quiet — use whatever server is already running
    print("[adb] devices (quiet)…")
    got = _from_out(adb("devices", timeout=15), "quiet")
    if got is not None:
        return got

    # 2) Soft start — no kill
    soft_start_server()
    got = _from_out(adb("devices", timeout=15), "after soft start")
    if got is not None:
        return got

    # 3) One hard recover only
    print("[adb] still no devices response — one hard recover")
    adb_recover()
    got = _from_out(adb("devices", timeout=15), "after hard recover")
    if got is not None:
        return got

    print("[adb] ADB still not responding after hard recover.")
    return []


def port_for(idx):
    return BASE_PORT + idx

def device_ports_paths():
    """IG repo first; Threads farm pool if that process is the live relay."""
    return [
        os.path.join(STATE_DIR, "device_ports.json"),
        os.path.join(os.path.dirname(ROOT), "threads-farm", "proxy_state", "device_ports.json"),
    ]


def load_device_ports():
    """Return (mapping, path) for the first non-empty device_ports.json."""
    for path in device_ports_paths():
        try:
            m = json.load(open(path, encoding="utf-8"))
        except (FileNotFoundError, ValueError, OSError):
            continue
        if isinstance(m, dict) and m:
            return m, path
    return {}, ""


def port_for_serial(serial):
    """Look up a phone's relay listener port from the live device_ports.json."""
    m, _path = load_device_ports()
    return m.get(serial) if serial else None

def verify_chain(port):
    """curl ipinfo.io from the PC THROUGH this local listener -> proves the exit IP
    before any phone is involved."""
    out = subprocess.run(["curl", "-s", "--max-time", "20",
                          "--socks5-hostname", "127.0.0.1:%d" % port,
                          "https://ipinfo.io/json"],
                         capture_output=True, text=True).stdout
    ip = re.search(r'"ip":\s*"([^"]+)"', out or "")
    cc = re.search(r'"country":\s*"([^"]+)"', out or "")
    return ("%s (%s)" % (ip.group(1), cc.group(1) if cc else "?")) if ip else "FAIL"


def _wire_reverse(serial, port):
    """adb reverse for one phone. Soft retry; hard recover at most once globally."""
    adb(
        "-s", serial, "reverse",
        "tcp:%d" % PHONE_LOCAL_PORT, "tcp:%d" % port,
        timeout=20,
    )
    check = adb("-s", serial, "reverse", "--list", timeout=15)
    if ("tcp:%d" % PHONE_LOCAL_PORT) in (check or ""):
        return True
    print("  [warn] reverse missing for %s — soft retry" % serial)
    soft_start_server()
    adb(
        "-s", serial, "reverse",
        "tcp:%d" % PHONE_LOCAL_PORT, "tcp:%d" % port,
        timeout=25,
    )
    check = adb("-s", serial, "reverse", "--list", timeout=15)
    if ("tcp:%d" % PHONE_LOCAL_PORT) in (check or ""):
        return True
    if adb_recover():
        adb(
            "-s", serial, "reverse",
            "tcp:%d" % PHONE_LOCAL_PORT, "tcp:%d" % port,
            timeout=25,
        )
        check = adb("-s", serial, "reverse", "--list", timeout=15)
        if ("tcp:%d" % PHONE_LOCAL_PORT) in (check or ""):
            return True
    print("  [warn] reverse still missing for %s — continuing (repair_adb.bat if many fail)"
          % serial)
    return False


def main():
    verify = "--verify" in sys.argv
    force_recover = "--recover" in sys.argv
    print("proxy_pool build=%s" % POOL_BUILD)
    print("PROVIDER=%s" % PROVIDER)
    if PROVIDER == "floppydata":
        cfg = _load_floppy()
        u = (cfg.get("username") or "")[:48]
        print("Floppydata host=%s port=%s user=%s… cfg=%s"
              % (cfg.get("host") or "?", cfg.get("port"), u,
                 cfg.get("_path") or "env-only"))
        if not cfg.get("username") or str(cfg.get("username")).startswith("PASTE_"):
            print("ERROR: paste FULL Floppydata username into floppydata_proxy.json")
            print("  Dashboard → Proxy strings → Copy all → fill username field")
            return
    if force_recover:
        print("[adb] --recover requested (explicit hard reset before start)")
        adb_recover(force=True)
    else:
        print("Listing phones via adb (quiet — no kill on start)…")
    try:
        devs = devices()
    except Exception as e:
        print("ERROR: adb devices failed: %s" % e)
        print("Optional: double-click repair_adb.bat, then start_proxy_pool.bat again.")
        print("No Admin needed for normal use.")
        return
    if not devs:
        print("No phones connected.")
        print("1) Unlock phones, check USB hub / cables")
        print("2) In a normal CMD:  adb devices   (expect 'device' rows)")
        print("3) If adb itself hangs: double-click repair_adb.bat  (one kill)")
        print("4) Then double-click start_proxy_pool.bat again")
        print("(Must see build=%s — if not, copy proxy_pool.py + bats from handover.)"
              % POOL_BUILD)
        return
    mapping = {}
    print("Starting PC-side proxy pool for %d phones...\n" % len(devs))
    for i, serial in enumerate(devs):
        port = port_for(i)
        mapping[serial] = port
        # default this port's session if not set yet
        if not os.path.exists(session_file(port)):
            set_session(port, "dev%04d" % port)
        threading.Thread(target=serve, args=(port,), daemon=True).start()
        _wire_reverse(serial, port)
        print("  %-22s  PC:127.0.0.1:%d  <-reverse-  phone:127.0.0.1:%d"
              % (serial, port, PHONE_LOCAL_PORT))
    # save the serial->port map so set_proxy_session/rotation can find each phone
    json.dump(mapping, open(os.path.join(STATE_DIR, "device_ports.json"), "w"))
    time.sleep(1.5)
    if verify:
        print("\nVerifying each chain from the PC (curl through the listener):")
        for serial, port in mapping.items():
            print("  %-22s  exit = %s" % (serial, verify_chain(port)))
    print("\nProxy pool RUNNING. Leave this window open.")
    print("Phone-side: NekoBox -> socks://127.0.0.1:%d  (no credentials, identical on every phone)" % PHONE_LOCAL_PORT)
    print("Rotate a phone's IP from code: proxy_pool.set_session(port, token)")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nproxy pool stopped.")

if __name__ == "__main__":
    main()
