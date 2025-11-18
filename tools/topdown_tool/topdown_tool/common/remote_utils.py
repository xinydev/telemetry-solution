# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import logging
import shlex
import time
from typing import NoReturn, Optional

from topdown_tool.common.devlib_types import Target


def _rethrow_interrupt(exc: BaseException) -> NoReturn:
    if isinstance(exc, KeyboardInterrupt):
        raise InterruptedError from exc
    raise exc


# pylint: disable=too-many-branches
def remote_cleanup_target_temp_dirs(target: Optional["Target"]) -> None:
    """
    Safely clean up temporary directories created by devlib (e.g., via target.mkdtemp()).
    Deletes tmp.* directories that either:
      - contain a dead background PID, or
      - do not have a pid file at all.
    Works for both SSH and ADB targets.
    """
    if target is None:
        # Nothing to do if no target is specified
        return

    # Determine tmp base dir (e.g., /data/local/tmp or /tmp)
    tmp_base = getattr(target, "tmp_directory", None)
    if not tmp_base or not isinstance(tmp_base, str):
        return

    # Normalize SSH targets that stage files under /tmp/tmp.* to /tmp
    if tmp_base.startswith("/tmp/tmp.") and getattr(target, "os", None) == "linux":
        tmp_base = "/tmp"

    try:
        tmp_listing = target.list_directory(tmp_base)  # type: ignore
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logging.warning("[cleanup] Failed to list temp dirs in %s: %s", tmp_base, exc)
        return

    for entry in tmp_listing:
        if not entry.startswith("tmp."):
            continue

        full_path = f"{tmp_base}/{entry}"
        pid_file = f"{full_path}/pid"
        removal_reason: Optional[str] = None

        try:
            pid_str = target.read_value(pid_file).strip()  # type: ignore
        except Exception as exc:  # pylint: disable=broad-exception-caught
            removal_reason = f"pid read failed ({exc})"
        else:
            if not pid_str.isdigit():
                removal_reason = "non-numeric pid"
            else:
                try:
                    check_cmd = f"kill -0 {pid_str}"
                    result = target.execute(check_cmd, check_exit_code=False)  # type: ignore
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    removal_reason = f"pid check failed ({exc})"
                else:
                    if result.strip() != "":
                        removal_reason = "stale pid"

        if removal_reason is None:
            logging.debug("[cleanup] Skipped temp dir: %s (pid still alive)", full_path)
            continue

        # dont delete the fifo gate dirs while its being used
        has_gate_fifo = False
        try:
            entry_listing = target.list_directory(full_path)
            has_gate_fifo = any(name.startswith("topdown_gate_") for name in entry_listing)
        except Exception:  # pylint: disable=broad-exception-caught
            has_gate_fifo = False

        if has_gate_fifo:
            logging.debug("[cleanup] Skipped temp dir: %s (gate FIFO present)", full_path)
            continue

        try:
            target.execute(f"rm -rf {shlex.quote(full_path)}", as_root=True)  # type: ignore
            logging.debug("[cleanup] Deleted temp dir: %s (%s)", full_path, removal_reason)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning("[cleanup] Failed to delete %s: %s", full_path, exc)


def remote_read_text(target: "Target", path: str) -> Optional[str]:
    """Read a small text file from the remote target returning stripped content."""

    try:
        out = target.read_value(path)  # type: ignore[attr-defined]
    except Exception:  # pylint: disable=broad-exception-caught
        try:
            out = target.execute(
                f"cat {shlex.quote(path)}",
                check_exit_code=False,
            )  # type: ignore[attr-defined]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.debug("Failed to read %s on remote target: %s", path, exc)
            return None

    if isinstance(out, (bytes, bytearray)):
        out = out.decode("utf-8", errors="ignore")
    text = str(out).strip()
    return text if text else None


def remote_pid_exists(target: "Target", pid: int, as_root: bool) -> bool:
    """
    Check existence of /proc/<pid> on the target.
    Prefer target.file_exists if available, else shell test.
    """
    try:
        if hasattr(target, "file_exists"):
            return bool(target.file_exists(f"/proc/{pid}"))
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    try:
        target.execute(
            f"sh -lc 'test -d /proc/{pid}'",
            check_exit_code=True,
            as_root=as_root,
        )
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def remote_path_exists(target: "Target", path: str) -> bool:
    """Return whether a filesystem path exists on the target.

    This first tries devlib's ``file_exists`` helper when available and then falls back to
    invoking ``test -e`` via the shell so that BusyBox systems without a standalone
    ``/bin/test`` are handled transparently.
    """

    try:
        if hasattr(target, "file_exists") and bool(target.file_exists(path)):
            return True
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    try:
        target.execute(
            f"sh -lc 'test -e {shlex.quote(path)}'",
            check_exit_code=True,
        )
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


# pylint: disable=too-many-arguments
def remote_wait_for_pid_state(
    target: "Target",
    pid: int,
    *,
    present: bool,
    timeout: Optional[float] = None,
    as_root: bool,
    poll_interval: float = 0.05,
) -> bool:
    """Block until ``/proc/<pid>`` either appears or disappears on the target.

    Args:
        target: devlib target instance.
        pid: Process ID to monitor.
        present: ``True`` to wait for the PID to appear, ``False`` to wait for it to vanish.
        timeout: Optional timeout in seconds; ``None`` waits indefinitely.
        as_root: Execute the helper shell loop with elevated privileges.
        poll_interval: Sleep interval (seconds) used inside the remote helper loop.

    Returns:
        bool: ``True`` if the desired state was observed before timing out, ``False`` otherwise.
    """

    interval = max(poll_interval, 0.01)
    deadline = None if timeout is None else time.monotonic() + max(timeout, 0.0)

    while True:
        exists = False
        try:
            exists = remote_pid_exists(target, pid, as_root)
        except (KeyboardInterrupt, InterruptedError) as exc:
            _rethrow_interrupt(exc)
        except Exception:  # pylint: disable=broad-exception-caught
            exists = False

        if (present and exists) or (not present and not exists):
            return True

        if deadline is not None and time.monotonic() >= deadline:
            return False

        try:
            time.sleep(interval)
        except (KeyboardInterrupt, InterruptedError) as exc:
            _rethrow_interrupt(exc)
        except Exception:  # pylint: disable=broad-exception-caught
            return False


__all__ = [
    "remote_cleanup_target_temp_dirs",
    "remote_read_text",
    "remote_pid_exists",
    "remote_path_exists",
    "remote_wait_for_pid_state",
]
