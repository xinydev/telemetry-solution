# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Module for capturing hardware performance events.

This module provides a public interface to record hardware performance events using platform-specific
tools (Linux perf or Windows wperf). The main entry point is the Perf class. Users configure the recording
with command line arguments via add_cli_arguments and process_cli_arguments, then instantiate Perf with the
desired event groups and optionally specify cores. Recording is started and stopped with start() and stop(),
and captured results can be retrieved with get_perf_result().

Usage example:
    parser = argparse.ArgumentParser(...)
    Perf.add_cli_arguments(parser.add_argument_group("perf"))
    args = parser.parse_args()
    Perf.process_cli_arguments(args)
    perf_instance = Perf(events_groups, output_filename, cores)
    perf_instance.start()
    ...  # run workload
    perf_instance.stop()
    results = perf_instance.get_perf_result()
"""

import argparse
import itertools
import logging
from pathlib import Path
import shlex
import sys
from json import load, loads
from signal import SIGINT, signal
from subprocess import DEVNULL, PIPE, Popen, run
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Set,
    Tuple,
    Sequence,
    TypeVar,
)
from dataclasses import dataclass


if sys.platform == "win32":
    from signal import CTRL_C_EVENT  # pylint: disable=no-name-in-module, ungrouped-imports

E_contra = TypeVar("E_contra", contravariant=True)


class PerfEvent(Protocol[E_contra]):
    """Interface representing a performance event.

    Implementers should provide the perf_name() to return the event's identifier. The type must be
    hashable to be used as a key in dictionaries and orderable so that it can be sorted.

    Attributes:
        name (str): The name of the performance event.
    """

    name: str

    def perf_name(self) -> str: ...
    def __lt__(self, other: E_contra) -> bool: ...


PerfEventGroup = Tuple[PerfEvent, ...]


@dataclass
class PerfEventCount:
    """Data class representing the count result of a performance event.

    Attributes:
        event: The performance event.
        value: The recorded value, if available.
        time: The timestamp of the recording, if applicable.
    """

    event: PerfEvent
    value: Optional[float] = None
    time: Optional[float] = None


@dataclass(frozen=True)
class PerfRecordLocation:
    """Base data class for a recording location (e.g. a CPU core or uncore component)."""


@dataclass(frozen=True)
class Uncore(PerfRecordLocation):
    """Data class representing uncore performance monitoring units."""


@dataclass(frozen=True, order=True)
class Cpu(PerfRecordLocation):
    """Data class representing a CPU core for performance measurement.

    Attributes:
        id: The CPU core identifier.
    """

    id: int


# Each tuple of Event(s) is uniquely mapped to a tuple of captured float values.
class PerfResults(Dict[Tuple[PerfEvent, ...], Tuple[Optional[float], ...]]):
    """Mapping from a tuple of performance events to their corresponding recorded float values."""


# For each location, a set of results with their timestamp.
class PerfTimedResults(Dict[Optional[float], PerfResults]):
    """Mapping from a timestamp (or None) to performance results."""


# Global performance mapping for all locations.
class PerfRecords(Dict[PerfRecordLocation, PerfTimedResults]):
    """Mapping from performance recording locations to their timed results."""


# pylint: disable=protected-access
class Perf:
    """Main class for capturing hardware performance events.

    Uses Linux perf or Windows wperf based on the execution platform. Configuration is provided
    via CLI arguments and recording is performed in logical groups across cores if specified.
    """

    _PERF_SEPARATOR: str = ";"
    _MAX_EVENT_COUNT: int = 1000

    _perf_path: str = "wperf" if sys.platform == "win32" else "perf"
    _perf_args: Optional[str] = None
    _interval: Optional[int] = None
    _wperf_test_json: Optional[Dict] = None

    @staticmethod
    def have_perf_privilege() -> bool:
        """
        Return True if the user can use all perf events (paranoid=-1 or has CAP_PERFMON or CAP_SYS_ADMIN).
        Works on Linux only.
        """
        if sys.platform != "linux":
            return True  # Not applicable

        try:
            with open("/proc/sys/kernel/perf_event_paranoid", encoding="ascii") as f:
                value = int(f.read().strip())
                if value == -1:
                    return True
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # If can't read, continue to capability check

        # Check CAP_PERFMON (bit 38) and CAP_SYS_ADMIN (bit 21) in /proc/self/status
        try:
            with open("/proc/self/status", encoding="ascii") as f:
                for line in f:
                    if line.startswith("CapEff:"):
                        eff_caps = int(line.split()[1], 16)
                        if (eff_caps & (1 << 38)) != 0 or (eff_caps & (1 << 21)) != 0:
                            return True
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        return False

    @staticmethod
    def add_cli_arguments(argument_group: argparse._ArgumentGroup) -> None:
        """Adds command line arguments for configuring performance event recording.

        Args:
            argument_group: The argparse argument group to which perf-related arguments will be added.
        """
        argument_group.add_argument("--perf-path", type=str, help="Path to perf executable")
        argument_group.add_argument(
            "--perf-args",
            type=str,
            help="Additional command line arguments to pass to Perf",
        )
        argument_group.add_argument(
            "--interval",
            "-I",
            "-i",
            type=int,
            help="Collect/output data every <interval> milliseconds",
        )

    @staticmethod
    def process_cli_arguments(args: argparse.Namespace) -> None:
        """Processes command line arguments and updates the Perf configuration.

        Args:
            args: Parsed command line arguments.
        """
        if args.perf_path is not None:
            Perf._perf_path = args.perf_path
        Perf._perf_args = args.perf_args
        Perf._interval = args.interval

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
        # pylint: disable=too-many-branches
        def __init__(
            self,
            events: Sequence[PerfEventGroup],
            cli_filename: Path,
            output_filename: str,
            cores: Optional[Sequence[int]],
        ):
            self._events = events
            self._flat_events = list(itertools.chain.from_iterable(events))
            self._cli_filename = cli_filename
            self._output_filename = output_filename
            self._process: Optional[Popen] = None
            self._previous_signal_handler = None

            self._cmd = [Perf._perf_path, "stat"]

            # Add columns separator on Linux
            if sys.platform == "linux":
                self._cmd.extend(["-x", Perf._PERF_SEPARATOR])

            # Specify output format on Windows
            if sys.platform == "win32":
                self._cmd.append("--json")

            # Add output filename
            self._cmd.extend(["-o", self._output_filename])

            # Add events
            events_string = ""
            for group in self._events:
                if events_string:
                    events_string += ","
                if len(group) > 1:
                    events_string += "{" + ",".join(e.perf_name() for e in group) + "}"
                else:
                    events_string += next(iter(group)).perf_name()
            self._cmd.extend(["-e", events_string])

            # Add cores argument if needed
            if cores is not None:
                if sys.platform == "linux":
                    self._cmd.extend(["--per-core", "-C"])
                elif sys.platform == "win32":
                    self._cmd.append("-c")
                self._cmd.append(",".join(map(str, cores)))

            # Add interval argument if needed
            if Perf._interval is not None:
                if sys.platform == "linux":
                    self._cmd.extend(["-I", str(Perf._interval)])
                elif sys.platform == "win32":
                    self._cmd.extend(["-t", "-i", "0", "--timeout", str(Perf._interval) + "ms"])

            # Add additional user defined arguments if needed
            if Perf._perf_args:
                self._cmd += shlex.split(Perf._perf_args)

            # Empty file for measurements
            with open(f"{self._output_filename}", "w", encoding="utf-8"):
                pass

        def start(self) -> None:
            if self._events is None:
                logging.info("Empty run with no events")
                return

            with open(self._cli_filename, "w", encoding="utf-8") as f:
                f.write(" ".join(shlex.quote(arg) for arg in self._cmd))
            self._process = Popen(self._cmd)  # pylint: disable=consider-using-with
            logging.info('Running "%s"', " ".join(shlex.quote(arg) for arg in self._cmd))

        def stop(self) -> None:
            assert self._process is not None
            if self._events is None:
                return

            if sys.platform == "linux":
                self._process.send_signal(SIGINT)
            elif sys.platform == "win32":
                self._previous_signal_handler = signal(
                    SIGINT, Perf._Recorder.signal_handler_windows
                )
                self._process.send_signal(CTRL_C_EVENT)

        def wait(self) -> None:
            if self._events is None or self._process is None:
                return

            self._process.wait()
            self._process = None
            if sys.platform == "win32":
                signal(SIGINT, self._previous_signal_handler)

        @staticmethod
        def signal_handler_windows(signum: Any, frame: Any) -> Any:
            pass

    def __init__(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        cores: Optional[Set[int]] = None,
    ):
        """Initializes a Perf instance.

        Args:
            events_groups: A sequence of event groups. Each group is a tuple of performance events.
            output_filename: The base filename for storing performance data outputs.
            cores: An optional set of CPU core ids to record events on.
        """
        self._events_groups = events_groups
        self._flat_events: List[PerfEvent] = list(
            itertools.chain.from_iterable(self._events_groups)
        )
        self._output_filename = output_filename
        self._cores = tuple(sorted(cores)) if cores is not None else None
        self._output_path = Path(output_filename).parent
        self._recorders: List[Perf._Recorder] = []

        recorders_events = Perf._extract_recorders_events(self._events_groups)
        for i, event_groups in enumerate(recorders_events):
            recorder = Perf._Recorder(
                events=event_groups,
                cli_filename=self._output_path / f"perf-cli-{i}",
                output_filename=f"{self._output_filename}-{i}",
                cores=self._cores,
            )
            self._recorders.append(recorder)

    @staticmethod
    def _extract_recorders_events(
        events_groups: Sequence[PerfEventGroup],
    ) -> Sequence[Sequence[PerfEventGroup]]:
        """
        Create bins of event groups such that each bin contains at most Perf.MAX_EVENT_COUNT events.

        This function takes a list of event groups and aggregates them into recorder event bins.
        Each bin accumulates event groups until adding a new group would exceed Perf.MAX_EVENT_COUNT.
        If any individual group has more events than Perf.MAX_EVENT_COUNT, a ValueError is raised.
        """
        count: int = 0
        current: List[PerfEventGroup] = []
        recorders_events: List[List[PerfEventGroup]] = []

        for group in events_groups:
            if len(group) > Perf._MAX_EVENT_COUNT:
                raise ValueError("Can't create Perf recording group. Too many events.")

            if count + len(group) > Perf._MAX_EVENT_COUNT:
                recorders_events.append(current)
                current = []
                count = 0
            count += len(group)
            current.append(tuple(group))

        if len(current) != 0:
            recorders_events.append(current)

        return recorders_events

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
        """Retrieves and aggregates recorded performance data.

        Returns:
            A dictionary mapping each performance recording location to its timed performance results.
        """
        locations: List[PerfRecordLocation] = (
            [Cpu(core) for core in self._cores] if self._cores is not None else [Uncore()]
        )

        records: PerfRecords = PerfRecords({loc: PerfTimedResults() for loc in locations})

        for recorder in self._recorders:
            recorder.wait()

            if recorder._events is None:
                continue

            output = self._read_perf_stat_output(recorder._output_filename)

            # Sanity check
            assert len(output) % len(recorder._flat_events) == 0

            # We need to map each recorder.events to their value
            i = 0
            while i < len(output):
                # Compute the CPU index as next iteration is for a full range of its values
                location: PerfRecordLocation
                if self._cores:
                    cpu_index = i // len(recorder._flat_events) % len(self._cores)
                    location = Cpu(self._cores[cpu_index])
                else:
                    location = Uncore()

                for event_group in recorder._events:
                    step = len(event_group)
                    values = output[i : i + step]
                    i = i + step
                    base_time = values[0][2]
                    # Sanity check, all the time should be equal and the name must match
                    for idx, (event_name, _value, t) in enumerate(values):
                        assert t == base_time
                        if sys.platform == "linux":
                            assert event_name == event_group[idx].perf_name()
                        elif sys.platform == "win32":
                            # Compare numeric values: parsed "0x22" vs expected "r22"
                            parsed_code = int(event_name, 0)
                            expected_code = int(event_group[idx].perf_name()[1:], 16)  # strip 'r'
                            assert parsed_code == expected_code, (
                                f"Windows event mismatch: parsed='{event_name}', expected='{event_group[idx].perf_name()}'"
                            )

                    records[location].setdefault(base_time, PerfResults())
                    assert records[location][base_time].get(tuple(event_group)) is None
                    records[location][base_time][tuple(event_group)] = tuple(v[1] for v in values)

        return records

    def _read_perf_stat_output(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        if sys.platform == "linux":
            return Perf._read_perf_stat_output_linux(self, filename)
        if sys.platform == "win32":
            return Perf._read_perf_stat_output_windows(self, filename)
        raise RuntimeError("Unsupported platform")

    def _read_perf_stat_output_windows(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        results: List[Tuple[str, Optional[float], Optional[float]]] = []
        with open(filename, encoding="utf-8") as f:
            json = load(f)
        if self._interval:
            current_time = 0.0
            for timed_result in json["timeline"]:
                current_time += timed_result["Time_elapsed"]
                for timed_result_for_core in timed_result["core"]["cores"]:
                    first = True
                    for event in timed_result_for_core["Performance_counter"]:
                        if first:
                            first = False
                            continue
                        results.append((event["event_idx"], event["counter_value"], current_time))
        else:
            for result_for_core in json["core"]["cores"]:
                first = True
                for event in result_for_core["Performance_counter"]:
                    if first:
                        first = False
                        continue
                    results.append((event["event_idx"], event["counter_value"], None))
        return results

    def _read_perf_stat_output_linux(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        with open(filename, encoding="utf-8") as f:
            return [
                self._parse_linux_perf_line(line)
                for line in f.read().splitlines()
                if line and not line.startswith("#")
            ]

    def _parse_linux_perf_line(self, line: str) -> Tuple[str, Optional[float], Optional[float]]:
        if self._interval is not None:
            if self._cores is None:
                # e.g. 0.100116703;178;;ITLB_WALK;96758700;100.00;;
                (time_str, count_str, _, event, _, _, _, _) = line.split(self._PERF_SEPARATOR)
            else:
                (time_str, _, _, count_str, _, event, _, _, _, _) = line.split(self._PERF_SEPARATOR)
            time = float(time_str)
        elif self._interval is None:
            # e.g. 139198,,BR_PRED:u,800440,100.00,,
            if self._cores is None:
                (count_str, _, event, _, _, _, _) = line.split(self._PERF_SEPARATOR)
            else:
                (_, _, count_str, _, event, _, _, _, _) = line.split(self._PERF_SEPARATOR)
            time = None
        else:
            assert False

        if count_str == "<not counted>":
            logging.info("Perf event %s was not counted", event)
        elif count_str == "<not supported>":
            logging.info(
                "Perf event %s was not supported. --max-events too big or not specified?",
                event,
            )

        if count_str == "0":
            logging.info("Perf counted 0 %s events", event)
        count = None if count_str in ("<not counted>", "<not supported>") else float(count_str)

        return self._strip_modifier(event), count, time

    def _strip_modifier(self, event_name: str) -> str:
        """Convert EVENT_NAME:modifier to EVENT_NAME"""
        if ":" in event_name:
            return event_name.split(":", 1)[0]
        return event_name

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def _create_event_count(
        self,
        r: _Recorder,
        index: int,
        name: str,
        value: Optional[float],
        time: Optional[float],
    ) -> PerfEventCount:
        event = r._flat_events[index % len(r._flat_events)]
        assert (
            sys.platform == "linux"
            and name == event.perf_name()
            or sys.platform == "win32"
            and int(name, 0) == event.code
        )
        return PerfEventCount(event=event, value=value, time=time)

    @staticmethod
    def get_pmu_counters(core: int) -> int:
        """Retrieves the number of PMU counters available for a given CPU core.

        Args:
            core: The core number to query.

        Returns:
            The number of available PMU counters.
        """
        if sys.platform == "linux":
            return Perf._get_pmu_counters_linux(core)
        if sys.platform == "win32":
            return Perf._get_pmu_counters_windows(core)
        raise RuntimeError("Invalid platform")

    @staticmethod
    def _get_pmu_counters_windows(_core: int) -> int:
        # pylint: disable=unsubscriptable-object
        return int(Perf._wperf_test()["PMU_CTL_QUERY_HW_CFG [gpc_num]"], 0)

    @staticmethod
    def _wperf_test() -> Dict[str, str]:
        if Perf._wperf_test_json is None:
            result = run([Perf._perf_path, "test", "--json"], stdout=PIPE, check=True)
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
            Perf._wperf_test_json = {
                item["Test_Name"]: item["Result"]
                for item in loads(result.stdout.decode("utf-8"))["Test_Results"]
            }
        return Perf._wperf_test_json

    @staticmethod
    def get_midr_value_windows() -> int:
        """Retrieves the MIDR value on Windows.

        Returns:
            The MIDR value as an integer.
        """
        # pylint: disable=unsubscriptable-object
        return int(Perf._wperf_test()["PMU_CTL_QUERY_CORE_CFG [0][midr_value]"], 0)

    @staticmethod
    def _get_pmu_counters_linux(core: int) -> int:
        def check_pmu_availability(core: int, count: int) -> bool:
            cmdline = [
                Perf._perf_path,
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
