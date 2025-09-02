# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Windows-specific implementation of the Perf interface using `wperf`.

This module defines the `WindowsPerf` class, which implements the abstract `Perf` interface for collecting
hardware performance data on Windows using Arm's `wperf` tool. It handles launching `wperf`, parsing JSON output,
handling signal termination, and aggregating results across CPU cores.

The implementation supports per-core recording, grouped events, and interval-based sampling.

Note:
    This is only functional on Windows (`sys.platform == "win32"`), and will not execute elsewhere.
"""
import sys
import itertools
import logging
import shlex
import subprocess
from pathlib import Path
from subprocess import Popen, PIPE, run
from signal import SIGINT, signal
from json import load, loads
from typing import Any, Dict, List, Optional, Sequence, Tuple, Callable, Union, final
from types import FrameType

# pylint: disable=duplicate-code

if sys.platform == "win32":
    # pylint: disable=import-error, no-name-in-module, ungrouped-imports
    from signal import (
        CTRL_C_EVENT,
    )
else:
    CTRL_C_EVENT = 0  # type: ignore

from topdown_tool.perf.perf import (
    Perf,
    PerfEvent,
    PerfEventGroup,
    PerfRecordLocation,
    PerfTimedResults,
    PerfResults,
    PerfRecords,
    Cpu,
    Uncore,
)


class WindowsPerf(Perf):
    """
    Windows-specific Perf implementation using `wperf` for collecting performance event statistics.

    This class builds `wperf` command lines for each group of events, starts one or more subprocesses to
    collect data, and aggregates the JSON output. It supports optional interval-based sampling and
    per-core filtering.

    Attributes:
        _perf_path: Path to the `wperf` binary.
        _wperf_test_json: Cached result of `wperf test --json`.
        _wperf_cpuinfo_cache: Cached result of `wperf cpuinfo` mapping core -> MIDR.
    """

    _wperf_test_json: Optional[Dict] = None
    _wperf_cpuinfo_cache: Optional[Dict[int, int]] = None
    _perf_path: str = "wperf"

    @staticmethod
    def have_perf_privilege() -> bool:
        """Return True since Windows doesn't use Linux-specific perf_event_paranoid."""
        return True

    @staticmethod
    def signal_handler_windows(signum: Any, frame: Any) -> Any:
        pass

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
            self._previous_signal_handler: Union[
                Callable[[int, Optional[FrameType]], Any], int, None
            ] = None

            self._cmd = [self._perf_path, "stat", "--json"]
            # Add output filename
            self._cmd.extend(["-o", self._output_filename])

            # Add events
            self._cmd.extend(["-e", WindowsPerf._build_event_string(self._events)])

            # Add cores argument if needed
            if cores is not None:
                self._cmd.append("-c")
                self._cmd.append(",".join(map(str, cores)))

            # Add interval argument if needed
            if self._interval is not None:
                self._cmd.extend(["-t", "-i", "0", "--timeout", str(self._interval) + "ms"])

            # Add additional user defined arguments if needed
            if self._perf_args:
                self._cmd += shlex.split(self._perf_args)

            # Empty file for measurements
            WindowsPerf._initialize_output_file(self._output_filename)

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
            if self._events is None:
                logging.info("Empty run with no events")
                return
            # pylint: disable=protected-access
            WindowsPerf._write_cli_command(self._cli_filename, self._cmd)
            self._process = Popen(self._cmd)  # pylint: disable=consider-using-with
            logging.info('Running "%s"', " ".join(shlex.quote(arg) for arg in self._cmd))

        def stop(self) -> None:
            assert self._process is not None
            if self._events is None:
                return
            self._previous_signal_handler = signal(SIGINT, WindowsPerf.signal_handler_windows)
            self._process.send_signal(CTRL_C_EVENT)

        def wait(self) -> None:
            if self._events is None or self._process is None:
                return
            self._process.wait()
            self._process = None
            signal(SIGINT, self._previous_signal_handler)

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
        Initialize the WindowsPerf instance and prepare Recorder instances for each group of events.

        Args:
            events_groups: Grouped performance events to record.
            output_filename: Base name for output files.
            cores: Optional list of core indices to record.
            perf_path: Optional override path to `wperf` binary.
            perf_args: Optional additional command-line flags for `wperf`.
            interval: Optional sampling interval (in milliseconds).
        """
        self._perf_path = perf_path or "wperf"
        self._perf_args = perf_args
        self._interval = interval
        self._events_groups = events_groups
        self._flat_events: List[PerfEvent] = list(
            itertools.chain.from_iterable(self._events_groups)
        )
        self._output_filename = output_filename
        self._cores = tuple(sorted(cores)) if cores is not None else None
        self._output_path = Path(output_filename).parent
        self._recorders: List[WindowsPerf._Recorder] = []

        recorders_events = self._extract_recorders_events(self._events_groups)
        for i, event_groups in enumerate(recorders_events):
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
        """
        Start all Recorder instances to begin collecting performance data via `wperf`.
        """
        for r in self._recorders:
            r.start()

    def stop(self) -> None:
        """
        Stop all active Recorder instances gracefully.
        Sends SIGINT or CTRL_C_EVENT depending on platform.
        """
        for r in self._recorders:
            r.stop()

    # pylint: disable=too-many-locals
    def get_perf_result(self) -> PerfRecords:
        """
        Wait for all recorders to finish, parse and aggregate the JSON output.

        Returns:
            A PerfRecords object mapping each recording location (core or Uncore) to its event results over time.
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
                        # Compare numeric values: parsed "0x22" vs expected "r22"
                        parsed_code = int(event_name, 0)
                        expected_code = int(event_group[idx].perf_name()[1:], 16)  # strip 'r'
                        assert (
                            parsed_code == expected_code
                        ), f"Windows event mismatch: parsed='{event_name}', expected='{event_group[idx].perf_name()}'"
                    records[location].setdefault(base_time, PerfResults())
                    assert records[location][base_time].get(tuple(event_group)) is None
                    records[location][base_time][tuple(event_group)] = tuple(v[1] for v in values)

        return records

    def _read_perf_stat_output(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        """
        Parse the JSON output file generated by `wperf stat --json`.

        Args:
            filename: Path to the output JSON file.

        Returns:
            A list of tuples (event_idx, counter_value, timestamp) per recorded event.
        """
        results: List[Tuple[str, Optional[float], Optional[float]]] = []
        with open(filename, encoding="utf-8") as f:
            json = load(f)
        if self._interval:
            current_time = 0.0
            for timed_result in json["timeline"]:
                current_time += timed_result["Time_elapsed"]
                for timed_result_for_core in timed_result["core"]["cores"]:
                    for event in timed_result_for_core["Performance_counter"][1:]:
                        results.append((event["event_idx"], event["counter_value"], current_time))
        else:
            for result_for_core in json["core"]["cores"]:
                for event in result_for_core["Performance_counter"][1:]:
                    results.append((event["event_idx"], event["counter_value"], None))
        return results

    @staticmethod
    def get_pmu_counters(core: int, perf_path: str = "wperf") -> int:
        """
        Query the number of available PMU counters via `wperf test`.

        Args:
            core: The core index (ignored in practice).
            perf_path: Path to the `wperf` binary.

        Returns:
            Number of general-purpose counters supported by the hardware.
        """
        return int(WindowsPerf._wperf_test(perf_path)["PMU_CTL_QUERY_HW_CFG [gpc_num]"], 0)

    @staticmethod
    def _wperf_test(perf_path: str) -> Dict[str, str]:
        """
        Run `wperf test --json` and parse the test results.

        Args:
            perf_path: Path to the `wperf` binary.

        Returns:
            Dictionary mapping test names to their results.
        """
        if WindowsPerf._wperf_test_json is None:
            result = run([perf_path, "test", "--json"], stdout=PIPE, check=True)
            # {
            #   "Test_Results": [
            #     ...
            #     {
            #       "Result": "0x000000000000413fd0c1",
            #       "Test_Name": "PMU_CTL_QUERY_HW_CFG [midr_value]"
            #     },
            #     ...
            #   ]
            # }
            WindowsPerf._wperf_test_json = {
                item["Test_Name"]: item["Result"]
                for item in loads(result.stdout.decode("utf-8"))["Test_Results"]
            }
        return WindowsPerf._wperf_test_json

    @staticmethod
    def _wperf_cpuinfo(perf_path: str) -> Dict[int, int]:
        """
        Run `wperf cpuinfo` and extract MIDR values per core.

        Args:
            perf_path: Path to the `wperf` binary.

        Returns:
            Mapping of core index to MIDR_EL1 integer.
        """
        if WindowsPerf._wperf_cpuinfo_cache is None:
            result = run([perf_path, "cpuinfo"], stdout=PIPE, check=True, text=True)
            midr_map = {}
            for line in result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("Core") or line.startswith("===="):
                    continue
                parts = line.split()
                if len(parts) >= 6:
                    core_id = int(parts[0])
                    midr = int(parts[-1], 16)  # MIDR_EL1 is the last column
                    midr_map[core_id] = midr
            WindowsPerf._wperf_cpuinfo_cache = midr_map
        return WindowsPerf._wperf_cpuinfo_cache

    @staticmethod
    def get_midr_value(core: int, perf_path: str = "wperf") -> int:
        """
        Return the MIDR_EL1 value for a specific core using cached `wperf cpuinfo`.

        Args:
            core: Core ID to look up.
            perf_path: Path to the `wperf` binary.

        Returns:
            MIDR value for the specified core.

        Raises:
            KeyError: If the core is not present in the CPU info.
        """
        cpuinfo = WindowsPerf._wperf_cpuinfo(perf_path)
        if core not in cpuinfo:
            raise KeyError(f"No MIDR entry found for core {core}")
        return cpuinfo[core]

    @staticmethod
    @final
    def _write_cli_command(path: Path, cmd: List[str]) -> None:
        """Write the perf command line to a CLI log file (Windows)."""
        cmd_line = subprocess.list2cmdline(cmd)
        with open(path, "w", encoding="utf-8") as f:
            f.write(cmd_line)
