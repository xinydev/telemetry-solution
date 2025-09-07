# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=broad-exception-caught

"""
Win32 implementation of a PID watcher using OpenProcess(SYNCHRONIZE) and
WaitForMultipleObjects.

Design:
- Open each requested PID with SYNCHRONIZE access so it becomes a waitable handle.
- Use WaitForMultipleObjects with a short timeout to allow Python to process
  SIGINT between polls.
- Close handles when a process signals, and when close() is called.
"""

from __future__ import annotations

import sys
from typing import Dict, Optional, Set

from topdown_tool.common import win32 as _win32


class Win32PidWatcher:
    """
    Watches a set of PIDs and yields them as they terminate.

    Public API:
      - start() -> Set[int]: returns the set of PIDs being watched
      - wait_next() -> Optional[int]: blocks until one PID terminates (or returns
        None when none remain)
      - close(): releases OS handles
    """

    # Class-level attribute declaration for static analyzers
    _handles: Dict[int, int]

    # Access right needed to wait on a process handle
    _SYNCHRONIZE = 0x00100000

    def __init__(self, pids: Set[int]) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Win32PidWatcher can only be used on Windows")

        # Map: HANDLE (int) -> pid
        self._handles: Dict[int, int] = {}

        # Open each PID with SYNCHRONIZE access to obtain a waitable handle.
        k32 = _win32.kernel32
        for pid in pids:
            handle = k32.OpenProcess(self._SYNCHRONIZE, False, int(pid))
            if handle:
                self._handles[int(handle)] = int(pid)
            # If OpenProcess fails (e.g., process gone or insufficient rights),
            # we silently skip it; callers get the set returned by start().

    # Public API ----------------------------------------------------------------

    def start(self) -> Set[int]:
        """
        Return the PIDs actually being watched (filtered to processes we could open).
        """
        return set(self._handles.values())

    def wait_next(self) -> Optional[int]:
        """
        Block until one watched PID is signaled and return it.
        Returns None if there are no more PIDs to watch.
        """
        if not self._handles:
            return None

        # Keep a stable list of raw HANDLE values for index math.
        handle_list = list(self._handles.keys())

        while handle_list:
            status = _win32.wait_for_multiple(handle_list, 200)  # 200 ms poll
            if status == _win32.WAIT_TIMEOUT:
                # No process changed state; loop again to allow SIGINT processing.
                continue
            if status == _win32.WAIT_FAILED:
                raise OSError("WaitForMultipleObjects failed")

            index = status - _win32.WAIT_OBJECT_0
            if 0 <= index < len(handle_list):
                signaled = handle_list[index]
                try:
                    _win32.close_handle(signaled)
                finally:
                    pid = self._handles.pop(signaled)
                    # Rebuild list from remaining keys to keep indices correct
                    handle_list = list(self._handles.keys())
                return pid

        # No more handles left
        return None

        # Note: any other exceptions (OSError, etc.) are allowed to propagate.

    def close(self) -> None:
        """
        Release all remaining process handles.
        """
        for handle in list(self._handles.keys()):
            try:
                _win32.close_handle(handle)
            except OSError:
                pass
        self._handles.clear()


__all__ = ["Win32PidWatcher"]
