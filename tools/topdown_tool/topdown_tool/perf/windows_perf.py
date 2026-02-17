# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Windows-specific implementation of the Perf interface using `wperf`.

This module provides the `WindowsPerf` class, a Windows-only implementation of
the abstract `Perf` interface that collects hardware performance statistics via
Arm's `wperf` tool. It builds combined runs through the shared
`WperfCoordinator`, parses JSON output, and aggregates results per core and
event group. Interval-based sampling and per-core filtering are supported.

Notes
-----
- Functional only on Windows (``sys.platform == "win32"``).
- On non-Windows platforms, this class should not be instantiated.
"""
from abc import ABC, abstractmethod
from functools import cache
import logging
from subprocess import PIPE, run
from json import loads
from pathlib import Path
from threading import Event
from typing import Dict, Optional, Sequence, Tuple, Type, Union

from topdown_tool.perf.windows_coordinator import WperfCoordinator
from topdown_tool.perf.perf import (
    Perf,
    PerfEvent,
    PerfEventGroup,
    PerfRecords,
)
from topdown_tool.probe.probe import Probe


class WindowsPerfParser(ABC):
    @abstractmethod
    def __init__(self, perf_groups: Sequence[Sequence[PerfEvent]], perf_instance: "WindowsPerf") -> None:
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def prepare_perf_command_line(self, run_id: str) -> Tuple[Optional[Path], Tuple[str, ...]]:
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def before_capture(self) -> Tuple[Tuple[PerfEvent, ...], ...]:
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def parse_perf_data(self, data: dict) -> PerfRecords:
        raise NotImplementedError("Use derived class")


# pylint: disable=too-many-public-methods
class WindowsPerf(Perf):
    """
    Windows-specific Perf implementation using `wperf` for collecting
    performance event statistics.

    This class wires a probe into the process-wide `WperfCoordinator` so that
    multiple probes can share a single `wperf stat --json` run. It prepares the
    probe's event groups and cores, receives the combined JSON output via a
    callback, and converts it to the Linux-parity shape (group-aligned tuples).
    """

    _perf_parsers: Dict[Type["Probe"], Type[WindowsPerfParser]] = {}

    @staticmethod
    def have_perf_privilege() -> bool:
        """Always True on Windows."""
        return True

    def __init__(
        self,
        *,
        perf_args: Optional[str] = None,
        interval: Optional[int] = None,
    ):
        """
        Initialize a `WindowsPerf` probe and hold per-run configuration.

        Parameters
        ----------
        perf_args : Optional[str], keyword-only
            Additional flags to pass to `wperf` verbatim.
        interval : Optional[int], keyword-only
            Sampling interval in milliseconds. None means one-shot (no
            periodic sampling).
        """
        # Stable settings
        self._perf_args = perf_args
        self._interval = interval
        self._cores: Optional[Tuple[int, ...]] = None
        # Run-scoped / lifecycle
        self._special_parser: Optional[Type[WindowsPerfParser]] = None
        self._special_parser_instance: Optional[WindowsPerfParser] = None
        self._timeout: Optional[int] = None
        self._events_groups: Sequence[PerfEventGroup] = []
        self._coordinator = WperfCoordinator.get_instance()
        self._collected_result: Optional[PerfRecords] = None
        self._active = False
        self._results_ready = Event()

    def get_events_groups(self) -> Sequence[PerfEventGroup]:
        return self._events_groups

    def get_cores(self) -> Optional[Tuple[int, ...]]:
        return self._cores

    def get_interval(self) -> Optional[int]:
        return self._interval

    def get_timeout(self) -> Optional[int]:
        return self._timeout

    def set_results(self, result: PerfRecords) -> None:
        self._collected_result = result
        self._results_ready.set()

    def __str__(self) -> str:
        return f"WindowsPerf({self._cores})"

    @property
    def max_event_count(self) -> int:
        """Upper bound on the number of events the probe will accept per run."""
        return 1000

    def enable(self) -> None:
        """Register this probe with the shared coordinator if not already active.

        Creates a unique probe ID, obtains the singleton `WperfCoordinator`,
        registers the probe's cores and callback, and marks it active.
        """
        if self._active:
            return

        self._coordinator.register(self)
        self._active = True

    def disable(self) -> None:
        """Deactivate this probe and clear per-run configuration.

        Marks the probe inactive in the coordinator (if present) and resets local
        event state. Does not unregister; use coordinator cleanup for full reset.
        """
        if not self._active:
            return
        self._active = False
        self._coordinator.deactivate(self)
        self._events_groups = []

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def start(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        pid: Optional[int] = None,
        cores: Optional[Sequence[int]] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """
        Provide per-run parameters and request the shared run to start.

        Parameters
        ----------
        events_groups : Sequence[PerfEventGroup]
            Event groups for this probe. Order is preserved and defines value
            ordering in results.
        output_filename : str
            Base filename the probe uses for logging; the coordinator may append
            a suffix for its internal combined JSON.
        """
        if not self._active:
            raise RuntimeError("Probe not active; call enable() before start()")
        if not self._coordinator:
            raise RuntimeError("Probe not properly registered with coordinator")
        if self._special_parser is None:
            self._events_groups = events_groups
        else:
            self._special_parser_instance = self._special_parser(events_groups, self)
            self._events_groups = self._special_parser_instance.before_capture()
        self._cores = tuple(sorted(cores)) if cores is not None else None
        self._timeout = timeout
        self._coordinator.start(self, pid)

    def stop(self) -> None:
        """
        Request the coordinator to stop the combined run.

        Finalization occurs once all *active* probes have called `stop()`. Results
        will be delivered back through this probe's callback.
        """
        if not self._coordinator:
            # pylint: disable=broad-exception-raised
            raise Exception("Probe not properly registered with coordinator")
        self._coordinator.stop(self)

    def wait(self) -> None:
        """
        Waits for perf process.
        """
        if not self._coordinator:
            # pylint: disable=broad-exception-raised
            raise Exception("Probe not properly registered with coordinator")
        self._coordinator.wait(self)

    # pylint: disable=too-many-locals
    def get_perf_result(self) -> PerfRecords:
        """
        Return converted results.

        The method polls for results produced by `set_results`.
        if it is empty, an **empty** `PerfRecords` is returned (no exception) so callers
        can continue gracefully.

        Returns
        -------
        PerfRecords
            Group-aligned results keyed by location and timestamp (possibly empty).
        """
        self._results_ready.wait()

        if self._collected_result is None:
            logging.warning("No result from WperfCoordinator. returning empty record")
            self._collected_result = PerfRecords({})  # empty but valid

        return self._collected_result

    @classmethod
    def get_pmu_counters(cls, core: int) -> int:
        """
        Query the number of general-purpose PMU counters via `wperf test`.

        Parameters
        ----------
        core : int
            Core index (currently ignored by `wperf`; counters are reported
            globally).

        Returns
        -------
        int
            Number of general-purpose counters supported by the hardware.
        """
        return int(cls._wperf_test()["PMU_CTL_QUERY_HW_CFG [gpc_num]"], 0)

    # TODO - check if we can get pmu count from different cores using wperf. Currently, it is always giving for core 0
    @classmethod
    @cache
    def _wperf_test(cls) -> Dict[str, str]:
        """
        Run `wperf test --json` (once) and cache the parsed results.

        Returns
        -------
        Dict[str, str]
            Mapping of test names to their string results (e.g., hex strings).

        Notes
        -----
        - The result is cached. Subsequent calls reuse the cached dictionary
        until the process restarts.
        """
        perf_path = WperfCoordinator.get_perf_path()
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
        return {
            item["Test_Name"]: item["Result"]
            for item in loads(result.stdout.decode("utf-8"))["Test_Results"]
        }

    @classmethod
    @cache
    def _wperf_cpuinfo(cls) -> Dict[int, int]:
        """
        Run `wperf cpuinfo` (once) and cache MIDR_EL1 values per core.

        Returns
        -------
        Dict[int, int]
            Mapping of `core_id -> midr_value` where `midr_value` is an integer
            parsed from the last hexadecimal column of each output line.

        Notes
        -----
        - Output parsing assumes lines of the form:
        ``<core_id> ... <MIDR_EL1_hex>``.
        - The result is cached.
        """
        perf_path = WperfCoordinator.get_perf_path()
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
        return midr_map

    @classmethod
    def get_midr_value(cls, core: int) -> int:
        """
        Look up the MIDR_EL1 value for a specific core.

        Parameters
        ----------
        core : int
            Core ID to look up.

        Returns
        -------
        int
            MIDR_EL1 value for the requested core.

        Raises
        ------
        KeyError
            If the core is not present in cached CPU info.
        """
        cpuinfo = cls._wperf_cpuinfo()
        if core not in cpuinfo:
            raise KeyError(f"No MIDR entry found for core {core}")
        return cpuinfo[core]

    @classmethod
    def update_perf_path(cls, perf_path: str) -> None:
        """
        Update the single global `wperf` path used by both the coordinator and probes.

        Parameters
        ----------
        perf_path : str
            Absolute or relative path to `wperf`.
        """
        WperfCoordinator.set_perf_path(perf_path)

    @classmethod
    def get_cmn_version(cls) -> Dict[int, Union[int, str]]:
        """
        Returns a mapping between CMN index and CMN version. On Windows,
        contrary to Linux, we are currently limited to a single CMN.

        Returns
        -------
        Dict[int, Optional[str]]
            Mapping between CMN index and CMN version
        """
        # Windows Perf currently supports only a single CMN
        version = None
        try:
            result = run(
                [WperfCoordinator.get_perf_path(), "cmnlist"], stdout=PIPE, check=True, text=True
            )
        except FileNotFoundError:
            return {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("CMN"):
                new_version, _ = line.split("_", 1)
                if version is None:
                    version = new_version
                else:
                    assert version == new_version
        return {0: version} if version is not None else {}

    @classmethod
    def get_cmn_frequency(cls, cmn_index: int) -> float:
        """
        Get approximate CMN frequency. Frequency is measured using cycles event
        with Windows Perf. We assume that frequency is a constant.

        Args:
            cmn_index: CMN at given index to test.

        Returns:
            Frequency of a given CMN.
        """
        timeout = 0.1
        cmdline = [
            WperfCoordinator.get_perf_path(),
            "stat",
            "-e",
            f"arm_cmn_{cmn_index}/type=1,eventid=1/",
            "--json",
            "--timeout",
            str(timeout),
        ]
        try:
            result = run(cmdline, stdout=PIPE, check=True, text=True)
        except FileNotFoundError as e:
            raise RuntimeError("Couldn't detect CMN frequency. Couldn't run wperf.") from e
        test_json = loads(result.stdout)
        for cmn in test_json["counting"]:
            if cmn["mesh"] == cmn_index:
                for event in cmn["CMN_DTC"]:
                    if event["event"] == "cycles":
                        return event["value"] / timeout
        raise RuntimeError("Couldn't detect CMN frequency")

    @classmethod
    def get_cmn_mux_interval(cls, cmn_index: int) -> int:
        mux_interval: Optional[int] = None
        try:
            result = run(
                [WperfCoordinator.get_perf_path(), "test", "--json"],
                stdout=PIPE,
                check=True,
                text=True,
            )
        except FileNotFoundError as e:
            raise ValueError("Unknown mux interval for CMN. Couldn't run wperf.") from e
        test_json = loads(result.stdout)
        for test in test_json["Test_Results"]:
            if test["Test_Name"] == "config.count.period":
                mux_interval = int(test["Result"])
                break
        if mux_interval is None:
            raise ValueError("Unknown mux interval for CMN")
        return mux_interval

    @classmethod
    def register_parser_for_class(cls, probe_class: Type["Probe"], parser_class: Type[WindowsPerfParser]) -> None:
        cls._perf_parsers[probe_class] = parser_class

    def use_parser_for_class(self, probe_class: Type["Probe"]) -> None:
        if probe_class not in self._perf_parsers:
            raise RuntimeError("JSON parser not registered")
        self._special_parser = self._perf_parsers[probe_class]

    def prepare_perf_command_line(self, run_id: str) -> Tuple[Optional[Path], Tuple[str, ...]]:
        if self._special_parser is None or self._special_parser_instance is None:
            raise RuntimeError("JSON parser not registered")
        return self._special_parser_instance.prepare_perf_command_line(run_id)

    def parse_perf_data(self, data: dict) -> None:
        if self._special_parser is None or self._special_parser_instance is None:
            raise RuntimeError("JSON parser not registered")
        result = self._special_parser_instance.parse_perf_data(data)
        self.set_results(result)
