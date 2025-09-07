# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Shared Win32 constants and tiny helpers for kernel32 calls.
Keep all WAIT_* and INFINITE values in one place to avoid duplication.
"""

import sys
from typing import Sequence, Optional

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    kernel32 = ctypes.windll.kernel32  # noqa: SLF001

    # WaitForMultipleObjects/WaitForSingleObject constants
    WAIT_OBJECT_0 = 0x00000000
    WAIT_ABANDONED_0 = 0x00000080
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
    INFINITE = 0xFFFFFFFF

    def wait_for_multiple(handles: Sequence[int], timeout_ms: Optional[int]) -> int:
        """
        Call WaitForMultipleObjects and return the raw DWORD status.
        `timeout_ms=None` maps to INFINITE.
        """
        arr = (ctypes.wintypes.HANDLE * len(handles))(*handles)
        timeout = INFINITE if timeout_ms is None else int(timeout_ms)
        return kernel32.WaitForMultipleObjects(len(arr), arr, False, timeout)

    def close_handle(handle: int) -> None:
        kernel32.CloseHandle(ctypes.wintypes.HANDLE(handle))

else:
    # Stubs so import works on non-Windows (shouldn’t be used at runtime there)
    WAIT_OBJECT_0 = WAIT_ABANDONED_0 = WAIT_TIMEOUT = WAIT_FAILED = INFINITE = 0

    def wait_for_multiple(
        handles: Sequence[int], timeout_ms: int
    ) -> int:  # pragma: no cover - non-Windows
        raise NotImplementedError("Windows only")

    def close_handle(handle: int) -> None:  # pragma: no cover - non-Windows
        raise NotImplementedError("Windows only")
