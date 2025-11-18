# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

# pylint: disable=broad-exception-caught

"""
Windows CommandRunner implementation using subprocess + Job Objects.

This module provides a small abstraction for spawning and managing a single
process on Windows in a way that ensures its entire process tree is killed
on interruption or teardown:

- spawn(command): creates a Job Object with the KILL_ON_JOB_CLOSE limit and
  launches the command in a new process group, then assigns the process to
  the Job. Returns a ManagedProcess handle.
- start(): no-op on Windows (process starts immediately on spawn).
- wait(): polls WaitForSingleObject with a short timeout so Python can process
  SIGINT (Converted to InterruptedError by our signal handler) between polls.
- kill(): terminates the Job (kills entire process tree) and closes the Job
  handle.

The Job handle is closed on both kill() and natural completion to prevent
handle leaks. The underlying process handle is owned by subprocess.Popen and
is not manually closed here.
"""

import ctypes
import subprocess
import shutil
import sys
from typing import List, Optional

if sys.platform != "win32":  # pragma: no cover - module is Windows-specific
    raise NotImplementedError("win32 CommandRunner is only available on Windows")

import ctypes.wintypes as wt  # pylint: disable=ungrouped-imports

from topdown_tool.common.abstractions import ManagedProcess
from topdown_tool.common import win32

# ──────────────────────────────────────────────────────────────────────────────
# ctypes.wintypes fallbacks for older Python releases (< 3.11)
# ──────────────────────────────────────────────────────────────────────────────
if not hasattr(wt, "SIZE_T"):
    wt.SIZE_T = ctypes.c_size_t  # type: ignore[attr-defined]
if not hasattr(wt, "ULONG_PTR"):
    wt.ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32  # type: ignore[attr-defined]
if not hasattr(wt, "ULONGLONG"):
    wt.ULONGLONG = ctypes.c_ulonglong  # type: ignore[attr-defined]

# ──────────────────────────────────────────────────────────────────────────────
# Win32 constants
# ──────────────────────────────────────────────────────────────────────────────
CREATE_NEW_PROCESS_GROUP = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

# ──────────────────────────────────────────────────────────────────────────────
# Structures required by SetInformationJobObject
# ──────────────────────────────────────────────────────────────────────────────


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):  # pylint: disable=invalid-name
    _fields_ = [
        ("PerProcessUserTimeLimit", wt.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wt.LARGE_INTEGER),
        ("LimitFlags", wt.DWORD),
        ("MinimumWorkingSetSize", wt.SIZE_T),
        ("MaximumWorkingSetSize", wt.SIZE_T),
        ("ActiveProcessLimit", wt.DWORD),
        ("Affinity", wt.ULONG_PTR),
        ("PriorityClass", wt.DWORD),
        ("SchedulingClass", wt.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):  # pylint: disable=invalid-name
    _fields_ = [
        ("ReadOperationCount", wt.ULONGLONG),
        ("WriteOperationCount", wt.ULONGLONG),
        ("OtherOperationCount", wt.ULONGLONG),
        ("ReadTransferCount", wt.ULONGLONG),
        ("WriteTransferCount", wt.ULONGLONG),
        ("OtherTransferCount", wt.ULONGLONG),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):  # pylint: disable=invalid-name
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", wt.SIZE_T),
        ("JobMemoryLimit", wt.SIZE_T),
        ("PeakProcessMemoryUsed", wt.SIZE_T),
        ("PeakJobMemoryUsed", wt.SIZE_T),
    ]


# Optionally, define function prototypes to make ctypes safer (not strictly required)
_k32 = win32.kernel32
try:
    _k32.CreateJobObjectW.restype = wt.HANDLE
    _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
    _k32.SetInformationJobObject.restype = wt.BOOL
    _k32.SetInformationJobObject.argtypes = [wt.HANDLE, wt.INT, ctypes.c_void_p, wt.DWORD]
    _k32.AssignProcessToJobObject.restype = wt.BOOL
    _k32.AssignProcessToJobObject.argtypes = [wt.HANDLE, wt.HANDLE]
    _k32.TerminateJobObject.restype = wt.BOOL
    _k32.TerminateJobObject.argtypes = [wt.HANDLE, wt.UINT]
    _k32.WaitForSingleObject.restype = wt.DWORD
    _k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
    _k32.CloseHandle.restype = wt.BOOL
    _k32.CloseHandle.argtypes = [wt.HANDLE]
except Exception:
    # If setting prototypes fails for any reason, ctypes will still work dynamically.
    pass


class _Win32Process(ManagedProcess):
    """
    ManagedProcess implementation for Windows based on subprocess + Job Object.
    """

    def __init__(self, proc: subprocess.Popen, job_handle: int) -> None:
        self._proc = proc
        self._job_handle: Optional[int] = int(job_handle)
        # Encapsulate the process handle as a HANDLE for WaitForSingleObject
        self._proc_handle = wt.HANDLE(self._proc._handle)
        self._finished = False

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    def start(self) -> None:
        # On Windows the process starts immediately; nothing to do.
        return None

    def wait(self) -> None:
        if self._finished:
            return
        # Poll with short timeouts to let Python run its SIGINT handler between polls.
        while True:
            try:
                res = _k32.WaitForSingleObject(self._proc_handle, 200)  # 200 ms
                if res == win32.WAIT_OBJECT_0:
                    break  # command finished naturally
                if res != win32.WAIT_TIMEOUT:
                    raise OSError("WaitForSingleObject failed")
            except InterruptedError:
                # Convert Ctrl-C into termination of the process tree.
                self.kill()
                raise
        # Reap process and close Job handle to avoid leaks
        try:
            self._proc.wait()
        finally:
            self._close_job_handle()
            self._finished = True

    def kill(self) -> None:
        if self._finished:
            return
        # Kill everything in the job (incl. grandchildren)
        try:
            if self._job_handle:
                _k32.TerminateJobObject(wt.HANDLE(self._job_handle), 1)
        finally:
            self._close_job_handle()
            # Ensure the process is reaped
            try:
                self._proc.wait(timeout=10)
            except Exception:
                # Best-effort; the OS should clean up eventually
                pass
            self._finished = True

    def _close_job_handle(self) -> None:
        if self._job_handle:
            try:
                _k32.CloseHandle(wt.HANDLE(self._job_handle))
            except Exception:
                pass
            self._job_handle = None

    def __del__(self) -> None:
        # Best-effort to avoid handle leaks if caller forgot to wait/kill.
        try:
            self._close_job_handle()
        except Exception:
            pass


class Win32CommandRunner:
    """
    Spawn and manage a Windows process using a Job Object with KILL_ON_JOB_CLOSE.

    The returned ManagedProcess ensures that killing it terminates the entire
    process tree and that resources are released on both normal completion and
    error paths.
    """

    def spawn(self, command: List[str]) -> ManagedProcess:
        """
        Launch the command in a new process group and assign it to a Job
        that will kill the entire process tree when closed.

        Raises:
            OSError: if the command is not found or cannot be executed, or if
            required Win32 calls fail.
        """
        if not command or not isinstance(command[0], str):
            raise OSError("Invalid command")
        exe = command[0]

        # Resolve executable like a shell would (PATH search)
        if shutil.which(exe) is None:
            raise OSError(
                f"Command {exe} cannot be executed. Please check that the file exists in PATH and you have the necessary rights to run it."
            )

        # 1) Create a Job Object
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError("CreateJobObject failed")

        # 2) Configure the Job to kill all processes when the Job handle is closed
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _k32.SetInformationJobObject(
            job,
            JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            # Ensure we close the job if configuration fails
            _k32.CloseHandle(job)
            raise OSError("SetInformationJobObject failed")

        # 3) Launch the target app in its own console group
        try:
            # pylint: disable=consider-using-with
            proc = subprocess.Popen(command, creationflags=CREATE_NEW_PROCESS_GROUP)  # type: ignore[arg-type]
        except Exception:
            _k32.CloseHandle(job)
            raise

        # 4) Put the process into the Job
        # pylint: disable=protected-access,no-member
        if not _k32.AssignProcessToJobObject(job, wt.HANDLE(proc._handle)):
            _k32.CloseHandle(job)
            # Terminate the process we couldn't assign
            try:
                proc.kill()
            except Exception:
                pass
            raise OSError("AssignProcessToJobObject failed")

        return _Win32Process(proc, int(job))


__all__ = ["Win32CommandRunner"]
