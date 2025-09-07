# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

# pylint: disable=unnecessary-ellipsis

"""
Abstractions (Protocols) used across workloads and platform helpers.

Defining these in a standalone module avoids cyclic imports between
topdown_tool.common.__init__ (which provides factories) and the platform-specific
implementations that consume the protocols.

These Protocols are intentionally small to keep them easy to mock in tests.
"""

from __future__ import annotations

from typing import List, Optional, Protocol, Set


class PidWatcher(Protocol):
    """
    Minimal interface for platform PID watchers (Linux/Windows).
    """

    def start(self) -> Set[int]:
        """
        Start watching processes and return the set of PIDs actually being watched.
        """
        ...

    def wait_next(self) -> Optional[int]:
        """
        Block until one watched PID terminates and return it.
        Return None when there is nothing left to watch.
        """
        ...

    def close(self) -> None:
        """
        Release all resources (file descriptors, OS handles, child helpers, etc).
        Safe to call multiple times.
        """
        ...


class ManagedProcess(Protocol):
    """
    Abstraction for a spawned process managed by a CommandRunner.
    """

    @property
    def pid(self) -> int:  # pragma: no cover - trivial property in fakes
        """
        The OS process ID of the managed process.
        """
        ...

    def start(self) -> None:
        """
        Transition the process to the running state.
        On Linux, typically sends SIGCONT; on Windows, usually a no-op.
        """
        ...

    def wait(self) -> None:
        """
        Block until the process terminates.
        May raise InterruptedError if the user interrupts (e.g., Ctrl+C).
        """
        ...

    def kill(self) -> None:
        """
        Terminate the process (and possibly its process tree, depending on platform).
        Must be idempotent.
        """
        ...


class CommandRunner(Protocol):
    """
    Factory that spawns a ManagedProcess for a given command.
    """

    def spawn(self, command: List[str]) -> ManagedProcess:
        """
        Create a new ManagedProcess for the given command.
        The returned process may or may not be immediately running (platform-specific).
        """
        ...


__all__ = [
    "PidWatcher",
    "ManagedProcess",
    "CommandRunner",
]
