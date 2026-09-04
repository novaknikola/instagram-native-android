# -*- coding: utf-8 -*-
"""Full-run screen recordings, one file per phone, stored on the PC.

Ported from Threads farm. Android `screenrecord` hard-caps at 180s. This records
the WHOLE IG farm run: scrcpy if installed (one continuous mp4), otherwise
15s ADB segments pulled to the PC and stitched with ffmpeg.

Layout (local, never git):

    per device recordings/
      device_numbers.txt      stable 01 <-> serial (shared with Threads when seeded)
      2026-08-26_184500/      one folder per farm run
        map.txt
        01_<serial>.mp4
        02_<serial>.mp4

Default ON in run_ig_farm / run_ig_device. Escape: --no-record.
Phone temp path is IG-specific so Threads + IG never clobber the same file.
"""
from __future__ import print_function

import os
import json
import subprocess
import sys
import time
import shutil
from datetime import datetime

from farm_root import ROOT

REC_ROOT = os.path.join(ROOT, "per device recordings")
INDEX_FILE = os.path.join(REC_ROOT, "device_numbers.txt")
# Distinct from Threads (/sdcard/farm_run_rec.mp4) so both farms can record.
PHONE_TMP = "/sdcard/ig_farm_run_rec.mp4"
BITRATE = "2500000"
# Short complete clips. Interrupting Android screenrecord (pkill) writes ftyp+mdat
# with NO moov atom — Windows/VLC/Movies cannot play those files. Let each clip
# finish its time-limit so the phone writes a real mp4, then pull.
SEG_SEC = "15"


def _no_window_flags():
    """Windows: never allocate a console for child processes.

    Without CREATE_NO_WINDOW, every adb/python spawn flashes a CMD window.
    Do NOT add CREATE_NEW_PROCESS_GROUP / CREATE_NEW_CONSOLE here — those
    were opening ~one console per phone/recorder and nearly wedged the PC.
    """
    if sys.platform != "win32":
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _adb(serial, *args, timeout=60):
    try:
        return subprocess.run(
            ["adb", "-s", serial] + list(args),
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_no_window_flags(),
        )
    except Exception as e:
        print("[rec] adb %s failed: %s" % (serial, e))
        return None


def _which(name):
    found = shutil.which(name) or shutil.which(name + ".exe")
    if found:
        return found
    extra = [
        os.path.join(ROOT, "tools", "ffmpeg.exe"),
        os.path.join(ROOT, "tools", "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(ROOT, "tools", "ffmpeg", "ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
        os.path.expandvars(r"%ProgramFiles%\ffmpeg\bin\ffmpeg.exe"),
    ]
    want = name.lower().replace(".exe", "")
    for path in extra:
        if os.path.isfile(path) and want in os.path.basename(path).lower():
            return path
    return None


def load_numbers():
    """serial -> int, stable across runs."""
    out = {}
    if not os.path.isfile(INDEX_FILE):
        return out
    for line in open(INDEX_FILE, encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            out[parts[1]] = int(parts[0])
    return out


def save_numbers(mapping):
    os.makedirs(REC_ROOT, exist_ok=True)
    rows = sorted(mapping.items(), key=lambda kv: kv[1])
    with open(INDEX_FILE, "w", encoding="utf-8") as fh:
        fh.write("# Stable device numbers. Serial stays on the same number forever.\n")
        fh.write("# number    serial\n")
        for serial, num in rows:
            fh.write("%02d    %s\n" % (num, serial))


def numbers_for(serials):
    mapping = load_numbers()
    nxt = (max(mapping.values()) + 1) if mapping else 1
    for s in serials:
        if s not in mapping:
            mapping[s] = nxt
            nxt += 1
    save_numbers(mapping)
    return mapping


def _write_map(run_dir, serials, mapping, files):
    path = os.path.join(run_dir, "map.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("run folder: %s\n" % run_dir)
        fh.write("number  serial                          file\n")
        for s in serials:
            n = mapping[s]
            fh.write("%02d      %-32s %s\n" % (n, s, os.path.basename(files.get(s, ""))))
    return path


def _has_moov(path):
    """Android puts the moov atom at the END. Head-only checks miss it."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            head = fh.read(min(size, 4096))
            fh.seek(max(0, size - 1024 * 1024))
            tail = fh.read()
        return b"moov" in head or b"moov" in tail
    except Exception:
        return False


def _playable_parts(parts):
    good = []
    for p in parts:
        if os.path.isfile(p) and os.path.getsize(p) > 64 and _has_moov(p):
            good.append(p)
        elif os.path.isfile(p):
            print("[rec] skip unplayable (no moov): %s" % p)
    return good


def _ffmpeg_concat(parts, dest):
    ffmpeg = _which("ffmpeg")
    existing = _playable_parts(parts)
    if not existing:
        return False
    if len(existing) == 1:
        shutil.copy2(existing[0], dest)
        return _has_moov(dest)
    if not ffmpeg:
        return False
    lst = dest + ".concat.txt"
    with open(lst, "w", encoding="utf-8") as fh:
        for p in existing:
            fh.write("file '%s'\n" % p.replace("\\", "/"))
    r = subprocess.run(
        [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dest],
        capture_output=True,
        text=True,
        creationflags=_no_window_flags(),
    )
    try:
        os.remove(lst)
    except Exception:
        pass
    return r.returncode == 0 and os.path.isfile(dest)


def _pid_alive(pid):
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _stop_screenrecord(serial):
    for cmd in (
        ["adb", "-s", serial, "shell", "pkill", "-2", "screenrecord"],
        ["adb", "-s", serial, "shell", "killall", "-2", "screenrecord"],
    ):
        try:
            subprocess.run(
                cmd,
                capture_output=True,
                timeout=8,
                creationflags=_no_window_flags(),
            )
        except Exception:
            pass


def _worker_scrcpy(serial, dest, stop_flag, parent_pid=None):
    exe = _which("scrcpy")
    if not exe:
        return False
    cmd = [
        exe,
        "-s",
        serial,
        "--record",
        dest,
        "--record-format=mp4",
        "--video-bit-rate=2M",
        "--max-size=800",
        "--no-audio",
    ]
    for extra in (["--no-window", "--no-playback"], ["--no-display"], ["-N"]):
        trial = cmd + extra
        try:
            p = subprocess.Popen(
                trial,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                creationflags=_no_window_flags(),
            )
        except Exception:
            continue
        time.sleep(1.5)
        if p.poll() is not None:
            continue
        print("[rec] %s scrcpy -> %s" % (serial, dest))
        while p.poll() is None:
            if os.path.isfile(stop_flag) or (parent_pid and not _pid_alive(parent_pid)):
                p.terminate()
                try:
                    p.wait(timeout=12)
                except Exception:
                    p.kill()
                return os.path.isfile(dest)
            time.sleep(0.4)
        return os.path.isfile(dest)
    return False


def _worker_adb_segments(serial, dest, stop_flag, parent_pid=None):
    parts_dir = dest + ".parts"
    os.makedirs(parts_dir, exist_ok=True)
    parts = []
    n = 0
    print("[rec] %s adb screenrecord loop -> %s" % (serial, dest))
    while not os.path.isfile(stop_flag) and not (parent_pid and not _pid_alive(parent_pid)):
        n += 1
        local = os.path.join(parts_dir, "seg_%03d.mp4" % n)
        _adb(serial, "shell", "rm", "-f", PHONE_TMP)
        p = subprocess.Popen(
            [
                "adb",
                "-s",
                serial,
                "shell",
                "screenrecord",
                "--time-limit",
                SEG_SEC,
                "--bit-rate",
                BITRATE,
                PHONE_TMP,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_no_window_flags(),
        )
        # Do NOT SIGINT screenrecord — that is what made every mp4 unplayable.
        # On STOP, let this clip finish (max SEG_SEC), then pull a complete file.
        limit = int(SEG_SEC) + 8
        try:
            p.wait(timeout=limit)
        except Exception:
            _stop_screenrecord(serial)
            try:
                p.wait(timeout=8)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        time.sleep(1.2)
        pull = _adb(serial, "pull", PHONE_TMP, local, timeout=90)
        if pull is not None and os.path.isfile(local) and os.path.getsize(local) > 64:
            parts.append(local)
        else:
            err = ""
            if pull is not None:
                err = ((pull.stderr or pull.stdout or "")[:160]).strip()
            print("[rec] %s pull miss seg_%03d %s" % (serial, n, err))
        _adb(serial, "shell", "rm", "-f", PHONE_TMP)
        if os.path.isfile(stop_flag) or (parent_pid and not _pid_alive(parent_pid)):
            break
    if not _ffmpeg_concat(parts, dest):
        good = _playable_parts(parts)
        if len(good) == 1:
            shutil.copy2(good[0], dest)
        else:
            note = dest + ".READ_ME.txt"
            with open(note, "w", encoding="utf-8") as fh:
                fh.write(
                    "ffmpeg not found or stitch failed. Play the .parts folder in order,\n"
                    "or install ffmpeg and run:\n"
                    "  ffmpeg -f concat -safe 0 -i list.txt -c copy \"%s\"\n" % dest
                )
            print("[rec] %s left %d segments in %s (install ffmpeg to stitch)"
                  % (serial, len(parts), parts_dir))
            return False
    print("[rec] %s saved %s" % (serial, dest))
    return True


def worker_main(serial, dest, stop_flag, parent_pid=None):
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    if _worker_scrcpy(serial, dest, stop_flag, parent_pid):
        return 0
    _worker_adb_segments(serial, dest, stop_flag, parent_pid)
    return 0


def supervisor_main(manifest_path, stop_flag, parent_pid=None):
    """One process, one thread per phone — avoids 18x python.exe spawn storm."""
    import threading

    try:
        rows = json.load(open(manifest_path, encoding="utf-8"))
    except Exception as e:
        print("[rec] supervisor bad manifest: %s" % e)
        return 2
    threads = []

    def _one(row):
        try:
            worker_main(row["serial"], row["dest"], stop_flag, parent_pid)
        except Exception as e:
            print("[rec] %s worker crashed: %s" % (row.get("serial"), e))

    for i, row in enumerate(rows):
        t = threading.Thread(
            target=_one, args=(row,), name="rec-%s" % row.get("serial", i)
        )
        t.daemon = True
        t.start()
        threads.append(t)
        print(
            "[rec] thread %02d = %s -> %s"
            % (int(row.get("num") or i + 1), row.get("serial"), row.get("dest")),
            flush=True,
        )
        time.sleep(0.6)
    for t in threads:
        t.join()
    return 0


def spawn_supervisor(run_dir, parent_pid=0, note=""):
    """Start (or restart) the recorder supervisor in an existing run folder.

    parent_pid 0 = do not die with run_farm. Daemon + STOP file own lifetime.
    """
    man_path = os.path.join(run_dir, "manifest.json")
    stop_flag = os.path.join(run_dir, "STOP")
    if not os.path.isfile(man_path):
        raise OSError("missing recorder manifest: %s" % man_path)
    if os.path.isfile(stop_flag):
        try:
            os.remove(stop_flag)
        except OSError:
            pass
    rec_log = open(
        os.path.join(run_dir, "recorder.log"),
        "a",
        encoding="utf-8",
        errors="replace",
    )
    if note:
        rec_log.write("\n--- %s ---\n" % note)
        rec_log.flush()
    extra = _no_window_flags()
    if sys.platform == "win32":
        try:
            import win_proc
            extra = win_proc.creationflags(
                win_proc.DETACHED_PROCESS | win_proc.CREATE_BREAKAWAY_FROM_JOB
            )
        except Exception:
            # IG farm may not ship win_proc — CREATE_NO_WINDOW is enough for CLI.
            extra = _no_window_flags()
    parent = "0"
    try:
        if parent_pid and int(parent_pid) > 0:
            parent = str(int(parent_pid))
    except (TypeError, ValueError):
        parent = "0"
    p = subprocess.Popen(
        [
            sys.executable,
            "-u",
            os.path.abspath(__file__),
            "supervisor",
            man_path,
            stop_flag,
            parent,
        ],
        cwd=ROOT,
        creationflags=extra,
        stdin=subprocess.DEVNULL,
        stdout=rec_log,
        stderr=subprocess.STDOUT,
    )
    print("[rec] supervisor pid=%s dir=%s parent=%s" % (p.pid, run_dir, parent))
    return p


class RunRecorder(object):
    def __init__(self, serials, parent_pid=None):
        self.serials = list(serials)
        self.mapping = numbers_for(self.serials)
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        self.run_dir = os.path.join(REC_ROOT, stamp)
        os.makedirs(self.run_dir, exist_ok=True)
        self.stop_flag = os.path.join(self.run_dir, "STOP")
        self.procs = []
        self.files = {}
        manifest = []
        for s in self.serials:
            n = self.mapping[s]
            safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in s)
            dest = os.path.join(self.run_dir, "%02d_%s.mp4" % (n, safe))
            self.files[s] = dest
            manifest.append({"serial": s, "dest": dest, "num": n})
        man_path = os.path.join(self.run_dir, "manifest.json")
        with open(man_path, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        _write_map(self.run_dir, self.serials, self.mapping, self.files)
        p = spawn_supervisor(
            self.run_dir,
            parent_pid=0,
            note="start recordings %s parent=0" % datetime.now().isoformat(timespec="seconds"),
        )
        self.procs.append(p)
        for row in manifest:
            print(
                "[rec] device %02d = %s  ->  %s"
                % (row["num"], row["serial"], row["dest"]),
                flush=True,
            )
        print("[rec] index: %s" % INDEX_FILE)
        print("[rec] this run: %s" % self.run_dir)

    def stop(self):
        try:
            open(self.stop_flag, "w").close()
        except Exception:
            pass
        # Do not pkill screenrecord here — workers finish the current 15s clip
        # so the pulled mp4 has a moov atom and actually plays.
        deadline = time.time() + 90
        for p in self.procs:
            left = max(1, deadline - time.time())
            try:
                p.wait(timeout=left)
            except Exception:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except Exception:
                    p.kill()
        print("[rec] recordings stopped. Watch: %s" % self.run_dir)


def start(serials, enabled=True, parent_pid=None):
    if not enabled or not serials:
        return None
    try:
        return RunRecorder(serials, parent_pid=parent_pid)
    except Exception as e:
        print("[rec] could not start recordings: %s" % e)
        return None


def stop(rec):
    if rec is None:
        return
    try:
        rec.stop()
    except Exception as e:
        print("[rec] stop failed: %s" % e)


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "supervisor":
        parent = int(sys.argv[4]) if len(sys.argv) >= 5 and sys.argv[4].isdigit() else None
        sys.exit(supervisor_main(sys.argv[2], sys.argv[3], parent))
    if len(sys.argv) >= 5 and sys.argv[1] == "worker":
        parent = int(sys.argv[5]) if len(sys.argv) >= 6 and sys.argv[5].isdigit() else None
        sys.exit(worker_main(sys.argv[2], sys.argv[3], sys.argv[4], parent))
    print(
        "usage: python device_record.py supervisor <manifest.json> <stopflag> [parent_pid]\n"
        "   or: python device_record.py worker <serial> <out.mp4> <stopflag> [parent_pid]"
    )
    sys.exit(2)

