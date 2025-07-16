# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Module for scheduling and managing hardware performance event groups.

This module provides an Event Scheduler designed to optimize and manage the sets of hardware
performance events for tools such as Perf. Modern performance profiling relies on capturing groups
of events simultaneously for each metric to ensure measurement accuracy. However, due to hardware
limitations (e.g. a restricted number of PMU counters), only a limited number of events can be recorded in one go.

Motivation & Context:
  - Grouping events per metric ensures that related events are captured together, thereby ensuring the
    integrity of measurements.
  - Different collection strategies provide trade-offs:
      * GROUP: Merges and consolidates overlapping events to minimize the total number of events captured,
        while ensuring metrics remain coherent.
      * METRIC: Captures each metric’s events separately, offering precise per-metric data.
      * NONE: Treats each event individually, useful for capturing a large number of metrics while reducing
        scheduling overhead.

Key Data Structures:
  - Tuple[E, ...]: Represents the set of events required for one metric.
  - Sequence[Tuple[E, ...]]: Represents a collection of metrics in a group.
  - Sequence[Sequence[Tuple[E, ...]]]: Represents all metric groups; this is the typical input for the scheduler.

Output and Consumption:
  The scheduler produces an iterator of event groups that can be passed directly to Perf for event capture.
  It also builds helper "retriever" functions to map the aggregated Perf output back to the original metrics,
  easing the extraction of measurement data.

Example Workflow:
    scheduler = EventScheduler(groups, collect_by=CollectBy.GROUP, max_events=6)
    for event_chunk in scheduler.get_event_group_iterator(split=True):
        perf.start(event_chunk)
        # perform workload measurement
    results = scheduler.retrieve_event_results(perf_result, group, metric_events)

Classes:
  CollectBy: Enumeration for scheduling strategy.
  EventGroupIterator: Iterator over event group chunks.
  GroupScheduleError: Exception raised when an event group exceeds hardware counter limits.
  EventScheduler: Scheduler for optimizing event group metrics.
"""

from enum import Enum
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    TypeVar,
    Generic,
)
from topdown_tool.perf import PerfEvent


E = TypeVar("E", bound=PerfEvent)


class CollectBy(Enum):
    """Enumeration for different collection strategies for performance events.

    Attributes:
        NONE: Each event is treated as an independent group.
        METRIC: Each metric (tuple of events) is captured as a group.
        GROUP: Groups are merged based on overlapping events.
    """

    NONE = "none"
    METRIC = "metric"
    GROUP = "group"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def from_string(arg: str) -> "CollectBy":
        """Converts a string to a CollectBy enum member.

        Args:
            arg: A string representing the collection strategy.

        Returns:
            The corresponding CollectBy member.
        """
        return CollectBy(arg.lower())


class GroupScheduleError(Exception, Generic[E]):
    """Exception raised when an event group cannot be scheduled within hardware counter limits.

    This exception is thrown if the union of events in a group exceeds the maximum allowed number
    of events that can be captured simultaneously.

    Args:
        unique_events: The set of unique events that were attempted to be scheduled.
        available_events: The maximum number of available events.
    """

    def __init__(self, unique_events: Set[E], available_events: int):
        self.unique_events = unique_events
        self.available_events = available_events
        events_str = ", ".join(ev.name for ev in self.unique_events)
        super().__init__(
            f"Could not schedule group with events: {events_str}. "
            f"Maximum number of events to schedule simultaneously: {available_events}"
        )


class EventGroupIterator(Generic[E]):
    """Iterator over event group chunks based on a maximum events limit.

    This iterator takes a sequence of event groups (each group is a Tuple[E, ...]) and, if splitting
    is enabled, yields chunks where the total number of events does not exceed max_events.

    Attributes:
        event_groups: A sequence of event groups.
        max_events: Maximum allowed events in a chunk.
        split: Whether to split event groups into multiple chunks.
    """

    def __init__(self, event_groups: Sequence[Tuple[E, ...]], max_events: int, split: bool):
        """Initializes the iterator with given event groups and settings.

        Args:
            event_groups: Sequence of event groups (each a tuple of events).
            max_events: Maximum number of events allowed in a single chunk.
            split: Boolean flag to indicate whether to split groups into chunks.

        Raises:
            ValueError: If an individual event group exceeds max_events.
        """
        self.event_groups = event_groups
        self.max_events = max_events
        self.split = split
        self._chunks: Sequence[Sequence[Tuple[E, ...]]] = (
            self._make_chunks() if split else [event_groups]
        )
        self._index: int = 0

    def _make_chunks(self) -> Sequence[Sequence[Tuple[E, ...]]]:
        # Splits the event groups into chunks such that each chunk's total events does not exceed max_events.
        chunks: List[List[Tuple[E, ...]]] = []
        current_chunk: List[Tuple[E, ...]] = []
        current_count = 0
        for group in self.event_groups:
            group_len = len(group)
            if group_len > self.max_events:
                raise ValueError("Too many events to capture without multiplexing")
            if current_chunk and current_count + group_len > self.max_events:
                chunks.append(current_chunk)
                current_chunk = []
                current_count = 0
            current_chunk.append(group)
            current_count += group_len
        if current_chunk:
            chunks.append(current_chunk)
        return chunks

    def __iter__(self) -> "EventGroupIterator[E]":
        return self

    def __next__(self) -> Sequence[Tuple[E, ...]]:
        """Returns the next chunk of event groups.

        Returns:
            A sequence (chunk) of event groups.

        Raises:
            StopIteration: When no more chunks are available.
        """
        if self._index >= len(self._chunks):
            raise StopIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    def has_next(self) -> bool:
        """Checks if there are more chunks available.

        Returns:
            True if more chunks are available; False otherwise.
        """
        return self._index < len(self._chunks)

    def remaining_chunks(self) -> int:
        """Returns the number of remaining chunks.

        Returns:
            The count of chunks that have not been iterated over.
        """
        return len(self._chunks) - self._index

    def index(self) -> int:
        """Returns the current index of iteration.

        Returns:
            The index of the next chunk to be returned.
        """
        return self._index


class EventScheduler(Generic[E]):
    """Scheduler for optimizing and managing performance event groups.

    Motivation:
      Perf requires the simultaneous capture of all events corresponding to a metric to ensure precise data
      recording. This scheduler automates the grouping of events so that they adhere to hardware limitations
      (via the max_events parameter) while preserving metric integrity.

    Collection Strategies:
      - GROUP: Merges overlapping event metrics to ensure that all required events are captured together.
      - METRIC: Captures each metric as provided, ideal for detailed per-metric analysis.
      - NONE: Treats each event as a standalone metric, useful when minimizing scheduling overhead.

    Technical Summary:
      - Tuple[E, ...]: Events required for one metric.
      - Sequence[Tuple[E, ...]]: A set of metrics forming a group.
      - Sequence[Sequence[Tuple[E, ...]]]: All groups of metrics; the scheduler’s input.

    Output and Retrievers:
      The scheduler outputs an iterator of optimized event groups that can be passed directly to Perf.
      Additionally, retriever functions are constructed to extract and map the recorded values back to the
      original metric definitions, even when events are merged or reordered.

    Example:
        scheduler = EventScheduler(groups, collect_by=CollectBy.GROUP, max_events=6)
        for event_chunk in scheduler.get_event_group_iterator(split=True):
            perf.start(event_chunk)
            # ... run workload ...
        results = scheduler.retrieve_event_results(perf_result, group, metric_events)

    Attributes:
        groups: The raw input event groups.
        collect_by: The strategy used to collect events.
        max_events: Maximum number of events that can be recorded simultaneously.
        optimized_event_groups: The optimized list of event groups after merging or splitting.
        retriever: A mapping function to extract specific event values from a perf result.
    """

    def __init__(
        self,
        groups: Sequence[Sequence[Tuple[E, ...]]],
        collect_by: CollectBy,
        max_events: int = 6,
    ) -> None:
        """Initializes an EventScheduler.

        Args:
            groups: A sequence of event group sequences; each inner tuple represents a metric.
            collect_by: A CollectBy enum value indicating the scheduling strategy.
            max_events: Maximum hardware counters available for simultaneous capture.

        Raises:
            GroupScheduleError: If a group cannot be scheduled given max_events.
        """
        self.groups = groups
        self.collect_by = collect_by
        self.max_events = max_events
        self.optimized_event_groups = self._generate_event_list()
        self.retriever: Optional[
            Dict[
                Tuple[E, ...],
                Callable[
                    [Dict[Tuple[E, ...], Tuple[Optional[float], ...]]],
                    Tuple[Optional[float], ...],
                ],
            ]
        ]

        if collect_by is not CollectBy.NONE:
            if collect_by is CollectBy.GROUP:
                events = [self._group_events(g) for g in self.groups]
            elif collect_by is CollectBy.METRIC:
                events = [
                    metric_events for group_events in self.groups for metric_events in group_events
                ]
            else:
                # Should not happen excepts if CollectBy is extended
                raise ValueError(f"Unknown collect_by value: {collect_by}")
            self.retriever = self._build_retriever(self.optimized_event_groups, events)
        else:
            self.retriever = None

    @staticmethod
    def _group_events(group_metrics: Sequence[Tuple[E, ...]]) -> Tuple[E, ...]:
        # Aggregates and sorts unique events across a group of metrics.
        unique_events = {event for metric_events in group_metrics for event in metric_events}
        sorted_events = sorted(unique_events)
        return tuple(sorted_events)

    def _generate_event_list(self) -> Sequence[Tuple[E, ...]]:
        # Generates the list of optimized event groups based on the 'collect_by' strategy.
        if self.collect_by is CollectBy.NONE:
            # Extract all events from groups and their metrics then remove duplicates
            metrics = [metric for group in self.groups for metric in group]
            events = {e for m in metrics for e in m}
            return [(e,) for e in events]
        if self.collect_by is CollectBy.GROUP:
            res: List[Tuple[E, ...]] = []
            for group in self.groups:
                group_events = self._group_events(group)
                if len(group_events) > self.max_events:
                    raise GroupScheduleError(set(group_events), self.max_events)
                res.append(group_events)
            return self._optimize_event_groups(res)
        if self.collect_by is CollectBy.METRIC:
            metrics = [m for g in self.groups for m in g]
            event_groups = set(metrics)
            return self._optimize_event_groups(event_groups)

        raise ValueError(f"Unknown collect_by value: {self.collect_by}")

    def _optimize_event_groups(self, groups: Iterable[Tuple[E, ...]]) -> List[Tuple[E, ...]]:
        # Optimizes groups by removing duplicates and merging groups where possible.
        # The method removes groups that are proper subsets of another and attempts greedy merging
        # if the union does not exceed max_events.
        unique_groups = list({g: None for g in groups}.keys())
        unique_groups = [
            g
            for g in unique_groups
            if not any(
                g != candidate and set(candidate).issuperset(g) for candidate in unique_groups
            )
        ]

        def merge_groups(g1: Tuple[E, ...], g2: Tuple[E, ...]) -> Tuple[E, ...]:
            merged = list(g1)
            for ev in g2:
                if ev not in merged:
                    merged.append(ev)
            return tuple(merged)

        merged: List[Tuple[E, ...]] = []
        used = [False] * len(unique_groups)
        for i in range(len(unique_groups)):  # pylint: disable=consider-using-enumerate
            if used[i]:
                continue
            candidate = unique_groups[i]
            used[i] = True
            merged_flag = True
            while merged_flag:
                merged_flag = False
                for j in range(i + 1, len(unique_groups)):
                    if not used[j]:
                        potential = merge_groups(candidate, unique_groups[j])
                        if len(potential) <= self.max_events:
                            candidate = potential
                            used[j] = True
                            merged_flag = True
            candidate = tuple(sorted(candidate))
            merged.append(candidate)
        return merged

    def get_event_group_iterator(self, split: bool = False) -> EventGroupIterator[E]:
        """Creates an iterator over event group chunks.

        Args:
            split: If True, yields chunks of event groups such that total events in each chunk
                   do not exceed max_events. If False, returns the entire optimized event groups list.

        Returns:
            An EventGroupIterator instance for iterating over event group chunks.

        Example:
            iterator = scheduler.get_event_group_iterator(split=True)
            for chunk in iterator:
                process(chunk)
        """
        return EventGroupIterator(self.optimized_event_groups, self.max_events, split)

    def retrieve_event_result(
        self,
        perf_result: Dict[Tuple[E, ...], Tuple[Optional[float], ...]],
        event: E,
    ) -> Tuple[Optional[float], ...]:
        """Retrieves the captured value for a single event in NONE mode.

        Args:
            perf_result: A dict mapping event tuples to recorded float values.
            event: The event whose result is to be retrieved.

        Returns:
            A tuple containing the value for the event.

        Raises:
            AssertionError: If the collection mode is not NONE.
        """
        assert self.collect_by is CollectBy.NONE
        assert self.retriever is None
        return perf_result[(event,)]

    def retrieve_metric_result(
        self,
        perf_result: Dict[Tuple[E, ...], Tuple[Optional[float], ...]],
        metric_events: Tuple[E, ...],
    ) -> Tuple[Optional[float], ...]:
        """Retrieves the values for a metric (tuple of events) in METRIC mode.

        Args:
            perf_result: A dictionary mapping metric event tuples to their captured values.
            metric_events: A tuple of events corresponding to the metric.

        Returns:
            A tuple of floats representing the captured metric values.

        Raises:
            AssertionError: If the scheduler is not in METRIC mode.
        """
        assert self.collect_by is CollectBy.METRIC
        assert self.retriever is not None
        return self.retriever[metric_events](perf_result)

    def retrieve_group_result(
        self,
        perf_result: Dict[Tuple[E, ...], Tuple[Optional[float], ...]],
        metric_groups: Sequence[Tuple[E, ...]],
    ) -> Tuple[Optional[float], ...]:
        """Retrieves aggregated event values for a group of metrics in GROUP mode.

        Args:
            perf_result: A dictionary mapping optimized group tuples to captured values.
            metric_groups: A sequence of metric event tuples forming the group.

        Returns:
            A tuple of floats corresponding to the aggregated values.

        Raises:
            AssertionError: If the scheduler is not in GROUP mode.
        """
        assert self.collect_by is CollectBy.GROUP
        assert self.retriever is not None
        return self.retriever[self._group_events(metric_groups)](perf_result)

    def retrieve_event_results(
        self,
        perf_result: Dict[Tuple[E, ...], Tuple[Optional[float], ...]],
        group: Sequence[Tuple[E, ...]],
        metric_events: Tuple[E, ...],
    ) -> Tuple[Optional[float], ...]:
        """Retrieves specific event values for a metric from performance results.

        Depending on the collection strategy (NONE, METRIC, or GROUP), this method extracts the
        relevant event values from the perf_result.

        Args:
            perf_result: A dictionary mapping event or metric tuples to their recorded values.
            group: The group of metrics from which to extract the result.
            metric_events: A tuple of events corresponding to the metric.

        Returns:
            A tuple of floats for the metric events or None if any value is missing.

        Raises:
            KeyError: If the expected key is not present in perf_result.
        """
        if self.collect_by is CollectBy.NONE:
            result = []
            for ev in metric_events:
                try:
                    result.append(perf_result[(ev,)][0])
                except Exception:  # pylint: disable=broad-exception-caught
                    result.append(None)
            return tuple(result)
        if self.collect_by is CollectBy.METRIC:
            return self.retrieve_metric_result(perf_result, metric_events)
        if self.collect_by is CollectBy.GROUP:
            group_values = self.retrieve_group_result(perf_result, group)
            result = []
            for ev in metric_events:
                try:
                    idx = self._group_events(group).index(ev)
                    result.append(group_values[idx])
                except ValueError:
                    result.append(None)
            return tuple(result)
        raise ValueError(f"Unknown collect_by value: {self.collect_by}")

    def _build_retriever(
        self,
        optimized: Sequence[Tuple[E, ...]],
        input_groups: Iterable[Tuple[E, ...]],
    ) -> Dict[
        Tuple[E, ...],
        Callable[
            [Dict[Tuple[E, ...], Tuple[Optional[float], ...]]],
            Tuple[Optional[float], ...],
        ],
    ]:
        """Builds a mapping from input event groups to extractor callables.

        Each callable, when invoked with a perf_result dictionary, extracts the corresponding
        event values based on precomputed indices.

        Args:
            optimized: A list of optimized event group tuples.
            input_groups: An iterable of input event group tuples.

        Returns:
            A dictionary mapping each input group tuple to a callable extractor function.

        Raises:
            KeyError: If no suitable optimized group covers an input group.
        """
        group_keys: List[Tuple[E, ...]] = list(optimized)
        retriever: Dict[Tuple[E, ...], Callable] = {}

        for input_group in input_groups:
            # find the unique group that covers it
            candidates = [g for g in group_keys if set(g).issuperset(set(input_group))]
            if not candidates:
                raise KeyError(f"optimized = {optimized}, input_groups = {list(input_groups)}")
            if len(candidates) > 1:
                # Require to get consistent results if a group was the subset of multiple ones
                candidates.sort(key=len)
            grp = candidates[0]

            # precompute the indices into grp for each event in m.events
            idxs = [grp.index(ev) for ev in input_group]

            # closure that captures grp and idxs
            def make_extractor(grp_key: Tuple[E, ...], idxs: List[int]) -> Callable[
                [Dict[Tuple[E, ...], Tuple[Optional[float], ...]]],
                Tuple[Optional[float], ...],
            ]:
                return lambda perf_res: tuple(perf_res[grp_key][i] for i in idxs)

            retriever[input_group] = make_extractor(grp, idxs)

        return retriever
