# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Shared Win32 constants and tiny helpers for kernel32 calls.
Keep all WAIT_* and INFINITE values in one place to avoid duplication.
"""

import contextlib
import signal
import sys
from typing import Sequence, Optional, Iterator

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

    # --- Tiny helpers for console Ctrl-C broadcast -------------------------
    # BOOL GenerateConsoleCtrlEvent(DWORD dwCtrlEvent, DWORD dwProcessGroupId)
    _GenerateConsoleCtrlEvent = kernel32.GenerateConsoleCtrlEvent
    _GenerateConsoleCtrlEvent.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD]
    _GenerateConsoleCtrlEvent.restype = ctypes.wintypes.BOOL

    # BOOL SetConsoleCtrlHandler(PHANDLER_ROUTINE HandlerRoutine, BOOL Add)
    _SetConsoleCtrlHandler = kernel32.SetConsoleCtrlHandler
    _SetConsoleCtrlHandler.argtypes = [ctypes.wintypes.LPVOID, ctypes.wintypes.BOOL]
    _SetConsoleCtrlHandler.restype = ctypes.wintypes.BOOL

    def send_console_ctrl_c(process_group_id: int = 0) -> None:
        """
        Broadcast a Ctrl-C console event to the given process group (0 = current console group).
        Raises OSError on Win32 API failure.
        """
        # Temporarily ignore console events in this process so our own Ctrl-C doesn't bite us.
        _SetConsoleCtrlHandler(None, True)
        try:
            if not _GenerateConsoleCtrlEvent(
                getattr(signal, "CTRL_C_EVENT", 0), int(process_group_id)
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            _SetConsoleCtrlHandler(None, False)

else:
    # Stubs so import works on non-Windows (shouldn’t be used at runtime there)
    WAIT_OBJECT_0 = WAIT_ABANDONED_0 = WAIT_TIMEOUT = WAIT_FAILED = INFINITE = 0

    def wait_for_multiple(
        handles: Sequence[int], timeout_ms: int
    ) -> int:  # pragma: no cover - non-Windows
        raise NotImplementedError("Windows only")

    def close_handle(handle: int) -> None:  # pragma: no cover - non-Windows
        raise NotImplementedError("Windows only")

    def send_console_ctrl_c(process_group_id: int = 0) -> None:  # pragma: no cover - non-Windows
        raise NotImplementedError("Windows only")


# ---------------------------------------------------------------------------
# Cross-platform (safe) context managers for SIGINT handling.
# These are useful even outside Windows and keep call sites simple.
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def ignore_sigint_temporarily() -> Iterator[None]:
    """
    Temporarily ignore SIGINT in the current process while the context is active.
    Restores the previous handler on exit.
    """
    prev = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, prev)


@contextlib.contextmanager
def swallow_keyboard_interrupt() -> Iterator[None]:
    """
    Swallow a user-triggered KeyboardInterrupt/InterruptedError inside the block.
    Useful around short critical sections during shutdown/cleanup.
    """
    try:
        yield
    except (KeyboardInterrupt, InterruptedError):
        pass
