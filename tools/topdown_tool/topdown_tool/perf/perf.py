# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Defines the abstract Perf interface and supporting data structures for performance event recording.

This module provides:
- Abstract base class `Perf` for platform-specific implementations like LinuxPerf or WindowsPerf.
- Definitions for `PerfEvent`, `PerfEventGroup`, and `PerfEventCount`.
- Data models for recording locations (`Cpu`, `Uncore`) and result aggregation (`PerfRecords`, etc.).
- Helper functions to group event sets, initialize output, and format perf commands.

It enables a unified and extensible interface for collecting PMU-based telemetry across platforms.
"""

from abc import ABC, abstractmethod
from typing import (
    Sequence,
    Optional,
    final,
    List,
    Union,
    Dict,
    TypeVar,
    Protocol,
    Tuple,
)
from pathlib import Path
from dataclasses import dataclass

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


@dataclass(frozen=True, order=True)
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


class Perf(ABC):
    """
    Abstract base class for all platform-specific performance profilers.

    Subclasses must implement methods to start/stop recording, return parsed results,
    and report capabilities like MIDR value and PMU counter count.

    All shared static helpers for event formatting and output preparation are also defined here.
    """

    @abstractmethod
    def __init__(
        self,
        *,
        perf_args: Optional[str] = None,
        interval: Optional[int] = None,
    ) -> None: ...

    @property
    @abstractmethod
    def max_event_count(self) -> int:
        """Maximum events supported per measurement for this platform."""

    @abstractmethod
    def enable(self) -> None: ...

    @abstractmethod
    def disable(self) -> None: ...

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    @abstractmethod
    def start(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        pid: Optional[int] = None,
        cores: Optional[Sequence[int]] = None,
        timeout: Optional[int] = None,
    ) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def wait(self) -> None: ...

    @abstractmethod
    def get_perf_result(self) -> PerfRecords: ...

    @classmethod
    @abstractmethod
    def get_pmu_counters(cls, core: int) -> int:
        """
        Return the number of available PMU counters for a given core.

        Args:
            core: Core ID to query.

        Returns:
            Number of available PMU counters.
        """

    @staticmethod
    @abstractmethod
    def have_perf_privilege() -> bool: ...

    @classmethod
    @abstractmethod
    def get_midr_value(cls, core: int) -> int:
        """
        Return the MIDR (Main ID Register) value for the given core.

        Args:
            core: Core ID to query.

        Returns:
            MIDR value as an integer.
        """

    # pylint: disable=too-many-locals
    @final
    def _extract_recorders_events(
        self,
        events_groups: Sequence[PerfEventGroup],
    ) -> Sequence[Sequence[PerfEventGroup]]:
        """
        Bins event groups so that each recorder gets ≤ _MAX_EVENT_COUNT events.

        This prevents perf failures from exceeding kernel-imposed limits on the number
        of simultaneous counters.

        Args:
            events_groups: Sequence of PerfEventGroup to divide.

        Returns:
            List of recorder bins, each a sequence of grouped PerfEventGroup tuples.

        Raises:
            ValueError: If any single group exceeds _MAX_EVENT_COUNT.
        """
        max_arg_length = 2 ** 17 - 1

        # Calculate each group size
        groups_string_lengths: List[int] = []
        groups_fds: List[int] = []
        for group in events_groups:
            assert len(group) >= 1
            group_string_length = 1 if len(group) >= 2 else -1  # "{" + "}" - ","
            for event in group:
                group_string_length += len(event.perf_name()) + 1
            groups_string_lengths.append(group_string_length)
            groups_fds.append(len(group))

        # Try to assign to perf instances
        bins: List[List[int]] = []
        bins_lengths: List[int] = []
        bins_fds: List[int] = []
        for group_index, (group_length, group_fds) in enumerate(zip(groups_string_lengths, groups_fds)):
            found = False
            for bin_index, group_bin in enumerate(bins):
                if bins_lengths[bin_index] + group_length + 1 <= max_arg_length and bins_fds[bin_index] <= self.max_event_count:
                    found = True
                    group_bin.append(group_index)
                    bins_lengths[bin_index] += group_length + 1
                    bins_fds[bin_index] += group_fds
                    break
            if not found:
                bins.append([group_index])
                bins_lengths.append(group_length)
                bins_fds.append(group_fds)

        # Map groups indices to groups
        recorders_events = [
            [events_groups[group_index] for group_index in bin_group]
            for bin_group in bins
        ]

        return recorders_events

    @staticmethod
    @final
    def _strip_modifier(event_name: str) -> str:
        """Convert EVENT_NAME:modifier to EVENT_NAME"""
        if ":" in event_name:
            return event_name.split(":", 1)[0]
        return event_name

    @staticmethod
    @final
    def build_event_string(events: Sequence[PerfEventGroup]) -> str:
        """
        Generate the perf-compatible event string for a list of event groups.

        Multiple events in a group are grouped using curly braces `{}`, while single events are directly added.

        Args:
            events: List of event groups to encode.

        Returns:
            A comma-separated string suitable for perf `-e` flag.
        """
        parts = []
        for group in events:
            if len(group) > 1:
                parts.append("{" + ",".join(e.perf_name() for e in group) + "}")
            else:
                parts.append(next(iter(group)).perf_name())
        return ",".join(parts)

    @staticmethod
    @final
    def _initialize_output_file(path: Union[str, Path]) -> None:
        """Ensure the output file is empty before perf starts writing."""
        with open(path, "w", encoding="utf-8"):
            pass

    @classmethod
    @abstractmethod
    def update_perf_path(cls, perf_path: str) -> None: ...
