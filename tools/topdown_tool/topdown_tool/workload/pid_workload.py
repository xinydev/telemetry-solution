# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm LimitedLimited

import ctypes
import logging
import os
import signal
import sys
from shutil import which
from types import TracebackType
from typing import Dict, Optional, Set, Type

from rich import get_console
from topdown_tool.workload.workload import Workload
from topdown_tool.common import win32


class PidWorkload(Workload):
    """
    Monitors a set of process IDs (PIDs) and waits for their termination.

    Linux Implementation Notes:
    On Linux, since it is not possible to wait for non-child processes directly, this class
    leverages the "inotifywait" tool (from the inotify-tools package) to detect when the
    file /proc/[pid]/exe is closed—which happens when the process terminates.
    Due to potential spurious notifications (e.g., when multiple processes share the same executable),
    the implementation additionally reads /proc/[pid]/status to confirm that the process has exited
    (or become a zombie). If the inotifywait tool is not installed, an exception is raised.
    """

    # We need that many bytes to get State field from /proc/[pid]/status
    RELEVANT_STATUS_LENTGH = 42

    def __init__(self, pids: Set[int]) -> None:
        self.pids: Set[int] = set()

        if sys.platform == "linux":
            self.inotify_pid = None
            self.inotify_pipe = None

            # Check if inotifywait is installed
            if which("inotifywait") is None:
                raise RuntimeError(
                    '"inotifywait" is not installed. Please install it from "inotify-tools" package to use "--pid" mode.'
                )

            # Check processes existence and create commandline for "inotifywait"
            self.procfs_directory_handles: Dict[int, int] = {}
            cmd = ["inotifywait", "-me", "close_nowrite"]
            for pid in pids:
                if pid not in self.procfs_directory_handles:
                    try:
                        self.procfs_directory_handles[pid] = os.open(
                            f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY
                        )
                        cmd.append(f"/proc/{pid}/exe")
                    except FileNotFoundError:
                        logging.warning("Process %d doesn't exist", pid)
            if not self.procfs_directory_handles:
                raise RuntimeError("No processes to monitor.")

            # Spawn "inotifywait"
            r, w = os.pipe2(0)
            self.inotify_pid = os.fork()
            if self.inotify_pid == 0:
                os.close(r)

                fd = os.open("/dev/null", os.O_RDONLY)
                os.dup2(fd, 0)
                os.close(fd)

                os.dup2(w, 1)
                os.close(w)

                fd = os.open("/dev/null", os.O_WRONLY)
                os.dup2(fd, 2)
                os.close(fd)

                os.execvp(cmd[0], cmd)

            # Must be done after forking inotifywait to prevent problems with signal
            # handlers inherited by inotifywait if done before fork
            super().__init__()

            os.close(w)
            self.inotify_pipe = os.fdopen(r)

            self.pids = set(self.procfs_directory_handles.keys())
            self.terminated_pids: Set[int] = set()
        elif sys.platform == "win32":
            super().__init__()
            self.pids = set()
            self.handles = {}
            synchronize = 0x100000
            for pid in pids:
                handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
                if handle:
                    self.pids.add(pid)
                    self.handles[handle] = pid
                else:
                    console = get_console()
                    console.print(f"Process {pid} doesn't exist")
        else:
            raise RuntimeError("Invalid platform for PidWorkload")

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        if sys.platform == "linux":
            for fd in self.procfs_directory_handles.values():
                os.close(fd)
            self.procfs_directory_handles.clear()
            self.kill_inotifywait()
        elif sys.platform == "win32":
            for handle in list(self.handles.keys()):
                win32.close_handle(handle)

    def __del__(self) -> None:
        if sys.platform == "linux":
            self.kill_inotifywait()

    def start(self) -> Set[int]:
        """
        Starts collection for specified PIDs
        """
        return self.pids.copy()

    # pylint: disable=too-many-branches,too-many-statements,too-many-locals, too-many-return-statements, try-except-raise
    def wait(self) -> Optional[int]:
        """
        Wait for a single process to complete
        If multiple processes are observed, this function must be called once
        for each PID
        """
        if sys.platform == "linux":
            if not self.procfs_directory_handles:
                return None

            def close_next_descriptor() -> int:
                child_pid = self.terminated_pids.pop()
                os.close(self.procfs_directory_handles[child_pid])
                del self.procfs_directory_handles[child_pid]
                if not self.procfs_directory_handles:
                    # If all processes terminated, we terminate "inotifywait" too
                    self.kill_inotifywait()
                return child_pid

            # During the last check with inotifywait, we could get more than one
            # terminated process
            if self.terminated_pids:
                return close_next_descriptor()

            child_pid = None
            try:
                assert self.inotify_pipe is not None
                # Wait for notification from "inotifywait"
                line = self.inotify_pipe.readline()
                while line:
                    # Check which process was terminated
                    # We can't rely on contents of line returned from inotifywait because
                    # in some circumstances "inotifywait" can issue spurious notification
                    for pid, dir_fd in self.procfs_directory_handles.items():
                        try:
                            # We try to read /proc/[pid]/status file.
                            # If we detect Zombie process or if file open fails,
                            # then we know that monitored process has terminated.
                            fd = os.open("status", os.O_RDONLY, dir_fd=dir_fd)
                            status = os.read(fd, self.RELEVANT_STATUS_LENTGH)
                            search = b"\nState:\t"
                            if status[status.index(search) + len(search)] == ord("Z"):
                                child_pid = pid
                            os.close(fd)
                        except (ProcessLookupError, FileNotFoundError):
                            child_pid = pid
                        if child_pid:
                            self.terminated_pids.add(child_pid)
                    # If self.terminated_pids is empty, it means that it was a
                    # spurious notification
                    if self.terminated_pids:
                        return close_next_descriptor()

                    # Wait for next notification from "inotifywait" after spurious one
                    line = self.inotify_pipe.readline()
            except InterruptedError as e:
                self.kill_inotifywait()
                raise e

            # If we reach this point, it means that not all PIDs were waited on but
            # there was error while reading from pipe. Presumably "inotifywait" was
            # killed externally. In this case, we return observed PIDs one after
            # another.
            self.kill_inotifywait()
            # Return next PID from observed set of PIDs
            child_pid = next(iter(self.procfs_directory_handles))
            os.close(self.procfs_directory_handles[child_pid])
            del self.procfs_directory_handles[child_pid]
            return child_pid
        if sys.platform == "win32":
            # Poll with a short timeout so Python can run its SIGINT handler between polls.
            if not self.handles:
                return None

            # Keep a stable list of raw HANDLE values for index math.
            handle_list = list(self.handles.keys())

            while handle_list:
                try:
                    status = win32.wait_for_multiple(handle_list, 200)  # 200 ms
                    if status == win32.WAIT_TIMEOUT:
                        # No process changed state; loop again to allow SIGINT processing.
                        continue
                    if status == win32.WAIT_FAILED:
                        raise OSError("WaitForMultipleObjects failed")

                    index = status - win32.WAIT_OBJECT_0
                    if 0 <= index < len(handle_list):
                        signaled = handle_list[index]
                        try:
                            win32.close_handle(signaled)
                        finally:
                            pid = self.handles.pop(signaled)
                            # Rebuild list from remaining keys to keep indices correct
                            handle_list = list(self.handles.keys())
                        return pid

                except InterruptedError:
                    # Propagate user interrupt upward; do not kill monitored processes.
                    raise

            return None
        return None

    def kill_inotifywait(self) -> None:
        """
        When inotifywait is no longer needed, this function is called to stop it
        """
        if sys.platform == "linux":
            if self.inotify_pipe:
                self.inotify_pipe.close()
                self.inotify_pipe = None
            if self.inotify_pid:
                os.kill(self.inotify_pid, signal.SIGTERM)
                os.waitpid(self.inotify_pid, 0)
                self.inotify_pid = None

    def kill(self) -> None:
        """
        Will throw an exception, not meant to be called
        """
        raise NotImplementedError("Can't kill workload in PID mode")
