@echo off
REM ============================================================
REM  Proxy Relay - double-click to start BEFORE running the farm
REM  Must be running (this window open) any time run_farm is used.
REM ============================================================
cd /d "%~dp0"

echo.
echo  Starting the proxy relay (Floppydata via proxy_pool.py)
echo  Keep this window open the whole time the farm is running.
echo.
python proxy_pool.py
pause
