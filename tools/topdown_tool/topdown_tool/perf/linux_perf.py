# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Linux-specific implementation of the Perf interface using the `perf` tool.

This module defines the `LinuxPerf` class, which implements the abstract `Perf` interface for recording
hardware performance counters using the Linux `perf` command-line tool.

It manages event grouping, spawns multiple `perf stat` processes if needed, parses their output,
and aggregates results for each core or uncore unit.

Limitations:
    - Requires CAP_PERFMON or CAP_SYS_ADMIN or kernel.perf_event_paranoid == -1
    - MIDR queries are not supported on Linux (NotImplementedError)
"""

import itertools
import logging
from pathlib import Path
from subprocess import Popen, PIPE, DEVNULL
from signal import SIGINT
import shlex
from typing import List, Optional, Sequence, Tuple, final

from topdown_tool.perf.perf import (
    Perf,
    PerfEvent,
    PerfEventGroup,
    PerfEventCount,
    PerfRecordLocation,
    PerfTimedResults,
    PerfResults,
    PerfRecords,
    Cpu,
    Uncore,
)

# pylint: disable=duplicate-code

_PERF_SEPARATOR: str = ";"


class LinuxPerf(Perf):
    """
    Linux-specific Perf implementation using the `perf` command-line tool for collecting event statistics.

    This class builds and executes `perf stat` commands for each set of grouped performance events,
    manages per-core recording, and aggregates textual output into structured results.

    Supports optional interval-based sampling and can handle platform-specific permission checks.
    """

    _perf_path: str = "perf"

    @staticmethod
    def have_perf_privilege() -> bool:
        """
        Determine whether the current process has sufficient privileges to access all perf events.

        This includes:
            - `perf_event_paranoid` set to -1
            - CAP_PERFMON or CAP_SYS_ADMIN

        Returns:
            True if perf privilege is granted, otherwise False.
        """

        try:
            with open("/proc/sys/kernel/perf_event_paranoid", encoding="ascii") as f:
                if int(f.read().strip()) == -1:
                    return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Could not read perf_event_paranoid: %s", e)

        try:
            with open("/proc/self/status", encoding="ascii") as f:
                for line in f:
                    if line.startswith("CapEff:"):
                        eff_caps = int(line.split()[1], 16)
                        if (eff_caps & (1 << 38)) or (eff_caps & (1 << 21)):
                            return True
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.warning("Could not read capabilities from /proc/self/status: %s", e)

        return False

    class _Recorder:
        # The Recorder class functions as a partner to the Perf class when the volume of
        # performance events exceeds system limits such as file descriptors or command line
        # argument length.
        #
        # The Perf class uses this class to divide the event capture load across multiple
        # perf instances.
        #
        # It builds the perf command line, starts the event capture process, stops it as requested,
        # and waits until the process completes.
        # pylint: disable=too-many-branches, too-many-arguments, too-many-positional-arguments
        def __init__(
            self,
            events: Sequence[PerfEventGroup],
            cli_filename: Path,
            output_filename: str,
            cores: Optional[Sequence[int]],
            perf_path: str,
            perf_args: Optional[str],
            interval: Optional[int],
        ):
            self._perf_path = perf_path
            self._perf_args = perf_args
            self._interval = interval
            self._events = events
            self._flat_events = list(itertools.chain.from_iterable(events))
            self._cli_filename = cli_filename
            self._output_filename = output_filename
            self._process: Optional[Popen] = None

            self._cmd = [
                self._perf_path,
                "stat",
                "-x",
                _PERF_SEPARATOR,
                "-o",
                self._output_filename,
            ]

            # Add events
            self._cmd.extend(["-e", LinuxPerf._build_event_string(self._events)])

            # Add cores argument if needed
            if cores is not None:
                self._cmd.extend(["--per-core", "-C", ",".join(map(str, cores))])

            # Add interval argument if needed
            if self._interval is not None:
                self._cmd.extend(["-I", str(self._interval)])

            # Add additional user defined arguments if needed
            if self._perf_args:
                self._cmd += shlex.split(self._perf_args)

            # Empty file for measurements
            LinuxPerf._initialize_output_file(self._output_filename)

        @property
        def events(self) -> Sequence[PerfEventGroup]:
            return self._events

        @property
        def flat_events(self) -> List[PerfEvent]:
            return self._flat_events

        @property
        def output_filename(self) -> str:
            return self._output_filename

        def start(self) -> None:
            """
            Start performance data recording by launching one or more `perf stat` processes.
            """
            if self._events is None:
                logging.info("Empty run with no events")
                return

            # pylint: disable=protected-access
            LinuxPerf._write_cli_command(
                self._cli_filename, self._cmd
            )  # pylint: disable=protected-access
            self._process = Popen(self._cmd)  # pylint: disable=consider-using-with
            logging.info('Running "%s"', " ".join(shlex.quote(arg) for arg in self._cmd))

        def stop(self) -> None:
            """
            Stop all active `perf` subprocesses by sending SIGINT.
            """
            assert self._process is not None
            if self._events is None:
                return
            self._process.send_signal(SIGINT)

        def wait(self) -> None:
            if self._events is None or self._process is None:
                return
            self._process.wait()
            self._process = None

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        cores: Optional[Sequence[int]] = None,
        *,
        perf_path: Optional[str] = None,
        perf_args: Optional[str] = None,
        interval: Optional[int] = None,
    ):
        """
        Initialize a LinuxPerf instance and prepare one or more Recorder objects.

        Args:
            events_groups: A list of event groups (tuples of PerfEvent) to record.
            output_filename: Base filename for output results.
            cores: Optional list of core indices to record events on.
            perf_path: Optional path override for the `perf` binary.
            perf_args: Additional user-provided command-line arguments for `perf`.
            interval: Optional sampling interval in milliseconds.
        """
        self._perf_path = perf_path or "perf"
        self._perf_args = perf_args
        self._interval = interval
        self._events_groups = events_groups
        self._flat_events: List[PerfEvent] = list(
            itertools.chain.from_iterable(self._events_groups)
        )
        self._output_filename = output_filename
        self._cores = tuple(sorted(cores)) if cores else None
        self._output_path = Path(output_filename).parent
        self._recorders: List[LinuxPerf._Recorder] = []

        for i, event_groups in enumerate(self._extract_recorders_events(self._events_groups)):
            recorder = self._Recorder(
                events=event_groups,
                cli_filename=self._output_path / f"perf-cli-{i}",
                output_filename=f"{self._output_filename}-{i}",
                cores=self._cores,
                perf_path=self._perf_path,
                perf_args=self._perf_args,
                interval=self._interval,
            )
            self._recorders.append(recorder)

    @property
    def max_event_count(self) -> int:
        return 1000

    def start(self) -> None:
        """Starts performance event recording."""
        for r in self._recorders:
            r.start()

    def stop(self) -> None:
        """Stops performance event recording."""
        for r in self._recorders:
            r.stop()

    # pylint: disable=too-many-locals
    def get_perf_result(self) -> PerfRecords:
        """
        Wait for all perf subprocesses to complete and aggregate their outputs.

        Returns:
            A PerfRecords object containing the recorded results for each core (or uncore) and timestamp.
        """
        locations: List[PerfRecordLocation] = (
            [Cpu(core) for core in self._cores] if self._cores is not None else [Uncore()]
        )
        records: PerfRecords = PerfRecords({loc: PerfTimedResults() for loc in locations})

        for recorder in self._recorders:
            recorder.wait()

            if recorder.events is None:
                continue

            output = self._read_perf_stat_output(recorder.output_filename)

            # Sanity check
            assert len(output) % len(recorder.flat_events) == 0

            # We need to map each recorder.events to their value
            i = 0
            while i < len(output):
                # Compute the CPU index as next iteration is for a full range of its values
                location: PerfRecordLocation
                if self._cores:
                    cpu_index = i // len(recorder.flat_events) % len(self._cores)
                    location = Cpu(self._cores[cpu_index])
                else:
                    location = Uncore()

                for event_group in recorder.events:
                    step = len(event_group)
                    values = output[i : i + step]
                    i = i + step
                    base_time = values[0][2]
                    # Sanity check, all the time should be equal and the name must match
                    for idx, (event_name, _value, t) in enumerate(values):
                        assert t == base_time
                        assert event_name == event_group[idx].perf_name()

                    records[location].setdefault(base_time, PerfResults())
                    assert records[location][base_time].get(tuple(event_group)) is None
                    records[location][base_time][tuple(event_group)] = tuple(v[1] for v in values)

        return records

    def _read_perf_stat_output(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        """
        Parse raw perf output from a perf stat file.

        Args:
            filename: Path to a perf output file.

        Returns:
            List of (event name, value, timestamp) tuples.
        """
        with open(filename, encoding="utf-8") as f:
            return [
                self._parse_perf_line(line)
                for line in f.read().splitlines()
                if line and not line.startswith("#")
            ]

    def _parse_perf_line(self, line: str) -> Tuple[str, Optional[float], Optional[float]]:
        """
        Parse a single line from perf output and extract the event name, value, and time (if present).

        Returns:
            Tuple containing (event name, value, time).
        """
        if self._interval is not None:
            if self._cores is None:
                # e.g. 0.100116703;178;;ITLB_WALK;96758700;100.00;;
                time_str, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            else:
                time_str, _, _, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            time = float(time_str)
        else:
            # e.g. 139198,,BR_PRED:u,800440,100.00,,
            if self._cores is None:
                count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            else:
                _, _, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            time = None

        if count_str == "<not counted>":
            logging.info("Perf event %s was not counted", event)
        elif count_str == "<not supported>":
            logging.info("Perf event %s was not supported.", event)
        if count_str == "0":
            logging.info("Perf counted 0 %s events", event)

        count = None if count_str in ("<not counted>", "<not supported>") else float(count_str)
        return self._strip_modifier(event), count, time

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def _create_event_count(
        self,
        r: _Recorder,
        index: int,
        name: str,
        value: Optional[float],
        time: Optional[float],
    ) -> PerfEventCount:
        """
        Create a PerfEventCount object from the recorder data and a flat event index.

        Args:
            r: The recorder the data came from.
            index: Flat index of the event.
            name: Name of the event.
            value: Measured value.
            time: Timestamp.

        Returns:
            PerfEventCount object for the parsed data.
        """
        event = r.flat_events[index % len(r.flat_events)]
        assert name == event.perf_name()
        return PerfEventCount(event=event, value=value, time=time)

    @staticmethod
    def get_pmu_counters(core: int, perf_path: str = "perf") -> int:
        """
        Determine the number of concurrently measurable PMU counters on a given core.

        Performs binary search using `perf stat` and synthetic events.

        Args:
            core: The core to test.
            perf_path: Path to the perf binary.

        Returns:
            The maximum number of hardware counters available on the given core.
        """

        def check_pmu_availability(core: int, count: int) -> bool:
            cmdline = [
                perf_path,
                "stat",
                "-e",
                "{" + ",".join(["instructions:u"] * count) + "}",
                "-C",
                str(core),
                "-x",
                "\\t",
                "echo",
                "0",
            ]
            with Popen(cmdline, stdin=DEVNULL, stdout=DEVNULL, stderr=PIPE, text=True) as process:
                stderr = process.communicate()[1]
                for line in stderr.splitlines():
                    row = line.split("\t")
                    if row[0] in {"<not counted>", "<not supported>"} or float(row[4]) != 100.0:
                        return False
            return True

        pmu_min = 0
        pmu_max = 31
        while pmu_min != pmu_max:
            pmu_attempt = (pmu_min + pmu_max + 1) // 2
            if check_pmu_availability(core, pmu_attempt):
                pmu_min = pmu_attempt
            else:
                pmu_max = pmu_attempt - 1
        return pmu_min

    @staticmethod
    def get_midr_value(core: int, perf_path: str = "perf") -> int:
        """
        Not supported on Linux. Always raises NotImplementedError.

        Raises:
            NotImplementedError
        """
        raise NotImplementedError("MIDR value is not supported on Linux")

    @staticmethod
    @final
    def _write_cli_command(path: Path, cmd: List[str]) -> None:
        """Write the perf command line to a CLI log file (Linux)."""
        cmd_line = " ".join(shlex.quote(arg) for arg in cmd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(cmd_line)
