@echo off
REM Troy phone 20 — full run: human-like, step-watch on state changes, no Grok per-tap delays
cd /d C:\farm\instagram-native

set IG_HUMAN=1
set IG_HUMAN_JITTER=4
set IG_WARMUP_MAX_SEC=90
set IG_WARMUP_SAVE_CHANCE=0
set IG_WARMUP_LIKE_CHANCE=0.08
set IG_WARMUP_FEED_ONLY=1
set IG_WARMUP_MIN_CLIPS=3
set IG_WARMUP_MIN_WATCH_FRAC=0.5

REM Screenshots on login/warmup/post/publish/fail — no Grok (timeouts stall the farm)
set IG_STEP_WATCH=1
set IG_STEP_WATCH_VISION=0
set IG_STEP_WATCH_ANALYZE=states
set IG_STEP_WATCH_TAPS=0
set IG_VISION=0

adb -s 988a98454b3949444a30 reverse tcp:1080 tcp:10802

python -u run_ig_device.py 988a98454b3949444a30 "%TEMP%\ig_plan_warmup_reel_troy.json" us us
