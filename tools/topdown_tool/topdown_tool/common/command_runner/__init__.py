# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm

"""
Platform-specific command runner implementations.

This package contains OS-specific implementations that spawn and manage a single
process in a way suitable for telemetry capture. It exists to keep workload
classes small and testable by delegating platform quirks here.

Submodules:
- linux:  LinuxCommandRunner (fork/exec with SIGSTOP/SIGCONT control)
- win32:  Win32CommandRunner (subprocess + Job Object with KILL_ON_JOB_CLOSE)

Recommended usage via factory:
    from topdown_tool.common import get_command_runner
    runner = get_command_runner()
    proc = runner.spawn(["sleep", "1"])
    proc.start()
    proc.wait()

You can also import classes directly if needed:
    from topdown_tool.common.command_runner.linux import LinuxCommandRunner
    from topdown_tool.common.command_runner.win32 import Win32CommandRunner

For convenience, this package supports lazy attribute access:
    from topdown_tool.common.command_runner import LinuxCommandRunner, Win32CommandRunner
will defer importing the platform modules until first attribute access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["LinuxCommandRunner", "Win32CommandRunner"]

if TYPE_CHECKING:  # pragma: no cover - for type checkers only
    from .linux import LinuxCommandRunner  # noqa: F401
    from .win32 import Win32CommandRunner  # noqa: F401


def __getattr__(name: str) -> Any:
    """
    Lazy-load platform-specific runner classes on attribute access.

    This avoids importing non-applicable platform modules at import time,
    which is helpful on systems where those imports would fail.
    """
    if name == "LinuxCommandRunner":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.command_runner.linux import (
            LinuxCommandRunner as _LinuxCommandRunner,
        )

        return _LinuxCommandRunner
    if name == "Win32CommandRunner":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.command_runner.win32 import (
            Win32CommandRunner as _Win32CommandRunner,
        )

        return _Win32CommandRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
