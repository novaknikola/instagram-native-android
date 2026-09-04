@echo off
REM ============================================================
REM  Proxy relay - HEADLESS launcher for the FarmProxyPool scheduled task.
REM  The task pointed at this file but it did not exist (Last Result: 1), so the
REM  relay only ever survived because someone had started it by hand - meaning a
REM  reboot would have left the whole farm with no proxy. Created 2026-08-06.
REM  NOTE: start_proxy_pool.bat ends with `pause`, so it can only run in a visible
REM  window and is unsuitable for the scheduler. This one logs and never blocks.
REM
REM  2026-08-14: interpreter pinned absolutely. Bare "python" is a PATH lookup, and
REM  the identical bug killed the watchman for three days. The relay is the farms
REM  lifeline - no relay means every account fails ALL_IPS_MASKED - so it must not
REM  depend on whose PATH happens to be in effect.
REM ============================================================
set PY=C:\Users\admin\AppData\Local\Programs\Python\Python311\python.exe
cd /d "%~dp0"
if not exist "%PY%" (
  echo %DATE% %TIME% FATAL: interpreter missing at %PY% >> proxy_pool.log
  exit /b 20
)
echo ---- proxy pool start %DATE% %TIME% ---- >> proxy_pool.log
"%PY%" -u proxy_pool.py >> proxy_pool.log 2>&1
