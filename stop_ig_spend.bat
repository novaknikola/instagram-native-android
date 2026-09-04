@echo off
REM Stop spend: farm python + IG clones + NekoBox. Optionally kill proxy_pool.
cd /d "%~dp0"
echo Stopping run_ig_farm / run_ig_device / device_record ...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'run_ig_farm|run_ig_device|device_record.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Force-stop IG clones + NekoBox on all adb devices ...
python -c "import subprocess, ig_pkg, ig_tunnel; d=[ln.split()[0] for ln in subprocess.run(['adb','devices'],capture_output=True,text=True).stdout.splitlines()[1:] if '\tdevice' in ln]; print('phones',len(d)); ig_pkg.quiet_idle_ig(keep_serials=None); ig_tunnel.disable_many(d, label='spend-stop')"

if /I "%1"=="--proxy" (
  echo Stopping proxy_pool.py ...
  powershell -NoProfile -Command ^
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'proxy_pool' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
)
echo Done. Floppy is idle if proxy_pool is down and tun0 is down.
pause
