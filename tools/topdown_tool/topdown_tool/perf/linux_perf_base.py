# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Common Linux ``perf`` implementation shared by local and remote runners."""

import itertools
import logging
import shlex
from abc import ABC
from pathlib import Path
from sys import platform
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
if platform == "linux":
    from resource import getrlimit, RLIMIT_NOFILE

from topdown_tool.perf.perf import (
    Cpu,
    Perf,
    PerfEvent,
    PerfEventCount,
    PerfEventGroup,
    PerfRecordLocation,
    PerfRecords,
    PerfResults,
    PerfTimedResults,
    Uncore,
)

_PERF_SEPARATOR: str = ";"


class LinuxPerfBase(Perf, ABC):
    """Abstraction containing core logic for Linux-based perf runners."""

    _PMU_PROBE_EVENTS: Tuple[str, ...] = ("r8", "instructions")

    class _Recorder(ABC):
        """Abstract recorder implementation shared by Linux perf runners."""

        def __init__(
            self,
            events: Sequence[PerfEventGroup],
            output_filename: str,
            **_kwargs: Any,
        ) -> None:
            """Initialise the recorder with its event configuration.

            Args:
                events: Event groups assigned to this recorder.
                output_filename: Local file where perf writes statistics for this recorder.
            """
            self._events: Sequence[PerfEventGroup] = events
            self._flat_events: List[PerfEvent] = (
                list(itertools.chain.from_iterable(events)) if events else []
            )
            self._output_filename = output_filename

        @property
        def events(self) -> Sequence[PerfEventGroup]:
            return self._events

        @property
        def flat_events(self) -> Sequence[PerfEvent]:
            return self._flat_events

        @property
        def output_filename(self) -> str:
            return self._output_filename

        def start(self) -> None:
            """Begin perf data collection."""
            raise NotImplementedError

        def stop(self) -> None:
            """Stop perf data collection."""
            raise NotImplementedError

        def wait(self) -> None:
            """Block until perf finishes writing output."""
            raise NotImplementedError

        def prepare_output(self) -> bool:
            """Prepare recorder output for parsing (default no-op)."""
            return True

    _perf_path: str = "perf"

    def __init__(
        self,
        *,
        perf_args: Optional[str] = None,
        interval: Optional[int] = None,
    ) -> None:
        self._perf_args = perf_args
        self._interval = interval
        self._cores: Optional[Sequence[int]] = None
        self._timeout: Optional[int] = None
        self._recorders: List["LinuxPerfBase._Recorder"] = []
        self._events_groups: Sequence[PerfEventGroup] = []
        self._flat_events: List[PerfEvent] = []
        self._output_filename: Optional[str] = None
        self._output_path: Optional[Path] = None

    # pylint: disable=possibly-used-before-assignment
    @property
    def max_event_count(self) -> int:
        """Return the maximum number of events supported per recording."""

        soft, _ = getrlimit(RLIMIT_NOFILE)
        return soft - 5

    def enable(self) -> None:
        """Enable perf collection (no-op for Linux perf)."""

        return

    def disable(self) -> None:
        """Disable perf collection (no-op for Linux perf)."""

        return

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def start(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        pid: Optional[int] = None,
        cores: Optional[Sequence[int]] = None,
        timeout: Optional[int] = None,
    ) -> None:
        """Begin recording perf events using the supplied configuration.

        Args:
            events_groups: Event groups scheduled for the run.
            output_filename: Basename used for recorder outputs.
            pid: Optional PID passed to ``perf -p``.
            cores: Optional sequence of CPU cores passed to ``perf -C``.
        """
        self._recorders.clear()
        self._events_groups = events_groups
        self._flat_events = list(itertools.chain.from_iterable(self._events_groups))
        self._output_filename = output_filename
        self._output_path = Path(output_filename).parent
        self._cores = tuple(sorted(cores)) if cores else None
        self._timeout = timeout

        self._before_start()

        for index, event_groups in enumerate(self._extract_recorders_events(self._events_groups)):
            recorder_kwargs = self._recorder_kwargs(
                events=event_groups,
                cli_path=self._output_path / f"perf-cli-{index}",
                output_basename=f"{self._output_filename}-{index}",
                pid=pid,
                timeout=timeout,
            )
            self._recorders.append(
                self._Recorder(**recorder_kwargs)  # pylint: disable=protected-access
            )

        for recorder in self._recorders:
            recorder.start()

    def stop(self) -> None:
        """Stop all active recorders."""
        for recorder in self._recorders:
            recorder.stop()

    def wait(self) -> None:
        """Wait for all active recorders."""
        for recorder in self._recorders:
            recorder.wait()

    def get_perf_result(self) -> PerfRecords:  # pylint: disable=too-many-locals
        """Aggregate recorder outputs into a ``PerfRecords`` structure.

        Returns:
            PerfRecords: Mapping of locations to timed perf results.
        """
        if self._cores is not None:
            locations: List[PerfRecordLocation] = [Cpu(core) for core in self._cores]
        else:
            locations = [Uncore()]
        records: PerfRecords = PerfRecords({loc: PerfTimedResults() for loc in locations})

        for recorder in self._recorders:
            recorder.wait()

            if recorder.events is None:
                continue

            if not recorder.prepare_output():
                continue

            output = self._read_perf_stat_output(recorder.output_filename)

            assert len(output) % len(recorder.flat_events) == 0

            idx = 0
            while idx < len(output):
                location: PerfRecordLocation
                if self._cores:
                    cpu_index = idx // len(recorder.flat_events) % len(self._cores)
                    location = Cpu(self._cores[cpu_index])
                else:
                    location = Uncore()

                for event_group in recorder.events:
                    step = len(event_group)
                    values = output[idx : idx + step]
                    idx += step
                    base_time = values[0][2]

                    for event_idx, (event_name, _value, time) in enumerate(values):
                        assert time == base_time
                        assert event_name == self._strip_modifier(event_group[event_idx].perf_name())

                    records[location].setdefault(base_time, PerfResults())
                    assert records[location][base_time].get(tuple(event_group)) is None
                    records[location][base_time][tuple(event_group)] = tuple(v[1] for v in values)

        return records

    def _before_start(self) -> None:
        """Hook executed immediately before recorders are created."""

    # pylint: disable=too-many-arguments
    def _recorder_kwargs(
        self,
        *,
        events: Sequence[PerfEventGroup],
        cli_path: Path,
        output_basename: str,
        pid: Optional[int],
        timeout: Optional[int],
    ) -> Dict[str, Any]:
        """Return keyword arguments used to instantiate a recorder."""
        return {
            "events": events,
            "cli_filename": cli_path,
            "output_filename": output_basename,
            "cores": self._cores,
            "perf_args": self._perf_args,
            "interval": self._interval,
            "pid": pid,
            "timeout": timeout,
        }

    # pylint: disable=too-many-arguments
    @staticmethod
    def _compose_stat_command(
        perf_path: str,
        output_filename: str,
        *,
        cores: Optional[Sequence[int]],
        pid: Optional[int],
        interval: Optional[int],
        timeout: Optional[int],
    ) -> List[str]:
        """Create the common ``perf stat`` CLI prefix shared by local and remote runners."""

        command = [
            perf_path,
            "stat",
            "-x",
            _PERF_SEPARATOR,
            "-o",
            output_filename,
        ]
        if cores is not None:
            command.extend(["--per-core", "-C", ",".join(map(str, cores))])
        if pid is not None:
            command.extend(["-p", str(pid)])
        if interval is not None:
            command.extend(["-I", str(interval)])
        if timeout is not None:
            command.extend(["--timeout", str(timeout)])
        return command

    @staticmethod
    def _build_pmu_probe_command(
        perf_path: str,
        event: str,
        sample_count: int,
        core: int,
    ) -> List[str]:
        event_string = "{" + ",".join([f"{event}:u"] * sample_count) + "}"
        return [
            perf_path,
            "stat",
            "-e",
            event_string,
            "-C",
            str(core),
            "-x",
            "\\t",
            perf_path,
            "-v",
        ]

    @classmethod
    def _probe_pmu_count(
        cls,
        *,
        count: int,
        runner: Callable[[str, int], Sequence[str]],
        on_command_error: Optional[Callable[[str, Exception], None]] = None,
        require_all_success: bool = False,
    ) -> Optional[bool]:
        """Return whether ``count`` events can be scheduled, or ``None`` if unknown."""

        saw_output = False

        for event in cls._PMU_PROBE_EVENTS:
            try:
                lines = runner(event, count)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                if on_command_error:
                    on_command_error(event, exc)
                if require_all_success:
                    return None
                continue

            saw_output = True
            is_full = cls._is_full_count(lines)
            if not is_full:
                return False

            if not require_all_success:
                return True

        if not saw_output:
            return None

        return True if require_all_success else None

    def _read_perf_stat_output(
        self, filename: str
    ) -> List[Tuple[str, Optional[float], Optional[float]]]:
        """Load and parse a perf ``stat`` output file.

        Args:
            filename: Path to the perf output file.

        Returns:
            List[Tuple[str, Optional[float], Optional[float]]]: Parsed event entries.
        """
        with open(filename, encoding="utf-8") as handle:
            return [
                self._parse_perf_line(line)
                for line in handle.read().splitlines()
                if line and not line.startswith("#")
            ]

    def _parse_perf_line(self, line: str) -> Tuple[str, Optional[float], Optional[float]]:
        """Parse a single perf output line into ``(event, value, time)`` tuple.

        Args:
            line: Raw perf output line.

        Returns:
            Tuple[str, Optional[float], Optional[float]]: Event name, value (if any) and time.
        """
        if self._interval is not None:
            if self._cores is None:
                time_str, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            else:
                time_str, _, _, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            time = float(time_str)
        else:
            if self._cores is None:
                count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            else:
                _, _, count_str, _, event, *_ = line.split(_PERF_SEPARATOR)
            time = None

        if count_str == "<not counted>":
            logging.debug("Perf event %s was not counted", event)
        elif count_str == "<not supported>":
            logging.debug("Perf event %s was not supported.", event)
        if count_str == "0":
            logging.debug("Perf counted 0 %s events", event)

        count = None if count_str in ("<not counted>", "<not supported>") else float(count_str)
        return self._strip_modifier(event), count, time

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def _create_event_count(
        self,
        recorder: "_Recorder",
        index: int,
        name: str,
        value: Optional[float],
        time: Optional[float],
    ) -> PerfEventCount:
        """Create a :class:`PerfEventCount` for the given recorder entry.

        Args:
            recorder: Recorder providing the flat event list.
            index: Flat event index processed for this count.
            name: Event name reported by perf.
            value: Recorded value, if any.
            time: Timestamp associated with the measurement, if any.

        Returns:
            PerfEventCount: Structured representation of the event sample.
        """
        flat_events = recorder.flat_events
        event = flat_events[index % len(flat_events)]
        assert name == event.perf_name()
        return PerfEventCount(event=event, value=value, time=time)

    @classmethod
    def update_perf_path(cls, perf_path: str) -> None:
        """Override the default perf binary path used by the class.

        Args:
            perf_path: Path to the perf executable.
        """
        cls._perf_path = perf_path

    @classmethod
    def get_midr_value(cls, _core: int) -> int:
        """Return the MIDR value for ``core`` (unsupported in the base class).

        Args:
            _core: CPU core identifier (ignored).

        Raises:
            NotImplementedError: Always raised for the base class.
        """
        raise NotImplementedError("MIDR value is not supported on Linux")

    @staticmethod
    def write_cli_command(path: Path, cmd: List[str]) -> None:
        """Persist the recorder CLI command to disk.

        Args:
            path: Destination file path.
            cmd: Command arguments to write.
        """
        cmd_line = " ".join(shlex.quote(arg) for arg in cmd)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(cmd_line)

    @staticmethod
    def _is_full_count(stderr_lines: Sequence[str]) -> bool:
        """Return ``True`` if the perf output lines signal fully counted events.

        Args:
            stderr_lines: ``perf stat`` stderr lines to inspect.

        Returns:
            bool: ``True`` when all lines report 100% counted events; otherwise ``False``.
        """

        for line in stderr_lines:
            row = line.split("\t")
            if len(row) < 5:
                continue
            if row[0] in {"<not counted>", "<not supported>"}:
                return False
            try:
                if float(row[4]) != 100.0:
                    return False
            except Exception:  # pylint: disable=broad-exception-caught
                return False
        return True

    @staticmethod
    def _binary_search_pmu_max(check: Callable[[int], bool]) -> int:
        """Binary search helper returning the maximum PMU count satisfying ``check``.

        Args:
            check: Predicate indicating whether ``count`` events can be scheduled.

        Returns:
            int: Largest PMU count for which ``check`` returns ``True``.
        """

        pmu_min, pmu_max = 0, 31
        while pmu_min != pmu_max:
            pmu_attempt = (pmu_min + pmu_max + 1) // 2
            if check(pmu_attempt):
                pmu_min = pmu_attempt
            else:
                pmu_max = pmu_attempt - 1
        return pmu_min

    @staticmethod
    def _has_privilege_from_values(
        paranoid_value: Optional[str], status_value: Optional[str]
    ) -> bool:
        """Evaluate perf privilege state from ``perf_event_paranoid`` and ``/proc/self/status`` text.

        Args:
            paranoid_value: Raw contents of ``perf_event_paranoid`` or ``None``.
            status_value: Raw contents of ``/proc/self/status`` or ``None``.

        Returns:
            bool: ``True`` if the values indicate unrestricted perf privilege.
        """

        if paranoid_value is not None:
            try:
                if int(paranoid_value.strip()) == -1:
                    return True
            except Exception:  # pylint: disable=broad-exception-caught
                pass

        if status_value is not None:
            for line in status_value.splitlines():
                if line.startswith("CapEff:"):
                    try:
                        eff_caps = int(line.split()[1], 16)
                    except Exception:  # pylint: disable=broad-exception-caught
                        continue
                    if (eff_caps & (1 << 38)) or (eff_caps & (1 << 21)):
                        return True

        return False
