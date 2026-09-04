@echo off
REM ============================================================
REM  Explicit ADB repair — use ONLY when adb is hung / times out.
REM  Normal day: use start_proxy_pool.bat (no kill).
REM  No Admin required for a normal user-owned adb.exe.
REM ============================================================
cd /d "%~dp0"

echo.
echo  Repair ADB — one force-kill, then list phones.
echo  (Not needed every day — only when adb hangs.)
echo.

taskkill /F /IM adb.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo Starting adb server…
adb start-server
echo.
echo adb devices:
adb devices
echo.
echo If you see phones as "device", double-click start_proxy_pool.bat
echo If still empty: unlock phones, replug USB hub, run this again.
echo.
pause
