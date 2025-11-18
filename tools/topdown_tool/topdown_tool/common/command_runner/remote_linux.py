# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=broad-exception-caught

"""
Remote Linux CommandRunner implementation (via devlib) with FIFO gating.

Semantics match the local Linux runner:
- spawn(command): starts a remote shell that blocks on a named pipe before exec'ing the command.
- start(): writes to the pipe (falling back to SIGCONT when FIFO creation fails) to let the child run.
- wait(): polls the remote /proc/<pid> until exit.
- kill(): sends SIGTERM (and best-effort SIGKILL) on the target, then waits for exit.
"""

import logging
import shlex
import uuid
from pathlib import Path
from typing import List, Optional

from topdown_tool.common.abstractions import ManagedProcess
from topdown_tool.common.devlib_types import Target
from topdown_tool.common.remote_utils import (
    remote_cleanup_target_temp_dirs,
    remote_wait_for_pid_state,
)


class _RemoteLinuxProcess(ManagedProcess):
    def __init__(
        self,
        pid: int,
        target: "Target",
        *,
        as_root: bool = True,
        gate_path: Optional[str] = None,
    ) -> None:
        self._pid = pid
        self._target = target
        self._as_root = as_root
        self._finished = False
        self._pgid: Optional[int] = None
        self._gate_path = gate_path
        self._gate_released = False

    def _cleanup_gate(self) -> None:
        if not self._gate_path:
            return
        try:
            self._target.execute(
                f"rm -f {shlex.quote(self._gate_path)}",
                check_exit_code=True,
                as_root=self._as_root,
            )
        except Exception as exc:
            logging.warning("[remote runner] failed to remove gate %s: %s", self._gate_path, exc)
        finally:
            self._gate_path = None

    def _wait_for_exit(self, timeout: Optional[float] = None) -> bool:
        """Block until the remote PID disappears; return True if it exited within *timeout*."""
        return remote_wait_for_pid_state(
            self._target,
            self._pid,
            present=False,
            timeout=timeout,
            as_root=self._as_root,
            poll_interval=0.1,
        )

    def _send_signal(self, signal_name: str) -> None:
        """Deliver signals in a way that reaches the BusyBox wrapper and real child.

        devlib launches commands through ``busybox sh -lc '…'``. Whether we gate with a
        FIFO or via ``kill -STOP``, the wrapper process is the PID we track and the real
        workload lives in the same process group. Signalling only the wrapper PID would
        leave the actual child untouched, so we resolve the process group once and signal
        it first, falling back to the single PID if group delivery fails.
        """
        if self._pgid is None:
            try:
                out = self._target.execute(
                    f"ps -o pgid= -p {self._pid}",
                    check_exit_code=False,
                    as_root=self._as_root,
                )
                text = out.decode() if isinstance(out, (bytes, bytearray)) else str(out)
                self._pgid = int(text.strip())
            except Exception:
                self._pgid = self._pid

        signal_targets = []
        if self._pgid:
            # Signal the wrapper's process group first so the real workload (forked child)
            # receives SIGCONT/SIGTERM/SIGKILL along with the BusyBox shell wrapper.
            signal_targets.append(f"-- -{self._pgid}")
        signal_targets.append(str(self._pid))

        for signal_spec in signal_targets:
            try:
                self._target.execute(
                    f"kill -{signal_name} {signal_spec}",
                    check_exit_code=False,
                    as_root=self._as_root,
                )
                return
            except Exception:
                continue

    @property
    def pid(self) -> int:
        return self._pid

    def start(self) -> None:
        if self._finished:
            return
        if self._gate_released:
            return

        # Ensure the wrapper is running (devlib's helper devlib-signal-target stops it via SIGSTOP).
        self._send_signal("CONT")

        if self._gate_path:
            try:
                script = f"printf '1\\n' > {shlex.quote(self._gate_path)}"
                self._target.execute(
                    f"sh -lc {shlex.quote(script)}",
                    check_exit_code=True,
                    as_root=self._as_root,
                )
                # The wrapper removes the FIFO after the read completes; avoid double cleanup.
                self._gate_path = None
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning(
                    "[remote runner] failed to release FIFO gate %s: %s",
                    self._gate_path,
                    exc,
                )
                self._cleanup_gate()
                raise RuntimeError("Failed to release remote start gate") from exc

        self._gate_released = True

    def wait(self) -> None:
        if self._finished:
            return
        # Poll until the remote /proc/<pid> disappears
        self._wait_for_exit()
        self._finished = True

    def kill(self) -> None:
        if self._finished:
            return
        # Best-effort graceful then hard kill on target
        self._send_signal("TERM")

        # Wait a bit; if still alive, try KILL
        if self._wait_for_exit(timeout=2.0):
            self._finished = True
            self._cleanup_gate()
            return

        self._send_signal("KILL")

        # Final wait
        self._wait_for_exit(timeout=1.0)
        self._finished = True
        self._cleanup_gate()

    def __del__(self) -> None:
        self._cleanup_gate()
        remote_cleanup_target_temp_dirs(self._target)


class RemoteLinuxCommandRunner:
    """
    Spawn and manage a remote Linux/Android process via devlib, initially STOPPED until start().
    """

    def __init__(self, target: "Target", *, as_root: bool = True) -> None:
        """
        Args:
            target: devlib Target instance (AndroidTarget or LinuxTarget).
            as_root: Run the wrapper with elevated privileges on the target.
        """
        self._target = target
        self._as_root = as_root

    def _has_busybox(self) -> Optional[str]:
        """
        Return busybox path if available on target, else None.
        """
        for path in ("/data/local/tmp/bin/busybox", "busybox"):
            try:
                if hasattr(self._target, "file_exists") and self._target.file_exists(path):
                    return path
                out = self._target.execute(
                    f"sh -lc 'command -v {shlex.quote(path)} || true'",
                    check_exit_code=False,
                    as_root=self._as_root,
                )
                s = out.decode() if isinstance(out, (bytes, bytearray)) else str(out)
                if s.strip():
                    return path
            except Exception:
                continue
        return None

    # pylint: disable=inconsistent-return-statements, duplicate-code
    def spawn(self, command: List[str]) -> ManagedProcess:
        """
        Spawn the command on the target in a STOPPED state and return a ManagedProcess.

        This mirrors local semantics: the process is created but paused via a FIFO
        so probes can be armed before start(). When the target does not support
        FIFO creation we gracefully fall back to SIGSTOP/SIGCONT.
        """
        if not command:
            raise OSError("Invalid command")

        cmd_str = " ".join(shlex.quote(arg) for arg in command)

        # Wrapper: stop the shell immediately, then exec the real command.
        # Prefer busybox sh for consistent behavior on Android.
        bb = self._has_busybox()
        shell = f"{bb} sh" if bb else "sh"
        gate_path: Optional[str] = None
        wrapper_body: Optional[str] = None

        try:
            gate_path = self._create_gate()
        except Exception:
            gate_path = None

        if gate_path:
            wrapper_body = (
                f"kill -STOP $$; "
                f"read _ < {shlex.quote(gate_path)}; "
                f"rm -f {shlex.quote(gate_path)}; "
                f"exec {cmd_str}"
            )
        else:
            wrapper_body = f"kill -STOP $$; exec {cmd_str}"

        wrapper = f"{shell} -lc {shlex.quote(wrapper_body)}"

        try:
            proc = self._target.background(
                wrapper,
                as_root=self._as_root,
            )
            pid = int(getattr(proc, "pid", -1))
            if pid <= 0:
                raise RuntimeError("background() did not provide a valid PID")

            # Optional: wait until the process is visible on /proc
            remote_wait_for_pid_state(
                self._target,
                pid,
                present=True,
                timeout=1.0,
                as_root=self._as_root,
                poll_interval=0.05,
            )

            return _RemoteLinuxProcess(
                pid,
                self._target,
                as_root=self._as_root,
                gate_path=gate_path,
            )

        except Exception as e:
            if gate_path:
                try:
                    self._target.execute(
                        f"rm -f {shlex.quote(gate_path)}",
                        check_exit_code=False,
                        as_root=self._as_root,
                    )
                except Exception:
                    pass
            raise RuntimeError(f"Failed to launch command on target: {e}") from e

    def _create_gate(self) -> Optional[str]:
        """Create a FIFO on the remote target for start gating.

        Returns the path on success, or ``None`` when FIFO creation fails.
        """

        tmp_dir = getattr(self._target, "tmp_directory", "/data/local/tmp")
        gate_path = f"{tmp_dir.rstrip('/')}/topdown_gate_{uuid.uuid4().hex}"
        parent_dir = shlex.quote(str(Path(gate_path).parent))
        try:
            script = (
                f"mkdir -p {parent_dir} && "
                f"rm -f {shlex.quote(gate_path)} && "
                f"mkfifo {shlex.quote(gate_path)}"
            )
            self._target.execute(
                f"sh -lc {shlex.quote(script)}",
                check_exit_code=True,
                as_root=self._as_root,
            )
            return gate_path
        except Exception:
            return None


__all__ = ["RemoteLinuxCommandRunner"]
