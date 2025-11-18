# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=broad-exception-caught

"""
Remote Linux PidWatcher implementation (via devlib).

This watcher monitors a set of PIDs on a remote Linux/Android target provided by
devlib. It polls /proc/<pid> on the target and reports PIDs as they exit.

Protocol alignment:
- start()      -> returns the set of PIDs actually being watched (existing on target).
- wait_next()  -> blocks until one watched PID terminates and returns it; returns None when none left.
- close()      -> idempotent resource cleanup (no-op here).
"""

import time
from typing import Optional, Set
from topdown_tool.common.remote_utils import (
    remote_cleanup_target_temp_dirs,
    remote_pid_exists,
)
from topdown_tool.common.devlib_types import Target


class RemoteLinuxPidWatcher:
    """
    Remote PidWatcher that operates on a devlib Target (Linux or Android).
    """

    def __init__(
        self,
        pids: Set[int],
        target: "Target",
        *,
        poll_interval: float = 0.1,
        as_root: bool = True,
    ) -> None:
        """
        Args:
            pids: Initial set of PIDs to watch (OS PIDs on the target).
            target: devlib Target instance (AndroidTarget or LinuxTarget).
            poll_interval: Sleep duration between /proc polls.
            as_root: Use elevated privileges for target.execute calls when needed.
        """
        self._target = target
        self._as_root = as_root
        self._poll = float(poll_interval)
        self._watched: Set[int] = set(int(p) for p in pids if p >= 0)
        self._closed = False

    # ----- Protocol: start / wait_next / close -----

    def start(self) -> Set[int]:
        """
        Filter PIDs to the subset that actually exist on the target.
        """
        if self._closed:
            return set()

        existing = {
            pid for pid in self._watched if remote_pid_exists(self._target, pid, self._as_root)
        }
        self._watched = existing
        return set(self._watched)

    def wait_next(self) -> Optional[int]:
        """
        Block until one watched PID terminates; return that PID.
        Return None if there are no PIDs left to watch.
        """
        if self._closed or not self._watched:
            return None

        while self._watched and not self._closed:
            # Check each PID; return the first one that disappeared
            for pid in list(self._watched):
                if not remote_pid_exists(self._target, pid, self._as_root):
                    self._watched.discard(pid)
                    return pid
            time.sleep(self._poll)

        return None

    def close(self) -> None:
        """
        No persistent handles to release; idempotent.
        """
        self._closed = True
        self._watched.clear()
        remote_cleanup_target_temp_dirs(self._target)

    def __del__(self) -> None:
        remote_cleanup_target_temp_dirs(self._target)


__all__ = ["RemoteLinuxPidWatcher"]
