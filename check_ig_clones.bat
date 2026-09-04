@echo off
REM ============================================================
REM  Count IG clones: APKs on this PC + unique clones on each phone.
REM  Read-only. Does NOT generate, install, or start the farm.
REM  Copy to C:\threads-android and double-click (phones plugged in).
REM ============================================================
cd /d "%~dp0"
if exist "threads_farm_handover\check_ig_clones.py" goto :go
if exist "check_ig_clones.py" if exist "..\run_ig_farm.py" cd /d "%~dp0\.."
:go

if exist threads_farm_handover\check_ig_clones.py copy /Y threads_farm_handover\check_ig_clones.py . >nul
if exist threads_farm_handover\ig_pkg.py copy /Y threads_farm_handover\ig_pkg.py . >nul
if exist threads_farm_handover\ig_clone_gap.py copy /Y threads_farm_handover\ig_clone_gap.py . >nul

echo.
echo  Clone count — phones must be USB "device" (not unauthorized).
echo.
python -u check_ig_clones.py --target 20
if errorlevel 1 (
  echo.
  echo Failed. If "No phones": unlock, replug hub, or run repair_adb.bat then this again.
)
echo.
pause
