"""Free proof that the T4 suspend step can never leave a process frozen.

A child process appends a heartbeat line every 0.2 s. The script suspends it with a 3-second
watchdog, verifies the heartbeat really stopped, then does NOTHING and verifies the independent
watchdog resumed it. It also proves the script refuses a missing or unknown PID.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
SCRIPT = REPO / "artifacts" / "suspend-app.ps1"
PY = sys.executable


def run_ps(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *args],
        capture_output=True,
        timeout=120,
    )
    return result.returncode, (result.stdout + result.stderr).decode("utf-8", "replace").strip()


def size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def main() -> int:
    failures = 0
    temp = Path(tempfile.mkdtemp(prefix="canalla-watchdog-"))
    beat = temp / "beat.txt"
    beat_script = temp / "beat.ps1"
    beat_script.write_text(
        "param([string]$Path)\n"
        "while ($true) { Add-Content -LiteralPath $Path -Value 'x' -NoNewline; Start-Sleep -Milliseconds 200 }\n",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(beat_script),
            str(beat),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    wait_start = time.monotonic() + 10
    while time.monotonic() < wait_start and size(beat) == 0:
        time.sleep(0.2)
    try:
        time.sleep(1.5)
        before = size(beat)
        code, output = run_ps("-Suspend", "-ProcessId", str(child.pid), "-WatchdogSeconds", "10")
        suspended = code == 0
        print("[%s] suspend with watchdog — %s" % ("PASS" if suspended else "FAIL", output))
        failures += 0 if suspended else 1

        time.sleep(1.5)
        freezing = size(beat)
        quiet = freezing - before <= 2
        print("[%s] heartbeat stopped while suspended (%d -> %d)" % ("PASS" if quiet else "FAIL", before, freezing))
        failures += 0 if quiet else 1

        # Do not resume: the independent watchdog must. Give it 10 s plus a margin.
        deadline = time.monotonic() + 25
        resumed_size = freezing
        while time.monotonic() < deadline and resumed_size <= freezing + 2:
            time.sleep(0.3)
            resumed_size = size(beat)
        resumed = resumed_size > freezing + 2
        print("[%s] the independent watchdog resumed it without the harness (%d -> %d)" % (
            "PASS" if resumed else "FAIL", freezing, resumed_size,
        ))
        failures += 0 if resumed else 1

        code, output = run_ps("-Suspend", "-ProcessId", "0", "-WatchdogSeconds", "1")
        refused = code != 0
        print("[%s] a missing PID is refused — %s" % ("PASS" if refused else "FAIL", output))
        failures += 0 if refused else 1

        code, output = run_ps("-Resume", "-ProcessId", "999999")
        refused = code != 0
        print("[%s] an unknown PID is refused — %s" % ("PASS" if refused else "FAIL", output))
        failures += 0 if refused else 1
    finally:
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
        import shutil

        shutil.rmtree(temp, ignore_errors=True)

    print("SELF-TEST", "PASS" if not failures else "FAIL")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
