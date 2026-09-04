@echo off
REM IG Console (port 3001). Proxy pool must already be running.
cd /d "%~dp0"
echo.
echo  IG CONSOLE
echo  1. start_proxy_pool.bat must already be open
echo  2. Leave THIS window open
echo  3. Browser: http://127.0.0.1:3001
echo  4. Run Control  -^>  Start posting
echo.
start "" "http://127.0.0.1:3001"
python -u run_ig_dashboard.py
pause
