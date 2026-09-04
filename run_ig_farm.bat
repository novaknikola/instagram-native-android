@echo off
REM ============================================================
REM  DEV / emergency only. NOT for the client.
REM  Tijana starts jobs from the dashboard:
REM    1. start_proxy_pool.bat
REM    2. run_ig_dashboard.bat
REM    3. browser http://127.0.0.1:3001  ->  Run Control  ->  Start posting
REM ============================================================
cd /d "%~dp0"

echo.
echo  STOP. This bat is not the client posting path.
echo  Use run_ig_dashboard.bat  then  Run Control  then  Start posting.
echo.
echo  Press Ctrl+C to cancel. Press any other key only if you are
echo  the developer and the Console cannot start the farm.
echo.
pause

python -u run_ig_farm.py --country us --per-device 1 --parallel
pause
