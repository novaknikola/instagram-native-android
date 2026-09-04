@echo off
REM Scheduled IG publishing
REM Prefer: Console Schedule → paste Sheet ID → Save (ig_schedule_sheet.txt)
REM Or: set IG_SCHEDULE_SHEET=...  Or use ig_schedule.csv
cd /d "%~dp0"
if "%IG_SCHEDULE_SHEET%"=="" (
  if exist ig_schedule_sheet.txt (
    echo Using ig_schedule_sheet.txt
  ) else (
    echo TIP: set schedule in Console ^(Schedule page^) or set IG_SCHEDULE_SHEET=...
    echo Falling back to ig_schedule.csv if present.
  )
)
python -u run_ig_schedule.py %*
pause
