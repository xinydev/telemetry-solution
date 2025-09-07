# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

"""
Common utilities plus small abstractions/factories used by workloads.

Goals:
- Keep existing helpers (unwrap, normalize_str, range_{en,de}code, ArgsError).
- Provide small, mockable Protocols for PID watchers and command runners.
- Expose platform-selecting factories with import-outside-toplevel to avoid
  cyclic imports and make patching in tests straightforward.
"""

from typing import List, Optional, Set, Sequence, TypeVar
import sys

# Re-export Protocols from a dedicated module to avoid cyclic imports while
# keeping the public API stable (importers can continue to do:
# `from topdown_tool.common import PidWatcher, CommandRunner, ManagedProcess`).
from topdown_tool.common.abstractions import (  # noqa: F401
    PidWatcher,
    ManagedProcess,
    CommandRunner,
)

T = TypeVar("T")


def get_pid_watcher(pids: Set[int]) -> PidWatcher:
    """
    Factory for a platform-specific PidWatcher implementation.

    Notes for tests:
    - Patch this function directly to return a fake watcher (DI-friendly), e.g.:
        monkeypatch.setattr("topdown_tool.common.get_pid_watcher",
                            lambda p: FakePidWatcher(p))
    - Deferred imports avoid importing platform modules on the wrong OS.
    """
    if sys.platform == "linux":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.pidwatch.linux import LinuxPidWatcher  # type: ignore[attr-defined]

        return LinuxPidWatcher(pids)  # pragma: no cover
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.pidwatch.win32 import Win32PidWatcher  # type: ignore[attr-defined]

        return Win32PidWatcher(pids)  # pragma: no cover
    raise NotImplementedError(f"Unsupported platform: {sys.platform}")


def get_command_runner() -> CommandRunner:
    """
    Factory for a platform-specific CommandRunner implementation.

    Notes for tests:
    - Patch this function directly to return a fake runner, e.g.:
        monkeypatch.setattr("topdown_tool.common.get_command_runner",
                            lambda: FakeCommandRunner())
    - Deferred imports avoid importing platform modules on the wrong OS.
    """
    if sys.platform == "linux":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.command_runner.linux import LinuxCommandRunner  # type: ignore[attr-defined]

        return LinuxCommandRunner()  # pragma: no cover
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel
        from topdown_tool.common.command_runner.win32 import Win32CommandRunner  # type: ignore[attr-defined]

        return Win32CommandRunner()  # pragma: no cover
    raise NotImplementedError(f"Unsupported platform: {sys.platform}")


__all__ = [
    # Protocols (re-exported)
    "PidWatcher",
    "ManagedProcess",
    "CommandRunner",
    # Factories
    "get_pid_watcher",
    "get_command_runner",
    # Utilities
    "unwrap",
    "normalize_str",
    "range_decode",
    "range_encode",
    "ArgsError",
]


def unwrap(value: Optional[T], message: str = "Unexpected None value") -> T:
    """
    Ensures that the given optional value is not None.

    Parameters:
        value (Optional[T]): The value to check.
        message (str): The error message to raise if value is None.

    Returns:
        T: The non-None value.

    Raises:
        ValueError: If value is None.
    """
    if value is None:
        raise ValueError(message)
    return value


def normalize_str(name: str) -> str:
    """Normalize strings (lower case and underscores) for consistent key matching."""
    return name.lower().replace("_", "").replace("-", "")


def range_decode(arg: str) -> Optional[List[int]]:
    """
    Converts a string representing ranges and individual numbers into a sorted list of integers.

    The input should contain numbers separated by commas (,) for individual elements or hyphens (-) to indicate a range.
    Examples:
      "1"           -> [1]
      "1,3,5"       -> [1, 3, 5]
      "1-3"         -> [1, 2, 3]
      "1-3,5,7-9"   -> [1, 2, 3, 5, 7, 8, 9]
    """
    if arg is None:
        return None
    intermediate_result = set()
    for value_range in arg.split(","):
        if value_range.isdecimal():
            element = int(value_range)
            assert element >= 0
            intermediate_result.add(element)
        else:
            range_start, range_stop = map(int, value_range.split("-", 1))
            assert 0 <= range_start <= range_stop
            intermediate_result.update(range(range_start, range_stop + 1))
    return sorted(intermediate_result)


def range_encode(nums: Sequence[int]) -> Optional[str]:
    if not nums:
        return None

    # Ensure the list is sorted
    nums = sorted(nums)
    ranges = []
    start = nums[0]
    end = nums[0]

    for n in nums[1:]:
        if n == end + 1:
            end = n
        else:
            if start == end:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{end}")
            start = n
            end = n

    # Add the last group
    if start == end:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{end}")
    return ",".join(ranges)


class ArgsError(Exception):
    pass
