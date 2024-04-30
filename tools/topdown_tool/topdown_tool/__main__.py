#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

import sys

if sys.version_info < (3, 7):
    print("Python 3.7 or later is required to run this script.", file=sys.stderr)
    sys.exit(1)

# Allow relative imports when running file/package directly (not as a module).
if __name__ == "__main__" and not __package__:
    __package__ = "topdown_tool"  # pylint: disable=redefined-builtin
    import os.path
    sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import csv
import logging
import subprocess
import textwrap
from re import Match
from typing import Dict, Generator, Iterable, List, Optional, Sequence, Tuple, Union

from . import cpu_mapping, simple_maths
from .event_collection import (CPU_PMU_COUNTERS, CollectBy, EventCount, GroupScheduleError, MetricScheduleError, PerfOptions, UncountedEventsError,
                               ZeroCyclesError, collect_events, format_command, get_pmu_counters)
from .metric_data import (IDENTIFIER_REGEX, AnyMetricInstance, AnyMetricInstanceOrValue, CombinedMetricInstance, Group, MetricData, MetricInstance,
                          MetricInstanceValue)

# Constants for nested printing
INDENT_LEVEL = 2
STAGE_LABELS = {0: "uncategorised", 1: "Topdown", 2: "uarch"}
DESCRIPTION_LINE_LENGTH = 80

# Default stages, unless levels or metric groups are specified
DEFAULT_ALL_STAGES = [1, 2]
COMBINED_STAGES: List[int] = []


def calculate_metrics(event_counts: List[EventCount], metric_instances: Iterable[MetricInstance]):
    """Calculate metric values from perf stat event data."""

    output: List[MetricInstanceValue] = []

    for mi in metric_instances:
        events = [e for e in event_counts if (e.event.metric == mi.metric or e.event.metric is None) and (e.event.group == mi.group or e.event.group is None)]
        formula = mi.metric.formula

        def event_value(match: Match):
            event = next(e for e in events if e.event.event.name == match.group(0))
            return str(event.value)
        formula = IDENTIFIER_REGEX.sub(event_value, mi.metric.formula)

        value = simple_maths.evaluate(formula)
        output.append(MetricInstanceValue(metric_instance=mi, value=value))

    return output


def indent_lines(text: str, indent: int, line_length=100):
    """Indent all lines in `text` by `indent` spaces"""

    def wrap_line(line: str):
        return "\n".join(" " * indent + line for line in textwrap.wrap(line, line_length - indent))

    return "\n".join(wrap_line(line) for line in text.splitlines())


def generate_metric_values(metric_instances: Iterable[AnyMetricInstanceOrValue]) -> Generator[Tuple[AnyMetricInstance, Optional[float]], None, None]:
    for mi in metric_instances:
        if isinstance(mi, MetricInstanceValue):
            yield (mi.metric_instance, mi.value)
        else:
            yield (mi, None)


# pylint: disable=too-many-branches
def print_nested_metrics(metric_instances: Iterable[AnyMetricInstanceOrValue],
                         stages: List[int],
                         show_descriptions: bool,
                         show_sample_events: bool):

    last_group: Dict[int, Group] = {}  # level => group
    last_level = -1
    last_stage = -1

    if not metric_instances:  # e.g. trying to display stages on a group without a stage
        print("No metrics to display")
        return

    if show_descriptions:
        max_width = DESCRIPTION_LINE_LENGTH
    else:
        max_width = max(
            INDENT_LEVEL * (getattr(mi, "level", 1) - 1) + max(len(mi.metric.title), len(mi.group.title) + 2)
            for (mi, _) in generate_metric_values(metric_instances)
        )

    for (instance, value) in generate_metric_values(metric_instances):
        instance_level = get_level(instance, 1)  # Flatten level 2 CombinedMetricInstances
        indent = INDENT_LEVEL * (instance_level - 1)

        if stages and instance.stage != last_stage:
            if last_stage != -1:
                print()
            heading = f"Stage {instance.stage} ({STAGE_LABELS[instance.stage]} metrics)"
            print(f"{heading}\n{'=' * len(heading)}")
            last_level = -1

        # On level-change, clear previous description
        if show_descriptions and instance_level != last_level and last_level != -1:
            print()

        assert instance.group
        if instance.group is not last_group.get(instance_level) and instance.group is not last_group.get(instance_level - 1):
            if instance_level == last_level:
                print()

            group_types = f"{' ' * (max_width-indent-2-len(instance.group.title))} [{STAGE_LABELS[instance.stage]} group]" if not stages else ""
            print(indent_lines(f"[{instance.group.title}]{group_types}", indent))
            if (stages and 1 in stages) and isinstance(instance, CombinedMetricInstance):
                for parent in instance.parents:
                    print(indent_lines(f"(follows {parent.metric.title})", indent + INDENT_LEVEL))
            if show_descriptions:
                print(indent_lines(instance.group.description, indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        if value is not None:
            print(indent_lines(f"{instance.metric.title.ljust(max_width-indent, '.')} {instance.metric.format_value(value)}", indent))
        else:
            print(indent_lines(instance.metric.title, indent))

        if show_descriptions:
            print(indent_lines(instance.metric.description, indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        if show_sample_events and instance.sample_events:
            print(indent_lines("Sample events: " + ", ".join(e.name for e in instance.sample_events), indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        last_group[instance_level] = instance.group
        last_level = instance_level
        last_stage = instance.stage


# pylint: disable=too-many-statements
def get_arg_parser():
    class ProcessStageArgs(argparse.Action):
        stage_names = {"topdown": 1, "uarch": 2, "1": 1, "2": 2}

        def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Union[str, Sequence, None], option_string: Optional[str] = None) -> None:
            if isinstance(values, str):
                if values.lower() == "all":
                    value = DEFAULT_ALL_STAGES
                elif values.lower() == "combined":
                    value = COMBINED_STAGES
                else:
                    try:
                        value = sorted(set(ProcessStageArgs.stage_names[x.lower().strip()] for x in values.split(",")))
                    except KeyError as e:
                        parser.error(f'"{e.args[0]}" is not a valid stage name.')

            else:
                assert False
            setattr(namespace, self.dest, value)

    def pid_list(arg: str):
        return [int(p) for p in arg.split(",")]

    def collect_by_value(arg: str):
        return CollectBy(arg.lower())

    def positive_nonzero_int(arg: str):
        try:
            val = int(arg)
            if val > 0:
                return val
        except ValueError:
            pass

        raise argparse.ArgumentTypeError(f"invalid positive int value: '{arg}'")

    class PlatformArgumentParser(argparse.ArgumentParser):
        """ArgumentParser that allows platform-specific arguments."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.default_namespace = argparse.Namespace()

        def add_linux_argument(self, *args, **kwargs):
            """
            Adds an argument that only appears on Linux.

            On Windows, the default value will be added to the default namespace.
            """
            if sys.platform == "linux":
                self.add_argument(*args, **kwargs)
            else:
                # Add default argument values to default namespace
                longest_name = max(args, key=len).lstrip(self.prefix_chars).replace("-", "_")
                setattr(self.default_namespace,
                        kwargs.get("dest", longest_name),
                        kwargs.get("default"))

        def add_argument_group(self, *args, **kwargs):
            group = super().add_argument_group(*args, **kwargs)
            setattr(group, "add_linux_argument", self.add_linux_argument)
            return group

        def parse_args(self, args):  # pylint: disable=arguments-differ
            return super().parse_args(args, self.default_namespace)

    if sys.platform == "linux":
        default_perf_path = "perf"
        default_perf_output = "perf.stat.txt"
    else:
        default_perf_path = "wperf"
        default_perf_output = "wperf.json"

    parser = PlatformArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_linux_argument("command", default=[], nargs=argparse.REMAINDER, help='command to analyse. Subsequent arguments are passed as program arguments. e.g. "sleep 10"')
    parser.add_linux_argument("--all-cpus", "-a", action="store_true", help="System-wide collection for all CPUs.")
    parser.add_linux_argument("--pid", "-p", type=pid_list, dest="pids", help='comma separated list of process IDs to monitor.')
    parser.add_argument("--perf-path", default=default_perf_path, help="path to perf executable")
    parser.add_argument("--perf-args", type=str, help="additional command line arguments to pass to Perf")
    parser.add_argument("--cpu", help="CPU name to use to look up event data (auto-detect by default)")
    query = parser.add_argument_group("query options")
    query.add_argument("--list-cpus", action="store_true", help="list available CPUs and exit")
    query.add_argument("--list-groups", action="store_true", help="list available metric groups and exit")
    query.add_argument("--list-metrics", action="store_true", help="list available metrics and exit")
    collection_group = parser.add_argument_group("collection options")
    collection_group.add_argument("-c", "--collect-by", type=collect_by_value, choices=list(CollectBy), default=CollectBy.METRIC, help='when multiplexing, collect events grouped by "none", "metric" (default), or "group". This can avoid comparing data collected during different time periods.')
    collection_group.add_argument("--max-events", type=positive_nonzero_int, help="Maximum simultaneous events. If more events are required, <command> will be run multiple times.")
    collection_group.add_argument("-m", "--metric-group", dest="metric_groups", type=lambda x: x.split(","), help="comma separated list of metric groups to collect. See --list-groups for available groups")
    collection_group.add_argument("-n", "--node", help='name of topdown node as well as its descendants (e.g. "frontend_bound"). See --list-metrics for available nodes')
    collection_group.add_argument("-l", "--level", type=int, choices=[1, 2], help=argparse.SUPPRESS)
    collection_group.add_argument("-s", "--stages", action=ProcessStageArgs, default=DEFAULT_ALL_STAGES, help='control which stages to display, separated by a comma. e.g. "topdown,uarch". "all" may also be specified, or "combined" to display all, but without separated the output in to stages.')
    collection_group.add_linux_argument("-i", "-I", "--interval", type=int, help="Collect/output data every <interval> milliseconds")
    collection_group.add_argument("--use-event-names", action="store_true", help='use event names rather than event codes (e.g. "r01") when collecting data from perf. This can be useful for debugging.')
    collection_group.add_argument("--core", "-C", help="count only on the list of CPUs provided. Multiple CPUs can be provided as a comma-separated list with no space.")
    output_group = parser.add_argument_group("output options")
    output_group.add_argument("-d", "--descriptions", action="store_true", help="show group/metric descriptions")
    output_group.add_argument("--show-sample-events", action="store_true", help="show sample events for metrics")
    output_group.add_argument("--perf-output", default=default_perf_output, help="output file for perf event data")
    output_group.add_argument("--csv", help="output file for metric CSV data")
    output_group.add_argument("-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO, help="enable verbose output")
    output_group.add_argument("--debug", action="store_const", dest="loglevel", const=logging.DEBUG, help="enable debug output")

    # Debug option to generate dummy data without collect event data. This uses 0 for metric values.
    parser.add_argument("--dummy-data", action="store_true", help=argparse.SUPPRESS)

    return parser


# instances without a level (CombinedMetricInstance) are uarch metrics (2)
def get_level(instance: AnyMetricInstance, default_level=2):
    return getattr(instance, "level", default_level)


def write_csv(timed_metric_values: Iterable[Tuple[Optional[float], Iterable[MetricInstanceValue]]], filename: str):
    with open(filename, "w") as f:  # pylint: disable=unspecified-encoding
        writer = csv.writer(f)
        writer.writerow(["time", "level", "stage", "group", "metric", "value", "units"])
        for (time, metric_values) in timed_metric_values:
            for (instance, value) in metric_values:
                group = instance.group.title if instance.group else ""
                writer.writerow([time, get_level(instance), instance.stage, group, instance.metric.title, value, instance.metric.units])


# pylint: disable=too-many-branches,too-many-statements,too-many-locals
def main(args=None):
    parser = get_arg_parser()
    args = parser.parse_args(args)

    logging.basicConfig(level=args.loglevel)

    if args.list_cpus:
        print("\n".join(MetricData.list_cpus()))
        sys.exit(0)

    # Handle mutually exclusive arguments
    if args.command and args.pids:
        parser.error("Cannot specify a command and a PID")
    if len([x for x in [args.metric_groups, args.level, args.node] if x]) > 1:
        parser.error("Only one metric group or topdown metric can be specified.")
    if args.interval and not args.csv:
        parser.error("Interval mode must be used with CSV output.")

    # Get CPU metric data
    cpu = args.cpu
    if not cpu:
        cpu = cpu_mapping.get_cpu(perf_path=args.perf_path)
        if not cpu:
            print("Could not detect CPU. Specify via --cpu", file=sys.stderr)
            sys.exit(1)
    try:
        if cpu == "mapping":  # Special-case to handle collision with mapping file
            parser.error(f'no data for "{cpu}" CPU')

        metric_data = MetricData(cpu)
    except FileNotFoundError:
        parser.error(f'no data for "{cpu}" CPU')

    if args.list_groups:
        for name, group in metric_data.groups.items():
            if args.stages and metric_data.topdown.get_stage(name) not in args.stages:
                continue
            print(f"{name} ({group.title})")
            if args.descriptions:
                print(" " * INDENT_LEVEL + group.description)
        sys.exit(0)
    elif args.list_metrics:
        metrics = metric_data.all_metrics(args.stages)
        print_nested_metrics(metrics, args.stages, args.descriptions, args.show_sample_events)
        sys.exit(0)

    if args.metric_groups:
        metric_instances = []
        for group_name in args.metric_groups:
            if metric_data.find_group(group_name):
                metric_instances += metric_data.metrics_for_group(group_name)
            else:
                suggestion = metric_data.get_close_group_match(group_name)
                suggestion = f' Did you mean "{suggestion}"?' if suggestion else ""
                parser.error(f'"{group_name}" is not a valid group.{suggestion}')
    elif args.level:
        metric_instances = metric_data.metrics_up_to_level(args.level)
    elif args.node:
        metric_instances = metric_instances = metric_data.metrics_descended_from(args.node)
        if not metric_instances:
            suggestion = metric_data.get_close_metric_match(args.node)
            suggestion = f' Did you mean {suggestion}?' if suggestion else ""
            parser.error(f'"{args.node}" is not a valid metric.{suggestion}')
    else:
        metric_instances = metric_data.all_metrics(args.stages)

    if not metric_instances:
        print("No metrics to collect.", file=sys.stderr)
        sys.exit(1)

    try:
        perf_options = PerfOptions.from_args(args)
        if not args.dummy_data:
            stat_data = collect_events(metric_instances, perf_options)
    except GroupScheduleError as e:
        print(f'The "{e.group.title}" group contains {len(e.events)} unique events, but only {min(e.available_events, get_pmu_counters(cpu, args.perf_path))} can be collected at once.\n\nChoose different groups/metrics or avoid collecting by group.', file=sys.stderr)
        sys.exit(1)
    except MetricScheduleError as e:
        print(f'The "{e.metric.name}" metric contains {len(e.events)} unique events, but only {min(e.available_events, get_pmu_counters(cpu, args.perf_path))} can be collected at once.\n\nChoose different metrics or avoid collecting by metric.', file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f'"{format_command(e.cmd)}" finished with exit code {e.returncode}.', file=sys.stderr)
        sys.exit(1)
    except UncountedEventsError as e:
        print(("The following events could not be counted:\n  %s\n\n"
               "This can be caused by insufficient time to collect information on all events.\n" % "\n  ".join(e.uncounted_events)), file=sys.stderr)
        if perf_options.interval:
            print("Try extending the interval period, or reducing the number of collected events.", file=sys.stderr)
        elif perf_options.command:
            print("Try running a longer running program, or reducing the number of collected events.", file=sys.stderr)
        else:
            print("Try collecting more output, or reducing the number of events collected.", file=sys.stderr)
        sys.exit(1)
    except ZeroCyclesError:
        print(f"A cycle count of zero was detected while collecting events. This likely indicates an issue with Linux Perf's ability to correctly count PMU events.\n\n"
              f"This may be related to known issues with multiplexing in a virtualised environment.\n\n"
              f"You may be able to work-around this by avoiding multiplexing. e.g. by specifying --max-events={CPU_PMU_COUNTERS}.", file=sys.stderr)
        sys.exit(1)

    if args.dummy_data:
        metric_values = [MetricInstanceValue(mi) for mi in metric_instances]
        timed_metric_values = [(None, metric_values)]
    else:
        timed_metric_values = []
        for (time, event_counts) in stat_data.items():
            metric_values = calculate_metrics(event_counts, metric_instances)
            logging.debug("\n".join(f"{v.metric_instance.group.name}/{v.metric_instance.metric.name} = {v.value}" for v in metric_values))

            timed_metric_values.append((time, metric_values))

    if args.interval:
        print(f'See "{args.csv}" for interval data.')
    else:
        print_nested_metrics(metric_values, args.stages, args.descriptions, args.show_sample_events)

    if args.csv:
        write_csv(timed_metric_values, args.csv)


if __name__ == "__main__":
    main()
