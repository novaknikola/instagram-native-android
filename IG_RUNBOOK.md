# Instagram native farm — operator runbook

**Host:** `C:\farm\instagram-native`  
**Goal:** 1 unique Nomix IG clone per phone. One account per phone. Sticky Floppy IP per clone. Drive media per phone.  
**Do not** use Threads `farm_accounts.csv`. IG creds live only in `Instagram_farm_accounts.csv` (gitignored).

Threads farm (`C:\farm\threads-farm`) is the **spine** we copy: `start_proxy_pool.bat` left open, NekoBox = `127.0.0.1:1080` via `adb reverse`, tun0 only while a job runs (`device_quiet.py` / our `ig_tunnel.py`), results CSV, recordings under `per device recordings\`.

---

## Architecture (same as Threads, Floppy not Geonode)

```
Phone NekoBox  →  socks://127.0.0.1:1080
     adb reverse tcp:1080 tcp:108NN
PC proxy_pool.py  →  127.0.0.1:10801–10820  →  Floppydata sticky session
```

- Listener map: `proxy_state/device_ports.json` (or threads copy if native file empty).
- Creds: `floppydata_proxy.json` (gitignored). Provider default in `proxy_pool.py` is **floppydata**.
- Any exit country is accepted. `PROXY_DEAD` = no SOCKS exit IP. That is **not** Instagram captcha.
- `ALL_IPS_MASKED` was a Threads leftover (IP-lookup false dead / captcha renamed). IG now keeps `CAPTCHA` / `CONTACT_VERIFY`. Old CSV rows with `ALL_IPS_MASKED` are retryable (removed from dead-ledger).

NekoBox package: `moe.nb4a`. Connect:  
`am start -n moe.nb4a/io.nekohasekai.sagernet.ui.QuickEnableShortcut`  
**Do not** re-kick while `tun0` already has `inet` (Threads `TUNNEL_FLAP.md` — flaps the tunnel).

---

## Files

| File | Role |
|---|---|
| `Instagram_farm_accounts.csv` | `username,password,tfa_secret,model` — model=`ig`. Spare row OK. |
| `ig_batch_results.csv` | One row per attempt: login, post, media, format. |
| `ig_sticky_ip.json` | serial\|pkg → session token + last exit. |
| `ig_account_clone.json` | username → Nomix package (not Threads `barcel*`). |
| `start_proxy_pool.bat` | `python proxy_pool.py` — **leave window open**. |
| `stop_ig_spend.bat` | Kill farm python + force-stop IG + NekoBox. `stop_ig_spend.bat --proxy` also kills the pool. |
| `run_ig_farm.py` | Fleet planner + `run_ig_device.py` children. |
| `ig_loop.py` | Login / warmup / create / publish / verify. |
| `ig_tunnel.py` | tun0 on/off (Threads `device_quiet.enable_vpn`). |
| `repair_adb.bat` | Only if `adb devices` hangs. Normal start does **not** kill ADB. |

Recordings: `per device recordings\<YYYY-MM-DD_HHMMSS>\01_<serial>.mp4`  
Step screenshots: `logs\step_watch\`  
Fail shots: `logs\error_shots\loose\`

---

## Start / stop (spend)

**Start (order matters — Threads INSTRUCTIONS.md):**

1. `adb devices` — all phones `device`. If hung: `repair_adb.bat` once, then pool.
2. Double-click `start_proxy_pool.bat`, leave it open. Prove: `python proxy_pool.py --verify` **starts another pool** — instead curl SOCKS `127.0.0.1:10801` ipinfo, or import `verify_chain`.
3. Farm asserts `adb reverse` and `ig_tunnel.enable_many` (tun0). Do not also run a second `proxy_pool.py` (two listeners on 10801 steal connections).
4. Run farm (below). Grok/vision: **off** for fleet publish (`IG_VISION=0`, `IG_STEP_WATCH_VISION=0`). Screenshots + recordings **on**.

**Stop:**

- `stop_ig_spend.bat --proxy` when idle overnight (Floppy).  
- Killing python mid-`uiautomator dump` can **hang ADB**. Prefer the bat, not Task Manager on random PIDs. If USB dies: `repair_adb.bat`.

---

## Login vs warmup+post

```bat
REM Login only (no Drive, no Create). Session kept; IG force-stopped after.
python -u run_ig_farm.py --login-only --stagger 4 --max-inflight 2

REM Warmup (feed-only Reels watch) + reel post. Restrict devices if needed.
set IG_VISION=0
set IG_STEP_WATCH=1
set IG_STEP_WATCH_VISION=0
set IG_WARMUP_FEED_ONLY=1
set IG_WARMUP_MAX_SEC=35
python -u run_ig_farm.py --warmup --format reel --stagger 5 --max-inflight 1 --devices SERIAL
```

Flags:

- `--allow-unproven` — include accounts Threads never logged in (needed for new IG CSV if proven pool is empty; farm **falls back** to unproven when proven=0).
- `--allow-cold` — include phones with historical `LOGIN_META_ERROR` (phone `988a5745584f554c4230` luciano).
- `--no-record` — no screen recordings.
- Ledger skips: `ACCOUNT_SUSPENDED` / `CHALLENGE` / `LOGIN_META_ERROR` / … **not** captcha. `LOGGED_IN`+`POST_DONE` skipped (already posted).

`--login-only` skips warmup and Create. Sticky session reused; `_already_logged_in` skips cold login if dump shows that username.

**Passkey:** Google overlay on launch (`PASSKEY_PROMPT`) can look like logged-out. Phone 20 (`988a98454b3949444a30`, clone `andrqge`, ex-troy) is the usual hit. Farm Backs out of GMS; if it still cold-logins, that is Passkey, not a new account.

**Cold login captcha:** image puzzle = Meta distrust of that IP/account, not a dead Floppy chain. Retry **1–2 phones**, not 6 parallel.

---

## First reel publish (the 2026-09-02 bug)

Fresh profiles show **0 posts** + “Create your first reel” until publish **confirms**.

What worked (Chasityghfu255): caption **Share** → sheet **About Reels** → **NUX Share** → profile has the reel (`POST_LIVE`).

What failed (Daliatydfh256, Nylahuttdff255): same composer `share_button @ 1068,2762` **eight times**, NUX never confirmed, verify on empty grid → `POST_BLOCKED` (name is “not live”, not a ban).

Nylah prove 2026-09-02: screen was **New reel** + blue **Next**; ADB Keyboard `{ON}` sat on the footer. Bot tapped `share_button` at IME coords, not Next. Later fail shot: Gboard still up, tap 2644 was **Enter**, Share not on screen.

**Caption/composer rule (2026-09-03):** no UI section longer than **60s**. Caption: type human-like, tap OK, tap Share, leave. No 24s share-wait, no 20s×4 dumps on a playing preview, no Grok on every publish round. Log line: `[budget] composer start max=60s` / `STOP after Ns`.

`ig_loop.publish_from_composer` now: after first Share, **poll ~13s** for About Reels / clips_nux / first-reel sheet (`_poll_reel_nux_after_share`), tap **that** Share, stop after 3 failed composer rounds. Do not Back off the reel editor (that dumped troy to EDIT).

Grok on 3 phones at once timed out (~14s) so taps ran blind. Fleet publish: vision off.

---

## Warmup

Port of iOS `novaknikola/instagram-reels-ios` classic `modules/warmup.py` (+ policy 3–7 min pre-post):

1. **Opener dice** — Reels 40% / home feed ~25% / notifications ~17% / 2–3 stories ~18%, then Reels.
2. **Beat scroll** — skip / glance / watch / linger (like mostly on linger; ~10% peek comments on watch/linger; rare rewatch).
3. Optional **seed profile** from `warmup_profiles` (short budget).
4. **Own Profile ≤5s** → Create. Session **stays** (no kill/logout — unlike iOS hard-exit).

Env: `IG_WARMUP_MINUTES` (fixed), or `IG_WARMUP_MAX_SEC` (cap). Default roll **3–7 min**. `WARMUP_PARTIAL` → **no post**. Do not dump while a Reel is playing (short dump only for like/comment).

Chinese `script-Instagram.html` (911 投屏) is **warmup-only** locators. Do not switch the farm to 911.

---

## Drive

Phone folder: `Reels/`, `Posts/`, `Stories/`. Map: `ig_phone_drive_map.json` or `ig_phones_drive_folder.txt`. Claim/release in `drive_content_ig_account.py`. Failed post **releases** the file (retry allowed). `POST_DONE` marks used.

---

## Fleet snapshot after 2026-09-02 (CSV)

**LOGGED_IN + POST_DONE:** Chasityghfu255, Yasmingufh255, Kiannagufdxf255.  
**LOGGED_IN, first reel not live:** Daliatydfh256, Nylahuttdff255 (`POST_BLOCKED` verify empty). Jazleneguyu258, Preciousyug255 (`POST_TIMEOUT`). Others logged in but run killed mid-post.  
**Login captcha (retry OK):** Toritufhd566, Norafufjg455, Corarhc784, Aryannahjgj255, Alenadvjc875, Aracelifhch754. Ellenfiv558 `UNKNOWN_STUCK`.  
**Not attempted:** phone `988a5745584f554c4230` META denylist; Britneygov853, imanidur95 no free unique clone.

Prove NUX fix on **one** logged-in empty profile (Nylah `988a9b39585859325730` or Dalia on phone 20) before inflight>1.

---

## Dashboard

`run_ig_dashboard.bat` → http://127.0.0.1:3001 — optional. Console `python -u run_ig_farm.py ...` is valid for ops.

---

## Ground truth

Logs lie if the UI dump was idle-blocked or Grok timed out. **Phone recording** + profile screenshot (0 posts vs reel tile) is the publish verdict.
