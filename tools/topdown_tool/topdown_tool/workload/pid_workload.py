# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=broad-exception-caught

from types import TracebackType
from typing import Optional, Set, Type

from topdown_tool.workload.workload import Workload
from topdown_tool.common import get_pid_watcher, PidWatcher


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

    # Delegate PID monitoring to a platform-specific watcher implementation.

    def __init__(self, pids: Set[int], watcher: Optional[PidWatcher] = None) -> None:
        super().__init__()
        self._watcher = watcher or get_pid_watcher(pids)
        self.pids: Set[int] = self._watcher.start()

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        try:
            watcher = getattr(self, "_watcher", None)
            if watcher:
                watcher.close()
        except Exception:
            pass

    def __del__(self) -> None:
        try:
            watcher = getattr(self, "_watcher", None)
            if watcher:
                watcher.close()
        except Exception:
            pass

    def start(self) -> Set[int]:
        """
        Starts collection for specified PIDs
        """
        return self.pids.copy()

    def wait(
        self,
    ) -> Optional[int]:
        """
        Wait for a single process to complete
        If multiple processes are observed, this function must be called once
        for each PID
        """
        watcher = getattr(self, "_watcher", None)
        if not watcher:
            return None
        return watcher.wait_next()

    def kill_inotifywait(self) -> None:
        """
        Backward-compat no-op: watcher handles its own cleanup.
        """
        watcher = getattr(self, "_watcher", None)
        if watcher:
            try:
                watcher.close()
            except Exception:
                pass

    def kill(self) -> None:
        """
        Will throw an exception, not meant to be called
        """
        raise NotImplementedError("Can't kill workload in PID mode")
