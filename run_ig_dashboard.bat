@echo off
REM IG Console — THIS is how Tijana starts posting (port 3001).
REM Do not use run_ig_farm.bat for client ops.
cd /d "%~dp0"

echo.
echo  ============================================
echo   IG CONSOLE  (client start)
echo  ============================================
echo   1. start_proxy_pool.bat must already be open
echo   2. Leave THIS window open
echo   3. Browser opens  http://127.0.0.1:3001
echo   4. Run Control  -^>  Start posting
echo  ============================================
echo.

start "" "http://127.0.0.1:3001"
python -u run_ig_dashboard.py
pause
