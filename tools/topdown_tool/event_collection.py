# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import dataclasses
import itertools
import logging
import math
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Collection, Iterable, List, Optional, Set

from metric_data import Event, Group, Metric, MetricInstance, field_dict

# Separator used in perf stat output
PERF_SEPARATOR = ";"
# TODO: Read from MRS data when available
CPU_PMU_COUNTERS = 6


class CollectBy(Enum):
    NONE = "none"
    METRIC = "metric"
    GROUP = "group"

    def __str__(self):
        return self.value


@dataclass(frozen=True)
class PerfOptions:
    command: List[str]
    all_cpus: bool = False
    pids: List[int] = field(default_factory=list)
    max_events: Optional[int] = None
    collect_by: CollectBy = CollectBy.METRIC
    raw: bool = False
    perf_args: str = ""
    perf_output: str = "perf.stat.txt"

    @property
    def pids_string(self):
        """Comma-separated list of PIDs"""
        return ",".join(str(p) for p in self.pids)

    @property
    def pids_display_string(self):
        """Comma-separated list of PIDs, prefixed with PID or PIDs"""
        return f'{"PID" if len(self.pids) == 1 else "PIDs"} {",".join(str(p) for p in self.pids)}'

    @staticmethod
    def from_args(args):
        data = {field.name: getattr(args, field.name) for field in dataclasses.fields(PerfOptions)}
        return PerfOptions(**data)


class GroupScheduleError(Exception):
    def __init__(self, group: Group, event_names: Collection[str], available_events: int):
        self.group = group
        self.events = event_names
        self.available_events = available_events
        super().__init__(f"Could not schedule {', '.join(event_names)}")


class MetricScheduleError(Exception):
    def __init__(self, metric: Metric, event_names: Collection[str], available_events: int):
        self.metric = metric
        self.events = event_names
        self.available_events = available_events
        super().__init__(f"Could not schedule {', '.join(event_names)}")


class UncountedEventsError(Exception):
    def __init__(self, uncounted_events):
        super().__init__("The follow events were not counted: " + ", ".join(uncounted_events))
        self.uncounted_events = uncounted_events


def read_perf_stat_output(filename: str):
    def strip_modifier(event_name: str):
        """Convert EVENT_NAME:modifier to EVENT_NAME"""
        if ":" in event_name:
            return event_name.split(":", 1)[0]
        return event_name

    def parse_line(line):
        # e.g. 139198,,BR_PRED:u,800440,100.00,,
        (count_str, _, event, _, _, _, _) = line.split(PERF_SEPARATOR)
        if count_str == "<not counted>":
            logging.info("Perf event %s was not counted", event)
        elif count_str == "<not supported>":
            logging.info("Perf event %s was not supported. --max-size too big or not specified?", event)

        if count_str == "0":
            logging.info("Perf counted 0 %s events", event)
        count = math.nan if count_str in ("<not counted>", "<not supported>") else float(count_str)
        return (strip_modifier(event), count)

    with open(filename, encoding="utf-8") as f:
        return [parse_line(line) for line in f.read().splitlines() if line and not line.startswith("#")]


@dataclass(frozen=True, repr=False)
class CollectionEvent(Event):
    group: Optional[Group] = None
    metric: Optional[Metric] = None

    @property
    def qualified_name(self):
        components = [self.group.name.replace("-", "_") if self.group else None, self.metric.name if self.metric else None, self.name]
        return ".".join(component for component in components if component is not None)

    def perf_name(self, raw: bool):
        return f"r{self.code:x}" if raw else self.name

    def __repr__(self) -> str:
        return f"{self.qualified_name} (0x{self.code:x})"


@dataclass(frozen=True)
class CollectionEventCount(CollectionEvent):
    value: float = 0.0


def schedule_events(events: List[Set[CollectionEvent]], max_events: int):
    """Create a schedule to run the specified events such as only max_events are collected at once.

    Keyword arguments:
    events     -- List of event groups. The inner list represents events that should be scheduled together.
    max_events -- Maximum number of events to scheduled simultaneously.

    Output:
    A schedule of event groups to be executed by Perf. Each element of the outer list represents an instance of Perf.

    Note that returned collection groups may contain more than `max_events` `CollectionEvent` objects. This is because
    several `CollectionEvent`s can refer to the same PMU event.

    TODO: Plenty of room for improvement here:
    Current strategy grabs collection groups (from front of list) until we hit max_events.
    * Doesn't give optimal scheduling
    * Doesn't account for the case where two collection groups share common events. This should require fewer PMU counters.
    * Doesn't account for events that use fix-function counters (e.g. CPU_CYCLES)
    """

    def unique_len(collection_group: Iterable[CollectionEvent]):
        """Calculate number of unique PMU events.

        This accounts for the fact that a collection group could contain the same PMU event multiple times.
        """
        return len(set(e.name for e in collection_group))

    remaining_events = events.copy()
    schedule: List[List[Set[CollectionEvent]]] = []
    while remaining_events:
        instance_events: List[Set[CollectionEvent]] = []
        num_events = 0
        while remaining_events and num_events + unique_len(remaining_events[0]) <= max_events:
            collection_group = remaining_events.pop()
            assert unique_len(collection_group) <= max_events
            instance_events.append(collection_group)
            num_events += unique_len(collection_group)

        schedule.append(instance_events)
    return schedule


def schedule_for_events(metric_instances: Iterable[MetricInstance], collect_by: CollectBy, max_events: int):
    def unique_event_names(events: Iterable[CollectionEvent]):
        """Unique events - excludes metric/group to get true event count that would need scheduling """
        return set(e.name for e in events)

    def create_collection_events(mi: MetricInstance):
        group = mi.group if collect_by is CollectBy.GROUP else None
        metric = mi.metric if collect_by is not CollectBy.NONE else None
        return [CollectionEvent(**field_dict(e), group=group, metric=metric) for e in mi.metric.events]

    # Set of unique event instances to be collected.
    # Events can be collected multiple times (with associated group or metric) depending on collecy_by option
    collection_events = set(itertools.chain(*[create_collection_events(mi) for mi in metric_instances]))

    collection_groups: List[Set[CollectionEvent]] = []
    available_events = min(max_events, CPU_PMU_COUNTERS)
    if collect_by is CollectBy.GROUP:
        for group in set(e.group for e in collection_events):
            assert group
            events = set(e for e in collection_events if e.group == group)
            unique = unique_event_names(events)
            if len(unique) > available_events:
                raise GroupScheduleError(group, unique, available_events)
            collection_groups.append(events)
    elif collect_by is CollectBy.METRIC:
        for metric in set(e.metric for e in collection_events):
            assert metric
            events = set(e for e in collection_events if e.metric == metric)
            unique = unique_event_names(events)
            if len(events) > max_events:
                raise MetricScheduleError(metric, unique, available_events)
            collection_groups.append(events)
    elif collect_by is CollectBy.NONE:
        collection_groups = [{e} for e in collection_events]
    else:
        assert False

    unique_metrics = set(mi.metric for mi in metric_instances)
    logging.info("Collecting derived metrics:")
    for metric in unique_metrics:
        logging.info("    %s = %s", metric.title, metric.formula)

    return schedule_events(collection_groups, max_events)


def collect_events(metric_instances: Iterable[MetricInstance], perf_options: PerfOptions):
    schedule = schedule_for_events(metric_instances, perf_options.collect_by, perf_options.max_events or sys.maxsize)

    if perf_options.pids:
        if len(schedule) > 1:
            print("Can't monitor PID(s) with multiple runs. Remove --max-events.", file=sys.stderr)
            sys.exit(1)
        else:
            print(f"Monitoring {perf_options.pids_display_string}. Hit Ctrl-C to stop.")
    elif not perf_options.command:
        if len(schedule) > 1:
            print("Can't do system-wide profiling with multiple runs. Remove --max-events.", file=sys.stderr)
            sys.exit(1)
        else:
            print("Starting system-wide profiling. Hit Ctrl-C to stop. (See --help for usage information.)")

    # "Schedule" perf instances based on max_events.
    event_counts: List[CollectionEventCount] = []
    for scheduled_events in schedule:
        flat_events = list(itertools.chain(*scheduled_events))  # Allows mapping of output to CollectionEvent
        # Pass duplicate events to Perf. Perf can remove them, and this makes it easier to map output back to CollectionEvents
        if perf_options.collect_by is CollectBy.NONE:
            assert all(len(g) == 1 for g in scheduled_events)
            perf_events_str = ",".join(e.perf_name(perf_options.raw) for e in itertools.chain(*scheduled_events))
        else:
            perf_events_str = ",".join(["{%s}" % ",".join(e.perf_name(perf_options.raw) for e in x) for x in scheduled_events if x])  # pylint: disable=consider-using-f-string

        perf_command = ["perf", "stat", "-e", perf_events_str, "-o", perf_options.perf_output, "-x", PERF_SEPARATOR]
        if perf_options.all_cpus:
            perf_command.append("-a")
        if perf_options.pids:
            perf_command += ["-p", perf_options.pids_string]
        if perf_options.perf_args:
            perf_command += shlex.split(perf_options.perf_args)
        perf_command += perf_options.command
        logging.info('Running "%s"', " ".join(perf_command))
        logging.debug("Unique events: %s", ",".join(set(e.name for e in flat_events)))

        try:
            subprocess.check_call(perf_command)
        except KeyboardInterrupt:
            logging.info("Received interrupt. Analysing data.")

        def to_event_value(index, name, value):
            e = flat_events[index]
            assert name == e.perf_name(perf_options.raw)
            return CollectionEventCount(**field_dict(e), value=value)

        event_counts += [to_event_value(index, name, value) for index, (name, value) in enumerate(read_perf_stat_output(perf_options.perf_output))]

    uncounted_events = set(e.name for e in event_counts if e.value is math.nan)
    if uncounted_events:
        raise UncountedEventsError(uncounted_events)
    return event_counts
