#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import argparse
import dataclasses
import json
import os
import sys
import textwrap
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, OrderedDict, TypeVar

import event_codes
import mapping
import mrs_data
from categorise import ArmDataEventGrouper, TelemetryEventGrouper, add_categories
from mrs_data import MrsEvent, read_json

CHAIN_EVENT_CODE = 0x1e
COMMON_MICROARCH_FILENAME = "common-and-microarch.json"
RECOMMENDED_FILENAME = "recommended.json"
MAPFILE_FILENAME = "mapfile.csv"
METRICS_FILENAME = "metrics.json"


@dataclass(frozen=True)
class PerfEvent:
    """Event in Linux Perf format.

    All fields optional as events may contain event data and/or ArchStdEvent to refer to a common
    event.
    """
    ArchStdEvent: Optional[str] = None
    EventCode: Optional[str] = None
    EventName: Optional[str] = None
    BriefDescription: Optional[str] = None
    PublicDescription: Optional[str] = None
    # Topics: Internal field, used to hold groups
    Topics: List[str] = dataclasses.field(default_factory=list)

    @property
    def numeric_code(self):
        return event_codes.to_int(self.EventCode)

    @property
    def common(self):
        return event_codes.is_common(self.numeric_code)

    @property
    def recommended(self):
        return event_codes.is_recommended(self.numeric_code)

    def to_perf_dict(self):
        """Return a dict that represents this event in Perf format.

        Used for JSON serialisation.
        """
        result = OrderedDict()
        for field in ["ArchStdEvent", "PublicDescription", "EventCode", "EventName",
                      "BriefDescription"]:
            value = getattr(self, field)
            if value is not None:
                result[field] = value
        return result


@dataclass(frozen=True)
class PerfMetric:
    """Metric in Linux Perf format."""
    MetricName: str
    MetricExpr: str
    BriefDescription: Optional[str] = None
    PublicDescription: Optional[str] = None
    MetricGroup: Optional[str] = None
    ScaleUnit: Optional[str] = None

    def to_perf_dict(self):
        """Return a dict that represents this metric in Perf format.

        Used for JSON serialisation.
        """
        result = OrderedDict()
        for field in ["MetricName", "MetricExpr", "BriefDescription", "PublicDescription",
                      "MetricGroup", "ScaleUnit"]:
            value = getattr(self, field)
            if value is not None:
                result[field] = value
        return result


def find_event_in_list(event: PerfEvent, event_list: List[PerfEvent]):
    """
    Find an instance of event in event_list (based on event code).

    Return the event from the list if found.
    Returns None if not found.
    """
    assert event.EventCode is not None
    return next((e for e in event_list if event.numeric_code == e.numeric_code), None)


def perf_arm64_path(perf_path: str):
    return os.path.join(perf_path, "pmu-events", "arch", "arm64")


def mrs_events_to_perf_events(cpu_events: List[MrsEvent], common_events: List[MrsEvent]):
    """
    Read MRS data and return a list of CPU events and corresponding common events.

    Note: Returned CPU and common events may not map 1:1 as IMPDEF events won't appear in common
    data.
    """

    T = TypeVar("T")

    def partition(iterable: Iterable[T], predicate: Callable[[T], bool]):
        """
        Return two lists. One with items where predicate(item) == true, one with items where
        predicate(item) is false
        """
        true_list: List[T] = []
        false_list: List[T] = []
        for i in iterable:
            (true_list if predicate(i) else false_list).append(i)

        return (true_list, false_list)

    def mrs_to_perf_event(event: mrs_data.MrsEvent):
        """Create dict in Perf format from dict in MRS format"""
        def format_code(code):
            return '0x{:>02X}'.format(code) if code is not None else None

        return PerfEvent(PublicDescription=event.description,
                         EventCode=format_code(event.code),
                         EventName=event.name,
                         BriefDescription=event.description)

    # Some events are defined without names or codes
    # e.g. 0xC0 and last events in
    # https://developer.arm.com/documentation/ddi0500/j/Performance-Monitor-Unit/Events?lang=en
    cpu_events, skipped_no_code = partition(cpu_events, lambda e: e.code is not None)
    cpu_events, skipped_no_name = partition(cpu_events, lambda e: bool(e.name))

    if skipped_no_name:
        # If no name, they should be IMPDEF
        assert not [e for e in skipped_no_name if not event_codes.is_impdef(e.code) or not e.impdef]
        skipped_codes = ", ".join([hex(e.code) for e in skipped_no_name])
        print("Warning: Skipping IMPDEF events with no event name: %s" % skipped_codes,
              file=sys.stderr)

    if skipped_no_code:
        def shorten(e):
            return textwrap.shorten(e.name or e.description or "no description", width=80)

        messages = [shorten(e) for e in skipped_no_code]
        print("Warning: Skipped events with no event code:\n  %s" % "\n  ".join(messages),
              file=sys.stderr)

    perf_cpu_events = [mrs_to_perf_event(e) for e in cpu_events if e.code != CHAIN_EVENT_CODE]
    perf_common_events = [mrs_to_perf_event(e) for e in common_events]

    # List of common events that are present in the specified CPU events
    # (may not be 1:1. e.g. impdef events)
    present_perf_common_events = [e for e in perf_cpu_events
                                  if find_event_in_list(e, perf_common_events)]

    return (perf_cpu_events, present_perf_common_events)


def mrs_metrics_to_perf_metrics(mrs_metrics: List[mrs_data.MrsMetric]):
    def mrs_to_perf_metric(metric: mrs_data.MrsMetric):
        """
        Create dict in Perf format from dict in MRS format
        """
        # TopdownL1 is used in perf instead of Topdown_L1
        groups = [g.replace("Topdown_L", "TopdownL") for g in metric.groups or []]
        group = ";".join(groups)

        # Use full description instead of title in brief description because
        # title just repeats the name without underscores.
        return PerfMetric(MetricExpr=metric.formula,
                          MetricName=metric.name,
                          BriefDescription=metric.description or None,
                          MetricGroup=group or None,
                          ScaleUnit=f"1{metric.units}" if metric.units else None)

    return [mrs_to_perf_metric(m) for m in mrs_metrics]


class PerfData:
    def __init__(self, perf_path, event_grouper):
        arm64_dir = perf_arm64_path(perf_path)

        common_path = os.path.join(arm64_dir, COMMON_MICROARCH_FILENAME)
        self.common_microarch_events = [PerfEvent(**e) for e in read_json(common_path)]
        recomended_path = os.path.join(arm64_dir, RECOMMENDED_FILENAME)
        self.recommended_events = [PerfEvent(**e) for e in read_json(recomended_path)]
        self.mapfile = mapping.read_perf_cpu_mappings(os.path.join(arm64_dir, MAPFILE_FILENAME))
        self.cpu_events = {}
        self.metrics = {}
        self.event_grouper = event_grouper

    def add_cpu(self, mrs_cpu_info: mapping.MidrFields, mrs_cpu_events: List[MrsEvent],
                mrs_common_events: List[MrsEvent], perf_cpu_name: str, source_file_name: str = ""):
        def sort_by_event_code(rows: List[PerfEvent]):
            return sorted(rows, key=lambda e: e.numeric_code or 0)

        def filter_common(events: List[PerfEvent]):
            return [e for e in events if e.common]

        def filter_recommended(events: List[PerfEvent]):
            return [e for e in events if e.recommended]

        def replace_arch_std_event(cpu_events: List[PerfEvent], common_events: List[PerfEvent]):
            """Replace non-IMPDEF events with "ArchStdEvent" reference to common event"""
            def replaced_event(event: PerfEvent):
                common_event = find_event_in_list(event, common_events)
                if common_event:  # If event exists in common events, include reference instead
                    return PerfEvent(ArchStdEvent=event.EventName, Topics=event.Topics,
                                     PublicDescription=event.PublicDescription)
                else:
                    assert event_codes.is_impdef(event.EventCode), \
                           f"{event.EventCode} is unknown and not IMPDEF: {event}"
                    return event

            return [replaced_event(e) for e in cpu_events]

        def replace_event_name(events: List[PerfEvent], common_events: List[PerfEvent]):
            """Replace event names where they disagree with common event names.

            Sometimes per-CPU events and common events have different names for the same event code.
            In this case, update the CPU event with the common name
            """

            def renamed_event(event: PerfEvent):
                existing_event = find_event_in_list(event, common_events)
                if existing_event and existing_event.EventName != event.EventName:
                    return dataclasses.replace(event, EventName=existing_event.EventName)
                else:
                    return event

            return [renamed_event(e) for e in events]

        # Ensure we haven't added this CPU already
        assert perf_cpu_name not in self.cpu_events

        # Get processed MRS data (in Perf event format)
        cpu_events, present_common_events = mrs_events_to_perf_events(mrs_cpu_events,
                                                                      mrs_common_events)

        # Add common events not present in Perf
        new_common = [e for e in filter_common(present_common_events)
                      if not find_event_in_list(e, self.common_microarch_events)]
        new_recommended = [e for e in filter_recommended(present_common_events)
                           if not find_event_in_list(e, self.recommended_events)]
        self.common_microarch_events = sort_by_event_code(self.common_microarch_events + new_common)
        self.recommended_events = sort_by_event_code(self.recommended_events + new_recommended)

        # Sanity check: No impdef events in common files
        assert not [e for e in self.common_microarch_events
                    if event_codes.is_impdef(e.numeric_code)]
        assert not [e for e in self.recommended_events if event_codes.is_impdef(e.numeric_code)]

        # Categorise and store CPU events
        all_common_perf_events = self.common_microarch_events + self.recommended_events
        # Must add categories before replacing event names otherwise looking up the categories by
        # event name doesn't work.
        cpu_events = add_categories(cpu_events, self.event_grouper, source_file_name)
        cpu_events = replace_event_name(cpu_events, all_common_perf_events)
        cpu_events = replace_arch_std_event(cpu_events, all_common_perf_events)
        self.cpu_events[perf_cpu_name] = cpu_events

        # Add CPU mapping
        self.mapfile.add_if_not_present(mrs_cpu_info, perf_cpu_name)

    def add_metrics(self,
                    mrs_cpu_info: mapping.MidrFields,
                    mrs_metrics: List[mrs_data.MrsMetric],
                    perf_cpu_name: str):
        def sort_by_metric_name(rows: List[PerfMetric]):
            return sorted(rows, key=lambda e: e.MetricName)

        # Ensure we haven't added this CPU already
        assert perf_cpu_name not in self.metrics

        # Get processed MRS data (in Perf event format)
        self.metrics[perf_cpu_name] = sort_by_metric_name(mrs_metrics_to_perf_metrics(mrs_metrics))

        # Add CPU mapping
        self.mapfile.add_if_not_present(mrs_cpu_info, perf_cpu_name)

    def write(self, perf_path: str):
        def write_json(data, path):
            class PerfJsonEncoder(json.JSONEncoder):
                """Use to_perf_dict to get data when writing PerfEvent objects"""

                def default(self, object):
                    if isinstance(object, PerfEvent) or isinstance(object, PerfMetric):
                        return object.to_perf_dict()
                    return super().default(object)

            with open(path, 'w') as f:
                json.dump(data, f, indent=4, cls=PerfJsonEncoder)
                f.write("\n")  # Perf JSON files tend to have a trailing new line

        def write_by_topic(events: List[PerfEvent], output_dir: str):
            events_by_topic: Dict[str, List[PerfEvent]] = {}

            for e in events:
                for t in e.Topics:
                    events_by_topic.setdefault(t, []).append(e)

            os.makedirs(output_dir, exist_ok=True)

            for topic, events in events_by_topic.items():
                file_name = topic + '.json'
                outfile = os.path.join(output_dir, file_name)
                write_json(events, outfile)

        def write_metrics(metrics: List[PerfMetric], output_dir: str):
            os.makedirs(output_dir, exist_ok=True)
            outfile = os.path.join(output_dir, METRICS_FILENAME)
            write_json(metrics, outfile)

        """
        Writes CPU events, common_microarch events, recommended_events, and mapfile.csv to the
        specified path
        """
        arm64_path = perf_arm64_path(perf_path)

        write_json(self.common_microarch_events, os.path.join(arm64_path,
                                                              COMMON_MICROARCH_FILENAME))
        write_json(self.recommended_events, os.path.join(arm64_path, RECOMMENDED_FILENAME))
        self.mapfile.write_fn(os.path.join(arm64_path, MAPFILE_FILENAME))

        for cpu, events in self.cpu_events.items():
            write_by_topic(events, os.path.join(arm64_path, "arm", cpu))

        for cpu, metrics in self.metrics.items():
            write_metrics(metrics, os.path.join(arm64_path, "arm", cpu))


def do_arm_data_mode(args, perf_data):
    mrs_cpu_mappings = mapping.read_mrs_cpu_info_dict(os.path.join(args.arm_data_path,
                                                                   mrs_data.CPUS_FILENAME))
    mrs_common_events = mrs_data.read_common_events(args.arm_data_path)

    for cpu in args.arm_data_cpus:
        if args.verbose:
            print(f"Processing {cpu}...")
        try:
            if ":" in cpu:
                mrs_cpu, perf_cpu = cpu.split(":", 1)
            else:
                mrs_cpu = perf_cpu = cpu
            # From cpus.json
            mrs_cpu_info = mrs_cpu_mappings[mrs_cpu]
            # From CPU's JSON file
            mrs_cpu_events = mrs_data.read_cpu_events(args.arm_data_path, mrs_cpu)

            perf_data.add_cpu(mrs_cpu_info, mrs_cpu_events, mrs_common_events, perf_cpu)
        except Exception as e:
            raise Exception("Exception while processing %s" % cpu) from e


def do_telemetry_mode(args, perf_data):
    def filter_common(events: List[MrsEvent]):
        return [e for e in events if e.common or e.recommended]

    def describe_common(events: List[MrsEvent]):
        """
        TODO: If telemetry-solution also includes the arch name for common events like:
              "arch_long_description, arch_short_description" then this function could replace title
              and description for just for those events. That way Perf's common and recommended
              files don't have arch specific descriptions in them.

              eg:
                title = e.arch_short_description, description = e.arch_long_description

              Until then use only the title from common events.
        """
        return [dataclasses.replace(e, description=e.title) for e in events]

    for cpu in args.telemetry_files:
        if args.verbose:
            print(f"Processing {cpu}...")
        try:
            if ":" in cpu:
                cpu, perf_cpu_name = cpu.split(":", 1)
                mrs_cpu_info = mapping.read_telemetry_cpu_id(cpu)
            else:
                mrs_cpu_info = mapping.read_telemetry_cpu_id(cpu)
                perf_cpu_name = mrs_cpu_info.name.lower().replace(" ", "-")

            mrs_events = mrs_data.read_telemetry_events(cpu)
            mrs_metrics = mrs_data.read_telemetry_metrics(cpu)

            common_events = describe_common(filter_common(mrs_events))
            perf_data.add_cpu(mrs_cpu_info, mrs_events, common_events, perf_cpu_name, cpu)
            perf_data.add_metrics(mrs_cpu_info, mrs_metrics, perf_cpu_name)
        except Exception as e:
            raise Exception("Exception while processing %s" % cpu) from e


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("perf_path", help="Path to Linux Perf (linux/tools/perf) directory to "
                                          "read/write PMU event files")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--arm-data-path",
                       help=("Run in Arm-data mode. Path to root of Arm data repo check out (e.g. "
                             "a clone of https://github.com/ARM-software/data)"))
    group.add_argument("--telemetry-files", nargs="+",
                       help=("Run in Telemetry mode. List of telemetry-solution json files. e.g "
                             "'~/Downloads/neoverse_n1_pmu_specification.json ...'. The Perf name "
                             "is taken from the CPU name field by replacing ' ' with '-'. Or can be"
                             " overridden by specifying a new name after a colon like: "
                             "'n1.json:neoverse-n1'"))
    parser.add_argument("--arm-data-cpus", nargs="+", type=str,
                        help=("List of CPU names to generate data for in Arm-data mode. e.g. "
                              "'neoverse-n1 ...'. The Perf name can be overriden by "
                              "specifying a new name after a colon like: 'neoverse-n1:cortex-a76'"))
    parser.add_argument("--no-groups", action="store_true", help="Don't group events")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # Read initial data for arm-data mode
    if args.arm_data_path:
        if args.arm_data_cpus is None:
            parser.error("Arm-data mode requires both --arm-data-cpus and --arm-data-path")

        perf_data = PerfData(args.perf_path, None if args.no_groups else ArmDataEventGrouper())
        do_arm_data_mode(args, perf_data)
    else:
        perf_data = PerfData(args.perf_path, None if args.no_groups else TelemetryEventGrouper())
        do_telemetry_mode(args, perf_data)

    perf_data.write(args.perf_path)


if __name__ == "__main__":
    main()
