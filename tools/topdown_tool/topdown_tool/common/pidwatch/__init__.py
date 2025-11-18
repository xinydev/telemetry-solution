# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

"""
Platform-specific PID watcher implementations.

This package contains OS-specific implementations that watch a set of PIDs
and report when individual processes exit. It exists to keep workload
classes small and testable by delegating platform quirks here.

Submodules:
- linux:  LinuxPidWatcher (inotifywait + /proc status confirmation)
- win32:  Win32PidWatcher (OpenProcess(SYNCHRONIZE) + WaitForMultipleObjects)

Usage (recommended via factory):
    from topdown_tool.common import get_pid_watcher
    watcher = get_pid_watcher({123, 456})

You can also import classes directly if needed:
    from topdown_tool.common.pidwatch.linux import LinuxPidWatcher
    from topdown_tool.common.pidwatch.win32 import Win32PidWatcher

For convenience, this package supports lazy attribute access:
    from topdown_tool.common.pidwatch import LinuxPidWatcher, Win32PidWatcher
will defer importing the platform modules until first attribute access.
"""
from typing import TYPE_CHECKING, Any

__all__ = ["LinuxPidWatcher", "Win32PidWatcher"]

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .linux import LinuxPidWatcher  # noqa: F401
    from .win32 import Win32PidWatcher  # noqa: F401


def __getattr__(name: str) -> Any:
    """
    Lazy-load platform-specific watcher classes on attribute access.

    This avoids importing non-applicable platform modules at import time,
    which is helpful on systems where those imports would fail.
    """
    if name == "LinuxPidWatcher":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.pidwatch.linux import LinuxPidWatcher as _LinuxPidWatcher

        return _LinuxPidWatcher
    if name == "Win32PidWatcher":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.pidwatch.win32 import Win32PidWatcher as _Win32PidWatcher

        return _Win32PidWatcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
