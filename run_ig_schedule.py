# -*- coding: utf-8 -*-
# run_ig_schedule.py — entrypoint for scheduled IG publishing.
#   set IG_SCHEDULE_SHEET=<spreadsheet_id>
#   python -u run_ig_schedule.py
#   python -u run_ig_schedule.py --dry
#   python -u run_ig_schedule.py --loop 120
from farm_root import ROOT
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Prefer farm root cwd when deployed
import os
FARM = ROOT
if os.path.isdir(FARM):
    os.chdir(FARM)
    if FARM not in sys.path:
        sys.path.insert(0, FARM)

import ig_scheduler

if __name__ == "__main__":
    ig_scheduler.main()
