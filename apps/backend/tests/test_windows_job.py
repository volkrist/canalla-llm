"""Native Windows Job Object proof. Playwright mocks are not this test."""

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects only")

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259
JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [("unused", ctypes.c_uint64 * 6)]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _running(kernel32, pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
    kernel32.CloseHandle(handle)
    return code.value == STILL_ACTIVE


def test_job_object_kills_parent_and_child_not_unrelated(tmp_path):
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

    go = tmp_path / "go"
    child_pid_file = tmp_path / "child.pid"
    flags = CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    parent_source = (
        "import subprocess, sys, time\n"
        f"from pathlib import Path\n"
        f"go = Path(r'{go}')\n"
        f"marker = Path(r'{child_pid_file}')\n"
        "while not go.exists():\n"
        "    time.sleep(0.05)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "marker.write_text(str(child.pid), encoding='ascii')\n"
        "time.sleep(30)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_source],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    job = kernel32.CreateJobObjectW(None, None)
    assert job
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    assert kernel32.SetInformationJobObject(
        job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
    )
    handle = kernel32.OpenProcess(0x1F0FFF, False, parent.pid)
    assert handle
    assigned = kernel32.AssignProcessToJobObject(job, handle)
    kernel32.CloseHandle(handle)
    assert assigned
    go.write_text("1", encoding="ascii")
    deadline = time.time() + 8
    child_pid = None
    while time.time() < deadline:
        if child_pid_file.exists():
            text = child_pid_file.read_text(encoding="ascii").strip()
            if text.isdigit():
                child_pid = int(text)
                break
        time.sleep(0.05)
    assert child_pid is not None
    assert _running(kernel32, child_pid)
    kernel32.CloseHandle(job)
    stop_deadline = time.time() + 5
    while time.time() < stop_deadline and (_running(kernel32, parent.pid) or _running(kernel32, child_pid)):
        time.sleep(0.05)
    try:
        assert not _running(kernel32, parent.pid)
        assert not _running(kernel32, child_pid)
        assert _running(kernel32, unrelated.pid)
    finally:
        for process in (parent, unrelated):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
