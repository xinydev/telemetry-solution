# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=broad-exception-caught

"""
Linux implementation of a PID watcher using inotifywait and /proc status parsing.

Design:
- Uses "inotifywait -me close_nowrite" on /proc/[pid]/exe to get notifications
  when a process exits. This can emit spurious events, so we confirm by reading
  /proc/[pid]/status and checking the "State:" field (Z for zombie), or by
  handling errors (ProcessLookupError/FileNotFoundError) when opening.
- Maintains an internal set of "terminated" PIDs that is drained one by one
  by wait_next().
"""

# NOTE: Avoid importing Protocols (PidWatcher/ManagedProcess) from topdown_tool.common
# here to prevent cyclic imports. This module exposes a concrete implementation only.

import logging
import os
import signal
import sys
from shutil import which
from typing import Dict, IO, Optional, Set


class LinuxPidWatcher:
    """
    Watches a set of PIDs and yields them as they terminate.

    Public API:
      - start() -> Set[int]: returns the set of PIDs being watched
      - wait_next() -> Optional[int]: blocks until one PID terminates (or returns
        None when none remain)
      - close(): releases resources and terminates helper process
    """

    # Class-level attribute declarations for static analyzers
    inotify_pid: Optional[int]
    inotify_pipe: Optional[IO[str]]
    procfs_directory_handles: Dict[int, int]
    terminated_pids: Set[int]

    def __init__(self, pids: Set[int]) -> None:
        assert sys.platform == "linux"

        # Ensure inotifywait is available
        if which("inotifywait") is None:
            raise RuntimeError(
                '"inotifywait" is not installed. Please install it from the "inotify-tools" package.'
            )

        self.inotify_pid: Optional[int] = None
        self.inotify_pipe: Optional[IO[str]] = None

        # Map pid -> open fd for /proc/<pid> directory (used as dir_fd)
        self.procfs_directory_handles: Dict[int, int] = {}
        self.terminated_pids: Set[int] = set()

        # Build inotifywait command and validate PIDs
        cmd = ["inotifywait", "-me", "close_nowrite"]
        for pid in pids:
            if pid in self.procfs_directory_handles:
                continue
            try:
                dir_fd = os.open(f"/proc/{pid}", os.O_RDONLY | os.O_DIRECTORY)
                self.procfs_directory_handles[pid] = dir_fd
                cmd.append(f"/proc/{pid}/exe")
            except FileNotFoundError:
                logging.warning("Process %d doesn't exist", pid)

        if not self.procfs_directory_handles:
            raise RuntimeError("No processes to monitor.")

        # Spawn inotifywait with stdout piped to the parent, stdin/stderr → /dev/null
        r, w = os.pipe2(0)  # pylint: disable=no-member
        try:
            self.inotify_pid = os.fork()
        except Exception:
            # Clean up the pipe if fork fails
            os.close(r)
            os.close(w)
            raise

        if self.inotify_pid == 0:
            # Child: connect stdio, then exec
            try:
                os.close(r)

                # stdin -> /dev/null
                fd = os.open("/dev/null", os.O_RDONLY)
                os.dup2(fd, 0)
                os.close(fd)

                # stdout -> pipe write end
                os.dup2(w, 1)
                os.close(w)

                # stderr -> /dev/null
                fd = os.open("/dev/null", os.O_WRONLY)
                os.dup2(fd, 2)
                os.close(fd)

                os.execvp(cmd[0], cmd)
            finally:
                # If exec fails, exit the child process
                os._exit(127)  # noqa: SLF001

        # Parent
        os.close(w)
        self.inotify_pipe = os.fdopen(r)

    # Public API ----------------------------------------------------------------

    def start(self) -> Set[int]:
        """
        Return the PIDs actually being watched (filtered to existing processes).
        """
        return set(self.procfs_directory_handles.keys())

    def wait_next(self) -> Optional[int]:
        """
        Block until one watched PID is confirmed terminated and return it.
        Returns None if there are no more PIDs to watch.
        """
        if not self.procfs_directory_handles:
            return None

        # If we already observed terminations in a previous iteration, return one
        if self.terminated_pids:
            return self._close_next_descriptor()

        try:
            assert self.inotify_pipe is not None
            line = self.inotify_pipe.readline()
            while line:
                # Check which process(es) actually terminated
                self._scan_proc_statuses()

                if self.terminated_pids:
                    return self._close_next_descriptor()

                # Spurious event; read again
                line = self.inotify_pipe.readline()

        except InterruptedError:
            # If user interrupted, ensure we reap inotifywait cleanly before propagating
            self._kill_inotifywait()
            raise

        # EOF or read error: fall back to returning remaining PIDs one by one
        self._kill_inotifywait()
        if self.procfs_directory_handles:
            pid = next(iter(self.procfs_directory_handles))
            os.close(self.procfs_directory_handles[pid])
            del self.procfs_directory_handles[pid]
            return pid

        return None

    def close(self) -> None:
        """
        Release resources and stop inotifywait.
        """
        # Close per-process /proc/<pid> directory handles
        for fd in list(self.procfs_directory_handles.values()):
            try:
                os.close(fd)
            except Exception:
                pass
        self.procfs_directory_handles.clear()

        # Stop inotifywait and close the pipe
        self._kill_inotifywait()

        # Drain any remaining pids just in case
        self.terminated_pids.clear()

    # Internal helpers ----------------------------------------------------------

    def _close_next_descriptor(self) -> int:
        """
        Close the directory FD for one terminated PID, stop inotifywait if none remain,
        and return the PID.
        """
        pid = self.terminated_pids.pop()
        fd = self.procfs_directory_handles.pop(pid, None)
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass

        if not self.procfs_directory_handles:
            self._kill_inotifywait()

        return pid

    def _scan_proc_statuses(self) -> None:
        """
        For each watched PID, try to open /proc/<pid>/status and look for "State:".
        Mark a PID as terminated if:
          - Opening the file fails with ProcessLookupError or FileNotFoundError
          - The "State:" line indicates 'Z' (zombie)
        """
        for pid, dir_fd in list(self.procfs_directory_handles.items()):
            try:
                fd = os.open("status", os.O_RDONLY, dir_fd=dir_fd)
            except (ProcessLookupError, FileNotFoundError):
                # The process is already gone
                self.terminated_pids.add(pid)
                continue

            try:
                # Read a small chunk – "status" is short (a few KB at most)
                data = os.read(fd, 2048)
            finally:
                os.close(fd)

            if self._is_zombie_state(data):
                self.terminated_pids.add(pid)

    @staticmethod
    def _is_zombie_state(status_bytes: bytes) -> bool:
        """
        Return True if the parsed 'State:' line denotes a zombie (Z).
        """
        # Decode as ascii with fallback for safety; 'status' is ASCII-safe
        try:
            text = status_bytes.decode("ascii", errors="ignore")
        except Exception:
            return False

        for line in text.splitlines():
            if line.startswith("State:\t") or line.startswith("State:"):
                # Examples: "State:\tZ (zombie)"
                #           "State:\tS (sleeping)"
                # Be robust to optional whitespace
                after = line.split(":", 1)[1].lstrip()
                return after[:1] == "Z"
        return False

    def _kill_inotifywait(self) -> None:
        """
        Stop and reap the inotifywait helper process.
        """
        # Close the pipe first (so child notices EOF if still running)
        if self.inotify_pipe is not None:
            try:
                self.inotify_pipe.close()
            except Exception:
                pass
            self.inotify_pipe = None

        # Terminate and wait for the child
        if self.inotify_pid is not None:
            try:
                os.kill(self.inotify_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(self.inotify_pid, 0)
            except ChildProcessError:
                pass
            self.inotify_pid = None


__all__ = ["LinuxPidWatcher"]
