# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Run `perf stat` locally on the host system.

This module exposes :class:`LinuxPerf`, the concrete :class:`Perf` implementation for
Linux hosts. The class constructs the local `perf` command line, manages the
subprocess lifecycle, and parses the textual statistics emitted by `perf`.

Limitations:
    * Requires CAP_PERFMON or CAP_SYS_ADMIN or ``kernel.perf_event_paranoid == -1``
      on the host environment.
    * MIDR queries are not supported on Linux (``NotImplementedError``).
"""

import logging
import os
from pathlib import Path
from select import select
import shlex
from signal import SIGINT
from subprocess import DEVNULL, PIPE, Popen
from typing import Optional, Sequence, Tuple

from topdown_tool.perf.linux_perf_base import LinuxPerfBase
from topdown_tool.perf.perf import Perf, PerfEventGroup


class LinuxPerf(LinuxPerfBase):
    """Orchestrate local ``perf stat`` runs.

    Instances of this class build `perf` command lines, launch the local
    subprocess, and expose structured results to the probes.
    """

    class _Recorder(LinuxPerfBase._Recorder):  # pylint: disable=protected-access
        """Manage a local ``perf stat`` subprocess."""

        # pylint: disable=too-many-branches, too-many-arguments, too-many-positional-arguments
        def __init__(
            self,
            events: Sequence[PerfEventGroup],
            cli_filename: Path,
            output_filename: str,
            cores: Optional[Sequence[int]],
            perf_args: Optional[str],
            interval: Optional[int],
            pid: Optional[int],
            timeout: Optional[int],
        ) -> None:
            """Initialise the local recorder.

            Args:
                events: Event groups assigned to the recorder.
                cli_filename: File that receives the fully quoted command line.
                output_filename: Local path where ``perf`` writes statistics.
                cores: Optional list of CPU IDs passed via ``-C``.
                perf_args: Extra ``perf`` command-line arguments from the user.
                interval: Optional sampling interval (milliseconds) mapped to ``-I``.
                pid: Optional PID passed through ``-p`` for task-scoped counting.
            """
            super().__init__(events=events, output_filename=output_filename)
            self._cli_filename = cli_filename
            self._perf_args = perf_args
            self._interval = interval
            self._process: Optional[Popen] = None

            cmd = LinuxPerfBase._compose_stat_command(
                LinuxPerf._perf_path,
                self._output_filename,
                cores=cores,
                pid=pid,
                interval=self._interval,
                timeout=timeout,
            )
            cmd.extend(["-e", Perf.build_event_string(self._events)])

            # Start with events disabled.
            # Create control and acknowledgement pipes
            # The control pipe will be used to enable events,
            # while the acknowledgement pipe will be used to resume Arm Top-Down tool.
            self._ctl_pipe: Optional[Tuple[int, int]] = os.pipe2(0)
            self._ack_pipe: Optional[Tuple[int, int]] = os.pipe2(0)
            os.set_blocking(self._ack_pipe[0], False)

            cmd.extend(
                ["--delay", "-1", "--control", f"fd:{self._ctl_pipe[0]},{self._ack_pipe[1]}"]
            )
            self._command = cmd
            self._control_index = len(self._command) - 1
            # Add additional user defined arguments if needed
            if perf_args:
                self._command += shlex.split(perf_args)
            # Empty file for measurements
            LinuxPerfBase._initialize_output_file(self._output_filename)

        def start(self) -> None:
            """Launch the local ``perf stat`` process with the prepared arguments."""
            if self._events is None:
                logging.info("Empty run with no events")
                return

            assert (
                isinstance(self._ctl_pipe, tuple)
                and isinstance(self._ack_pipe, tuple)
                or self._ctl_pipe is None
                and self._ack_pipe is None
            )
            if self._ctl_pipe is None or self._ack_pipe is None:
                self._ctl_pipe = os.pipe2(0)
                self._ack_pipe = os.pipe2(0)
                self._command[self._control_index] = f"fd:{self._ctl_pipe[0]},{self._ack_pipe[1]}"

            # pylint: disable=protected-access
            LinuxPerfBase.write_cli_command(self._cli_filename, self._command)

            self._process = Popen(  # pylint: disable=consider-using-with
                self._command,
                stderr=DEVNULL,
                close_fds=True,
                pass_fds=(self._ctl_pipe[0], self._ack_pipe[1]),
            )

            logging.info('Running "%s"', " ".join(shlex.quote(arg) for arg in self._command))

            os.close(self._ctl_pipe[0])
            os.close(self._ack_pipe[1])

            # Try to enable perf after it creates the events.
            msg = b"enable"
            if os.write(self._ctl_pipe[1], msg) != len(msg):
                os.close(self._ctl_pipe[1])
                os.close(self._ack_pipe[0])
                raise RuntimeError("Perf version not supported. Control pipe closed by perf.")

            # Wait for acknowledgement from perf to ensure events are ready.
            readable_fds, _, _ = select((self._ack_pipe[0],), (), (), 2.0)
            if self._ack_pipe[0] not in readable_fds:
                os.close(self._ack_pipe[0])
                raise RuntimeError("Perf version not supported. Perf didn't acknowledge.")
            expected_msg = b"ack\n\0"
            if os.read(self._ack_pipe[0], len(expected_msg)) != expected_msg:
                os.close(self._ack_pipe[0])
                raise RuntimeError(
                    "Perf version not supported. Unexpected acknowledgement message."
                )

        def stop(self) -> None:
            """Stop the local ``perf`` process by sending ``SIGINT``."""
            if self._events is None or self._process is None:
                return
            self._process.send_signal(SIGINT)

        def wait(self) -> None:
            """Wait for the local ``perf`` process to exit and close pipes."""
            if self._events is None or self._process is None:
                return
            self._process.wait()
            self._process = None
            assert isinstance(self._ctl_pipe, tuple) and isinstance(self._ack_pipe, tuple)
            os.close(self._ctl_pipe[1])
            os.close(self._ack_pipe[0])
            self._ctl_pipe = None
            self._ack_pipe = None

    @staticmethod
    def have_perf_privilege() -> bool:
        """Return whether unrestricted ``perf`` access is available on the host.

        Returns:
            bool: ``True`` if the host satisfies the privilege checks; otherwise ``False``.
        """
        return LinuxPerf._has_local_privileges()

    @staticmethod
    def _has_local_privileges() -> bool:
        """Return whether the host satisfies the standard perf privilege checks.

        Returns:
            bool: ``True`` if the checks indicate sufficient privilege; otherwise ``False``.
        """
        paranoid_value: Optional[str] = None
        status_value: Optional[str] = None

        try:
            with open("/proc/sys/kernel/perf_event_paranoid", encoding="ascii") as file_obj:
                paranoid_value = file_obj.read()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning("Could not read perf_event_paranoid: %s", exc)

        try:
            with open("/proc/self/status", encoding="ascii") as file_obj:
                status_value = file_obj.read()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning("Could not read capabilities from /proc/self/status: %s", exc)

        return LinuxPerfBase._has_privilege_from_values(paranoid_value, status_value)

    def _create_recorder(  # pylint: disable=too-many-arguments
        self,
        *,
        index: int,  # pylint: disable=unused-argument
        events: Sequence[PerfEventGroup],
        cli_path: Path,
        output_basename: str,
        pid: Optional[int],
        timeout: Optional[int],
    ) -> LinuxPerfBase._Recorder:  # pylint: disable=protected-access
        return self._Recorder(
            events=events,
            cli_filename=cli_path,
            output_filename=output_basename,
            cores=self._cores,
            perf_args=self._perf_args,
            interval=self._interval,
            pid=pid,
            timeout=timeout,
        )

    @classmethod
    def get_pmu_counters(cls, core: int) -> int:
        """Determine the number of concurrently measurable PMU counters on ``core``."""

        def runner(event: str, sample_count: int) -> Sequence[str]:
            cmdline = LinuxPerfBase._build_pmu_probe_command(
                cls._perf_path,
                event,
                sample_count,
                core,
            )
            with Popen(cmdline, stdin=DEVNULL, stdout=DEVNULL, stderr=PIPE, text=True) as process:
                return [line for line in process.communicate()[1].strip().splitlines() if line]

        def check_pmu_availability(count: int) -> bool:
            """Return ``True`` when ``count`` events can be scheduled concurrently."""
            result = cls._probe_pmu_count(count=count, runner=runner)
            if result is None:
                raise RuntimeError(
                    f"Failed to check PMU availability with perf. Expected {count} lines in perf stderr"
                )
            return result

        pmu_max = LinuxPerfBase._binary_search_pmu_max(check_pmu_availability)
        logging.info("Detected %d PMU counters on core %d", pmu_max, core)
        return pmu_max
