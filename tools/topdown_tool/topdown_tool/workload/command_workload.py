# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm LimitedLimited

import ctypes
import os
import signal
import sys
import shutil
import subprocess
from types import TracebackType
from typing import List, Optional, Set, Type

if sys.platform == "win32":
    import ctypes.wintypes as wt  # pylint: disable=ungrouped-imports

    # ────────────────────────────────────────────────────────
    # Older Python releases (< 3.11) don’t provide a few
    # aliases in ctypes.wintypes.  Define them if absent so
    # the struct declarations below compile everywhere.
    # ────────────────────────────────────────────────────────
    if not hasattr(wt, "SIZE_T"):
        wt.SIZE_T = ctypes.c_size_t

    if not hasattr(wt, "ULONG_PTR"):
        wt.ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

    if not hasattr(wt, "ULONGLONG"):
        wt.ULONGLONG = ctypes.c_ulonglong

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


from topdown_tool.workload.workload import Workload
from topdown_tool.common import win32


class CommandWorkload(Workload):
    """
    CommandWorkload is a workload implementation that executes a command by forking a new process.

    The process is initially stopped using SIGSTOP after forking. When the start method
    is called, the process is resumed with SIGCONT. The command execution concludes either when
    the forked process terminates or if it is interrupted (e.g., via Ctrl + C).
    """

    def __init__(self, command: List[str]):
        # Setup signal handlers
        super().__init__()

        self.finished = False
        self.executable = command[0].split("/")[-1]
        if sys.platform == "win32":
            self.executable = self.executable.split("\\")[-1]

        self.pid: int = -1

        if shutil.which(command[0]) is None:
            self.finished = True
            raise OSError(
                f"Command {command[0]} cannot be executed. Please check that the file exists in PATH and you have the necessary rights to run it."
            )
        # Prepare command for running
        if sys.platform == "linux":
            self.pid = os.fork()
            if self.pid == 0:
                os.kill(os.getpid(), signal.SIGSTOP)
                os.execvp(command[0], command)
            os.waitpid(self.pid, os.WUNTRACED)
        elif sys.platform == "win32":
            # ────────── Win32 (GUI-safe) implementation ───────────
            kernel32 = win32.kernel32

            # 1. create a Job Object that will track the whole process tree
            self.job = kernel32.CreateJobObjectW(None, None)
            if not self.job:
                raise OSError("CreateJobObject failed")

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000  # pylint: disable=invalid-name
            JobObjectExtendedLimitInformation = 9  # pylint: disable=invalid-name

            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                self.job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                raise OSError("SetInformationJobObject failed")

            # 2. launch the target app in its own console
            CREATE_NEW_PROCESS_GROUP = 0x00000200  # pylint: disable=invalid-name

            # pylint: disable=consider-using-with
            self._proc = subprocess.Popen(
                command, creationflags=CREATE_NEW_PROCESS_GROUP
            )  # pylint: disable=consider-using-with

            # 3. put it into the job (the Job will kill every descendent
            #    once we close it from kill())
            if not kernel32.AssignProcessToJobObject(self.job, wt.HANDLE(self._proc._handle)):
                raise OSError("AssignProcessToJobObject failed")

            # Expose the usual fields
            self.pid = self._proc.pid
            self.proc_handle = wt.HANDLE(self._proc._handle)  # wait() uses this
            self.job_handle = self.job  # kill() uses this
        else:
            raise NotImplementedError(f"CommandWorkload is not supported on {sys.platform}")
        self.pids = {self.pid}

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.kill()

    def __del__(self) -> None:
        self.kill()

    def start(self) -> Set[int]:
        """
        Starts collection for a single command
        """
        if sys.platform == "linux":
            os.kill(self.pid, signal.SIGCONT)
        return {self.pid}

    def wait(self) -> Optional[int]:
        """
        Wait for a single process to complete
        """
        if self.finished:
            return None

        try:
            if sys.platform == "linux":
                # print(f"waiting for pid {self.pid}")
                os.waitpid(self.pid, 0)
            elif sys.platform == "win32":
                k32 = win32.kernel32
                while True:
                    try:
                        res = k32.WaitForSingleObject(self.proc_handle, 200)  # 200 ms
                        if res == win32.WAIT_OBJECT_0:
                            break  # command finished naturally
                        if res != win32.WAIT_TIMEOUT:
                            raise OSError("WaitForSingleObject failed")
                    except InterruptedError:
                        self.kill()  # Ctrl-C → terminate children
                        raise
        except (InterruptedError, KeyboardInterrupt) as e:
            self.kill()
            raise e
        self.finished = True
        return self.pid

    def kill(self) -> None:
        """
        This function will kill command workload
        """
        if not self.finished:
            if sys.platform == "linux":
                try:
                    os.kill(self.pid, signal.SIGTERM)
                    os.waitpid(self.pid, 0)
                except ProcessLookupError:
                    pass
            elif sys.platform == "win32":
                # Closing the Job handle kills *all* processes in the job
                k32 = win32.kernel32
                # Kill *everything* in the job (incl. grandchildren)
                k32.TerminateJobObject(self.job_handle, 1)

                # We close **only the job handle** here; the per-process
                # handle is left for subprocess.Popen to close in its
                # destructor – otherwise it would try to close an already
                # closed handle and log WinError 6.
                k32.CloseHandle(self.job_handle)
                self.job_handle = None

            self.finished = True
