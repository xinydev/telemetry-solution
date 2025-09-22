# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited
"""
Helpers for managing wperf run artifacts on Windows.

Includes cleanup of side files (CSV, CLI traces, etc.) and
JSON stability/wait helpers used by the coordinator.
"""

from pathlib import Path
import time
from typing import Optional


def _try_unlink(p: Path) -> None:
    """
    Best-effort attempt to delete a file.

    Retries once after a short delay if a PermissionError occurs (common on Windows
    due to file locks). Ignores missing files. Any other exceptions are re-raised.
    """
    try:
        p.unlink(missing_ok=True)
    except PermissionError:
        time.sleep(0.05)
        try:
            p.unlink(missing_ok=True)
        except PermissionError:
            # Still locked → give up silently
            pass
    except FileNotFoundError:
        pass


def cleanup_wperf_side_artifacts(dirs: list[Path]) -> None:
    """Delete wperf side artifacts (e.g. core CSVs) from given directories."""
    patterns = ("wperf_system_side_*.core.csv",)
    for d in dirs:
        try:
            for pat in patterns:
                for p in d.glob(pat):
                    _try_unlink(p)
        except Exception:  # pylint: disable=broad-except
            continue


def cleanup_run_artifacts(paths: list[Path]) -> None:
    """Delete a list of run artifact files, ignoring errors."""
    for p in paths:
        try:
            _try_unlink(p)
        except Exception:  # pylint: disable=broad-except
            continue


def cleanup_run_artifacts_for_output_windows(output_json: Optional[Path]) -> None:
    """Remove the JSON output and any companion/side artifacts for a run."""
    if not output_json:
        return

    # Remove main JSON
    _try_unlink(output_json)

    # Remove companions next to the JSON
    base = output_json.with_suffix("") if output_json.suffix == ".json" else output_json
    for suffix in (".cmdline", ".cli.txt"):
        _try_unlink(base.with_suffix(suffix))

    # Side artifacts in output dir (and CWD if different)
    out_dir = output_json.parent
    dirs = [out_dir] + ([Path.cwd()] if out_dir != Path.cwd() else [])
    cleanup_wperf_side_artifacts(dirs)


# ----------------------- JSON wait/stability helpers -----------------------
def is_json_stable(path: Path, settle: float = 0.3) -> bool:
    """
    Return True if the JSON file exists and its size is unchanged after `settle` seconds.
    Mirrors the coordinator's previous inline logic.
    """
    if not path.is_file():
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    time.sleep(settle)
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def wait_for_json_stable(path: Path, timeout: float = 30.0, interval: float = 0.2) -> None:
    """
    Wait until `path` appears and is size-stable, with the same thresholds
    your working coordinator used:
      - up to 30s overall,
      - poll every 200ms,
      - require 3 consecutive stable checks.
    """
    deadline = time.time() + timeout
    last_size = -1
    stable_for = 0
    while time.time() < deadline:
        if path.is_file():
            try:
                size_now = path.stat().st_size
            except OSError:
                size_now = -1
            if size_now > 0:
                if size_now == last_size:
                    stable_for += 1
                    if stable_for >= 3:
                        return
                else:
                    stable_for = 0
                last_size = size_now
        time.sleep(interval)
