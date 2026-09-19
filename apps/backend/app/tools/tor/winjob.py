"""Windows Job Object for processes Alex started. Close kills only this job."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
PROCESS_ALL_ACCESS = 0x1F0FFF
JobObjectExtendedLimitInformation = 9
JobObjectBasicProcessIdList = 3
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


class JOBOBJECT_BASIC_PROCESS_ID_LIST(ctypes.Structure):
    _fields_ = [
        ("NumberOfAssignedProcesses", wintypes.DWORD),
        ("NumberOfProcessIdsInList", wintypes.DWORD),
        ("ProcessIdList", ctypes.c_void_p * 256),
    ]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel32


class JobProcess:
    def __init__(self, argv, *, cwd=None, env=None):
        if sys.platform != "win32":
            raise RuntimeError("windows_job_required")
        self.kernel32 = _kernel32()
        self.job = self.kernel32.CreateJobObjectW(None, None)
        if not self.job:
            raise OSError("CreateJobObjectW failed")
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel32.SetInformationJobObject(
            self.job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.kernel32.CloseHandle(self.job)
            raise OSError("SetInformationJobObject failed")
        flags = CREATE_BREAKAWAY_FROM_JOB | CREATE_UNICODE_ENVIRONMENT
        self.child = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        handle = self.kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, self.child.pid)
        if not handle:
            self.close()
            raise OSError("OpenProcess failed")
        assigned = self.kernel32.AssignProcessToJobObject(self.job, handle)
        self.kernel32.CloseHandle(handle)
        if not assigned:
            self.close()
            raise OSError("AssignProcessToJobObject failed")

    @property
    def pid(self) -> int:
        return int(self.child.pid)

    def pids(self) -> list[int]:
        listing = JOBOBJECT_BASIC_PROCESS_ID_LIST()
        returned = wintypes.DWORD()
        ok = self.kernel32.QueryInformationJobObject(
            self.job,
            JobObjectBasicProcessIdList,
            ctypes.byref(listing),
            ctypes.sizeof(listing),
            ctypes.byref(returned),
        )
        if not ok:
            return [self.pid]
        count = min(int(listing.NumberOfProcessIdsInList), 256)
        found = []
        for index in range(count):
            value = listing.ProcessIdList[index]
            if value:
                found.append(int(value))
        return found or [self.pid]

    def poll(self):
        return self.child.poll()

    def close(self):
        job, self.job = getattr(self, "job", None), None
        if job:
            self.kernel32.CloseHandle(job)
        child = getattr(self, "child", None)
        if child and child.poll() is None:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
