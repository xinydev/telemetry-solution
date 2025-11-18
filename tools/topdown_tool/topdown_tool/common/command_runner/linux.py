# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

# pylint: disable=broad-exception-caught

"""
Linux CommandRunner implementation using fork/exec and pipe for IPC.

This module provides a small, testable abstraction for spawning and managing a
single process in a way that lets a caller control the exact moment it starts
running user code:

- spawn(command): forks and execs the command but immediately stops the child by
  blocking on a pipe. The parent returns a ManagedProcess handle.
- start(): closes write end of the pipe so that child either returns from an
  already blocked read or will return immediately from read if it hasn't been
  called yet.
- wait(): waits for child termination (propagates InterruptedError).
- kill(): sends SIGTERM and waits for the child to exit (best-effort).
"""

import os
import shutil
import signal
from typing import List

from topdown_tool.common.abstractions import ManagedProcess


class _LinuxProcess(ManagedProcess):
    def __init__(self, pid: int, pipe: int) -> None:
        self._pid = pid
        self._pipe = pipe
        self._finished = False

    def _close_pipe(self) -> None:
        if self._pipe >= 0:
            os.close(self._pipe)
            self._pipe = -1

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        if not self._finished:
            self._close_pipe()

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
            self._close_pipe()
            self._finished = True
            return
        # If process is killed before we call start, we should close pipe descriptor
        self._close_pipe()
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

        r, w = os.pipe2(os.O_CLOEXEC)
        pid = os.fork()
        if pid == 0:
            # Child process: stop immediately, then replace image with execvp
            try:
                os.close(w)
                os.read(r, 1)
                os.execvp(exe, command)
            except Exception:
                # If exec fails, exit child without affecting parent
                os._exit(127)  # noqa: SLF001
        else:
            os.close(r)
            # Parent: wait for the child to enter the stopped state (WUNTRACED)
            return _LinuxProcess(pid, w)


__all__ = ["LinuxCommandRunner"]
