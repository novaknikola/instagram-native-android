# -*- coding: utf-8 -*-
# run_ig_dashboard.py - launch the IG Console (port 3001).
#   cd C:\threads-android
#   python -u run_ig_dashboard.py
#
# Password: dashboard_password.txt (same as Threads dash) or DASH_PASS env.
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ig_dashboard.app import main

if __name__ == "__main__":
    main()
