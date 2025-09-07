# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

# pylint: disable=broad-exception-caught

"""
Linux CommandRunner implementation using fork/exec with SIGSTOP/SIGCONT.

This module provides a small, testable abstraction for spawning and managing a
single process in a way that lets a caller control the exact moment it starts
running user code:

- spawn(command): forks and execs the command but immediately stops the child
  (SIGSTOP). The parent waits for the child to be stopped (WUNTRACED) and
  returns a ManagedProcess handle.
- start(): sends SIGCONT to let the child run.
- wait(): waits for child termination (propagates InterruptedError).
- kill(): sends SIGTERM and waits for the child to exit (best-effort).
"""

from __future__ import annotations

import os
import shutil
import signal
from typing import List

from topdown_tool.common.abstractions import ManagedProcess


class _LinuxProcess(ManagedProcess):
    def __init__(self, pid: int) -> None:
        self._pid = pid
        self._finished = False

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        if not self._finished:
            os.kill(self._pid, signal.SIGCONT)

    def wait(self) -> None:
        if self._finished:
            return
        # Propagate InterruptedError to caller
        os.waitpid(self._pid, 0)
        self._finished = True

    def kill(self) -> None:
        if self._finished:
            return
        try:
            os.kill(self._pid, signal.SIGTERM)
        except ProcessLookupError:
            # Already gone
            self._finished = True
            return
        # Best-effort reap
        try:
            os.waitpid(self._pid, 0)
        except (ChildProcessError, ProcessLookupError):
            pass
        self._finished = True


class LinuxCommandRunner:
    """
    Spawn and manage a Linux process, initially stopped (SIGSTOP) until start().
    """

    # pylint: disable=inconsistent-return-statements, duplicate-code
    def spawn(self, command: List[str]) -> ManagedProcess:
        """
        Fork and exec the command in a stopped state and return a ManagedProcess.

        Raises:
            OSError: if the command is not found or cannot be executed.
        """
        if not command or not isinstance(command[0], str):
            raise OSError("Invalid command")
        exe = command[0]

        # Resolve executable like a shell would (PATH search)
        if shutil.which(exe) is None:
            raise OSError(
                f"Command {exe} cannot be executed. Please check that the file exists in PATH and you have the necessary rights to run it."
            )

        pid = os.fork()
        if pid == 0:
            # Child process: stop immediately, then replace image with execvp
            try:
                os.kill(os.getpid(), signal.SIGSTOP)
                os.execvp(exe, command)
            except Exception:
                # If exec fails, exit child without affecting parent
                os._exit(127)  # noqa: SLF001
        else:
            # Parent: wait for the child to enter the stopped state (WUNTRACED)
            os.waitpid(pid, os.WUNTRACED)
            return _LinuxProcess(pid)


__all__ = ["LinuxCommandRunner"]
