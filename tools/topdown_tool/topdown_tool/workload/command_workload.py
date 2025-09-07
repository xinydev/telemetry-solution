# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm LimitedLimited

import ctypes
import os
import signal
import sys
import shutil
from types import TracebackType
from typing import List, Optional, Set, Type

from topdown_tool.workload.workload import Workload


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
            self.handle = os.spawnv(os.P_NOWAIT, command[0], command)
            self.pid = ctypes.windll.kernel32.GetProcessId(self.handle)
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
        return self.pids.copy()

    def wait(self) -> Optional[int]:
        """
        Wait for a single process to complete
        """
        if not self.finished:
            try:
                if sys.platform == "linux":
                    # print(f"waiting for pid {self.pid}")
                    os.waitpid(self.pid, 0)
                elif sys.platform == "win32":
                    os.waitpid(self.handle, 0)
            except InterruptedError as e:
                self.kill()
                raise e
            self.finished = True
            return self.pid

        return None

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
                ctypes.windll.kernel32.TerminateProcess(self.handle, signal.SIGTERM)
            self.finished = True
