# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2024 Arm Limited

import dataclasses
import itertools
import json
import logging
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from typing import Collection, Dict, Iterable, List, Optional, Set

from topdown_tool.metric_data import Event, Group, Metric, MetricData, MetricInstance
from topdown_tool.utils import get_pmu_counters_windows

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


PerfStatFormat = Enum("PerfStatFormat", "NON_INTERVAL INTERVAL")


@dataclass(frozen=True)
class PerfOptions:
    command: List[str]
    core: Optional[str] = None
    all_cpus: bool = False
    pids: List[int] = field(default_factory=list)
    max_events: Optional[int] = None
    collect_by: CollectBy = CollectBy.METRIC
    use_event_names: bool = False
    perf_path: str = "perf"
    perf_args: str = ""
    perf_output: str = "perf.stat.txt"
    interval: Optional[int] = None

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
    def __init__(self, uncounted_events: Iterable[str]):
        super().__init__("The follow events were not counted: " + ", ".join(uncounted_events))
        self.uncounted_events = uncounted_events


class NoPMUCounterError(Exception):
    pass


class ZeroCyclesError(Exception):
    pass


def format_command(cmd):
    """Formats a command (string or list) for output"""

    if isinstance(cmd, list):
        return " ".join(shlex.quote(arg) for arg in cmd)
    return str(cmd)


def read_perf_stat_output_windows(filename: str, _perf_format: PerfStatFormat):
    def parse_event_idx(e):
        name = e["event_idx"]
        return f"r{int(name, 16):x}"

    def parse_value(e):
        value = e["scaled_value"] if "scaled_value" in e else e["counter_value"]
        if isinstance(value, str):
            value = value.replace(",", "")
        return float(value)

    with open(filename, encoding="utf-8") as f:
        core_data = json.load(f)["core"]

        counter_data = core_data["overall"].get("Systemwide_Overall_Performance_Counters")
        if not counter_data:
            counter_data = core_data["cores"][0]["Performance_counter"]

    # Remove initial fixed cycle counter element added by wperf
    if counter_data[0]["event_idx"] == "fixed" and counter_data[0]["event_name"] == "cycle":
        counter_data.pop(0)

    return [(parse_event_idx(e), parse_value(e), None) for e in counter_data]


def read_perf_stat_output_linux(filename: str, perf_format: PerfStatFormat):
    def strip_modifier(event_name: str):
        """Convert EVENT_NAME:modifier to EVENT_NAME"""
        if ":" in event_name:
            return event_name.split(":", 1)[0]
        return event_name

    def parse_line(line):
        if perf_format is PerfStatFormat.INTERVAL:
            # e.g. 0.100116703;178;;ITLB_WALK;96758700;100.00;;
            (time_str, count_str, _, event, _, _, _, _) = line.split(PERF_SEPARATOR)
            time = float(time_str)
        elif perf_format is PerfStatFormat.NON_INTERVAL:
            # e.g. 139198,,BR_PRED:u,800440,100.00,,
            (count_str, _, event, _, _, _, _) = line.split(PERF_SEPARATOR)
            time = None
        else:
            assert False

        if count_str == "<not counted>":
            logging.info("Perf event %s was not counted", event)
        elif count_str == "<not supported>":
            logging.info("Perf event %s was not supported. --max-events too big or not specified?", event)

        if count_str == "0":
            logging.info("Perf counted 0 %s events", event)
        count = None if count_str in ("<not counted>", "<not supported>") else float(count_str)
        return (strip_modifier(event), count, time)

    with open(filename, encoding="utf-8") as f:
        return [parse_line(line) for line in f.read().splitlines() if line and not line.startswith("#")]


read_perf_stat_output = read_perf_stat_output_linux if sys.platform == "linux" else read_perf_stat_output_windows


@dataclass(frozen=True, repr=False)
class CollectionEvent:
    event: Event
    group: Optional[Group] = None
    metric: Optional[Metric] = None

    @property
    def qualified_name(self):
        components = [self.group.name.replace("-", "_") if self.group else None, self.metric.name if self.metric else None, self.event.name]
        return ".".join(component for component in components if component is not None)

    def perf_name(self, use_event_names: bool):
        return self.event.name if use_event_names else f"r{self.event.code:x}"

    def __repr__(self) -> str:
        return f"{self.qualified_name} (0x{self.event.code:x})"


@dataclass(frozen=True)
class EventCount():
    event: CollectionEvent
    value: Optional[float] = None
    time: Optional[float] = None


def unique_event_names(events: Iterable[CollectionEvent]):
    """
    Unique events - excludes metric/group to get true event count that would need scheduling.

    This accounts for the fact that a collection group could contain the same PMU event multiple times.
    """
    return set(e.event.name for e in events)


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
        return len(unique_event_names(collection_group))

    remaining_events = events.copy()
    schedule: List[List[Set[CollectionEvent]]] = []
    while remaining_events:
        instance_events: List[Set[CollectionEvent]] = []
        num_events = 0
        while remaining_events and num_events + unique_len(remaining_events[-1]) <= max_events:
            collection_group = remaining_events.pop(-1)
            assert unique_len(collection_group) <= max_events
            instance_events.append(collection_group)
            num_events += unique_len(collection_group)

        schedule.append(instance_events)
    return schedule


def schedule_for_events(metric_instances: Iterable[MetricInstance], collect_by: CollectBy, max_events: int):
    def create_collection_events(mi: MetricInstance):
        group = mi.group if collect_by is CollectBy.GROUP else None
        metric = mi.metric if collect_by is not CollectBy.NONE else None
        return [CollectionEvent(event=e, group=group, metric=metric) for e in mi.metric.events]

    # Set of unique event instances to be collected.
    # Events can be collected multiple times (with associated group or metric) depending on collecy_by option
    collection_events = set(itertools.chain(*[create_collection_events(mi) for mi in metric_instances]))

    # A list of sets, where each set represents the events required for a particular group or metric (depending on collect-by)
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
            if len(events) > available_events:
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


# pylint: disable=too-many-branches
def __run_scheduled_events(scheduled_events: List[Set[CollectionEvent]], perf_options: PerfOptions):
    timed_event_counts: Dict[Optional[float], List[EventCount]] = {}
    flat_events = list(itertools.chain(*scheduled_events))  # Allows mapping of output to CollectionEvent
    # Pass duplicate events to Perf. Perf can remove them, and this makes it easier to map output back to CollectionEvents
    if perf_options.collect_by is CollectBy.NONE:
        assert all(len(g) == 1 for g in scheduled_events)
        perf_events_str = ",".join(e.perf_name(perf_options.use_event_names) for e in itertools.chain(*scheduled_events))
    else:
        perf_events_str = ",".join(["{%s}" % ",".join(e.perf_name(perf_options.use_event_names) for e in x) for x in scheduled_events if x])  # pylint: disable=consider-using-f-string

    perf_command = [perf_options.perf_path, "stat", "-e", perf_events_str]
    if sys.platform == "linux":
        perf_command += ["-o", perf_options.perf_output, "-x", PERF_SEPARATOR]
        if perf_options.core:
            perf_command += ["-C", perf_options.core]
    else:
        perf_command += ["--json", "--output", perf_options.perf_output]
        if perf_options.core:
            perf_command += ["-c", perf_options.core]

    if perf_options.all_cpus:
        perf_command.append("-a")
    if perf_options.pids:
        perf_command += ["-p", perf_options.pids_string]
    if perf_options.interval:
        perf_command += ["-I", str(perf_options.interval)]
    if perf_options.perf_args:
        perf_command += shlex.split(perf_options.perf_args)
    if perf_options.command:
        perf_command += ["--"]  # double-dash delimiter is accepted by Linux and WindowsPerf CLI parser
        perf_command += perf_options.command
    logging.info('Running "%s"', format_command(perf_command))
    logging.debug("Unique events: %s", ",".join(set(e.event.name for e in flat_events)))

    try:
        subprocess.check_call(perf_command)
    except KeyboardInterrupt:
        logging.info("Received interrupt. Analysing data.")

    def to_event_count(index: int, name: str, value: Optional[float], time: Optional[float]):
        event = flat_events[index % len(flat_events)]
        assert name == event.perf_name(perf_options.use_event_names) or name == event.perf_name(False)  # Note: event index always used on Windows
        return EventCount(event=event, value=value, time=time)

    perf_format = PerfStatFormat.INTERVAL if perf_options.interval else PerfStatFormat.NON_INTERVAL
    event_counts = [to_event_count(index, name, value, time) for index, (name, value, time) in enumerate(read_perf_stat_output(perf_options.perf_output, perf_format))]

    if any(e.event.event.name == "CPU_CYCLES" and e.value == 0 for e in event_counts):
        raise ZeroCyclesError()

    uncounted_events = [e for e in event_counts if e.value is None]
    if uncounted_events:
        last_interval_time = event_counts[-1].time
        if perf_options.interval and any(e.time != last_interval_time for e in event_counts) and all(e.time == last_interval_time for e in uncounted_events):
            logging.info("Ignoring last interval as not all events could be collected. Likely too short.")
            return timed_event_counts

        raise UncountedEventsError(set(e.event.event.name for e in uncounted_events))

    # Append event counts to the corresponding timed bucket
    for time, counts_for_time in itertools.groupby(event_counts, key=lambda e: e.time):
        timed_event_counts.setdefault(time, []).extend(counts_for_time)

    return timed_event_counts


def collect_events(metric_instances: Iterable[MetricInstance], perf_options: PerfOptions):
    schedule = schedule_for_events(metric_instances, perf_options.collect_by, perf_options.max_events or sys.maxsize)

    if len(schedule) > 1:
        if not perf_options.command:
            print("Can't do system-wide profiling with multiple runs. Remove or increase --max-events.", file=sys.stderr)
            sys.exit(1)
        elif perf_options.pids:
            print("Can't monitor PID(s) with multiple runs. Remove or increase --max-events.", file=sys.stderr)
            sys.exit(1)
        elif perf_options.interval:
            print("Can't collect interval data with multiple runs. Remove or increase --max-events.", file=sys.stderr)
            sys.exit(1)

    if not perf_options.command:
        print("Starting system-wide profiling. Hit Ctrl-C to stop. (See --help for usage information.)")
    elif perf_options.pids:
        print(f"Monitoring {perf_options.pids_display_string}. Hit Ctrl-C to stop.")

    # "Schedule" perf instances based on max_events.
    timed_event_counts: Dict[Optional[float], List[EventCount]] = {}
    for scheduled_events in schedule:
        for time, counts_for_time in __run_scheduled_events(scheduled_events, perf_options).items():
            timed_event_counts.setdefault(time, []).extend(counts_for_time)
    return timed_event_counts


def get_pmu_counters_linux(cpu: str, perf_path: str) -> int:
    """Detect the maximum number of programmable counters available on the current machine

    First will run the following perf command with 6 events:
    sudo perf stat -e {event1,event2,event3...} -- sleep 0.1

    If the event's data is not collected, the corresponding perf output result is <not counted> or <not supported>.
    However, outputting 0 is considered as data has been collected, which does not affect the logic of detecting the number of PMU counters.

    If the events's data is not collected, it means the number of PMU counts is less than the number of events specified in the command,
    we shall then reduce one event and try again.

    """

    metric_data = MetricData.get_data_for_cpu(cpu)
    # CPU_CYCLES event has a dedicated counter, here we only care about the number of programmable counters.
    events = (e for e in metric_data.events.values() if e.name != "CPU_CYCLES")

    for cnt in range(CPU_PMU_COUNTERS, 0, -1):
        try:
            scheduled_events = {CollectionEvent(event=e) for e in itertools.islice(events, cnt)}
            perf_options = PerfOptions(command=["sleep", "0"],
                                       all_cpus=False,
                                       collect_by=CollectBy.GROUP,
                                       perf_path=perf_path
                                       )
            logging.info("Detect the number of available programmable counters, try to collect %s events at the same time", cnt)
            __run_scheduled_events([scheduled_events], perf_options)
            logging.info("There are %s programmable PMU counters available", cnt)
            return cnt
        except UncountedEventsError:
            pass
    raise NoPMUCounterError()


def get_pmu_counters(cpu: str, perf_path: str) -> int:
    if sys.platform == "linux":
        return get_pmu_counters_linux(cpu, perf_path)
    return get_pmu_counters_windows(perf_path)
