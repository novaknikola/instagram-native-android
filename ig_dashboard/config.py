# -*- coding: utf-8 -*-
"""IG Console paths and runtime config (Windows farm host)."""
import os
from pathlib import Path

# This repo root. Override with IG_FARM_BASE if the console is launched elsewhere.
_DEFAULT_BASE = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get("IG_FARM_BASE", str(_DEFAULT_BASE)))

ACCOUNTS_CSV = BASE / "Instagram_farm_accounts.csv"
RESULTS_CSV = BASE / "ig_batch_results.csv"
THREADS_RESULTS = BASE / "batch_results.csv"
SERVICE_ACCOUNT = BASE / "service_account.json"
PASSWORD_FILE = BASE / "dashboard_password.txt"
RUN_STATE = BASE / "ig_console_run.json"
RUN_LOG = BASE / "ig_console_run.log"  # legacy pointer / fallback
LOGS_DIR = Path(os.environ.get("IG_LOGS_DIR", str(BASE / "logs")))
ERROR_SHOTS_DIR = Path(
    os.environ.get(
        "IG_ERROR_SHOT_DIR",
        str(LOGS_DIR / "error_shots" / "loose"),
    )
)
RECORDINGS_DIR = Path(
    os.environ.get(
        "IG_RECORDINGS_DIR",
        str(BASE / "per device recordings"),
    )
)
STICKY_IP_JSON = BASE / "ig_sticky_ip.json"
PHONE_DRIVE_MAP = BASE / "ig_phone_drive_map.json"
PHONES_DRIVE_ROOT = BASE / "ig_phones_drive_folder.txt"
SHARED_DRIVE_FILE = BASE / "ig_shared_drive_folder.txt"
ACCOUNT_DRIVE_MAP = BASE / "ig_account_drive_map.json"  # optional overrides only
SCHEDULE_CSV = BASE / "ig_schedule.csv"
SCHEDULE_SHEET_FILE = BASE / "ig_schedule_sheet.txt"
# Prefer resolve via ig_scheduler.schedule_sheet_id() (env + file). Kept for back-compat.
SCHEDULE_SHEET = os.environ.get("IG_SCHEDULE_SHEET", "").strip()
CONTENT_LINES_JSON = BASE / "ig_content_lines.json"
ACCOUNT_STATUS_JSON = BASE / "ig_account_status.json"
WARMUP_PROFILES_TXT = BASE / "ig_warmup_profiles.txt"
PROXY_STATE_DIR = Path(
    os.environ.get("IG_PROXY_STATE", str(BASE / "proxy_state"))
)
PHONE_SHOTS_DIR = Path(
    os.environ.get("IG_PHONE_SHOTS", str(BASE / "phone_shots"))
)

MODELS = ("ig",)
DEFAULT_MODEL = "ig"

DASH_USER = os.environ.get("DASH_USER", "tijana")
DASH_PASS = os.environ.get("DASH_PASS")
if not DASH_PASS and PASSWORD_FILE.exists():
    DASH_PASS = PASSWORD_FILE.read_text(encoding="utf-8").strip()

HOST = os.environ.get("IG_CONSOLE_HOST", "0.0.0.0")
PORT = int(os.environ.get("IG_CONSOLE_PORT", "3001"))

# Fleet Screens: age (seconds) before a shot counts as stale
SCREEN_STALE_SECS = int(os.environ.get("IG_SCREEN_STALE_SECS", "900"))
SCREEN_FRESH_SECS = int(os.environ.get("IG_SCREEN_FRESH_SECS", "180"))
SCREEN_CAPTURE_TIMEOUT = float(os.environ.get("IG_SCREEN_CAPTURE_TIMEOUT", "18"))
SCREEN_THUMB_WIDTH = int(os.environ.get("IG_SCREEN_THUMB_WIDTH", "360"))

# Process match for active IG farm (not Threads run_farm).
RUN_PROCESS_PATTERN = r"run_ig_farm|run_ig_device|run_ig_schedule|ig_scheduler"
