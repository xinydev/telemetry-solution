#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import sys

if sys.version_info < (3, 7):
    print("Python 3.7 or later is required to run this script.", file=sys.stderr)
    sys.exit(1)

import argparse
import csv
import logging
import subprocess
import textwrap
from re import Match
from typing import Dict, Iterable, List, Optional, Sequence, Union

import cpu_mapping
import simple_maths
from event_collection import (CollectBy, CollectionEventCount,
                              GroupScheduleError, MetricScheduleError,
                              PerfOptions, UncountedEventsError,
                              collect_events, field_dict)
from metric_data import (IDENTIFIER_REGEX, CombinedMetricInstance, CombinedMetricInstanceValue, MetricData,
                         MetricInstance, MetricInstanceValue, SeparateMetricInstance, combine_instances)

# Constants for nested printing
INDENT_LEVEL = 2
STAGE_LABELS = {0: "uncategorised", 1: "Topdown", 2: "uarch"}
DESCRIPTION_LINE_LENGTH = 80

# Default stages, unless levels or metric groups are specified
DEFAULT_ALL_STAGES = [1, 2]


def calculate_metrics(stat_data: List[CollectionEventCount], metric_instances: Iterable[MetricInstance]):
    """Calcaulte metric values from perf stat event data."""

    events_dict: Dict[str, float] = {}
    for event in stat_data:
        assert event.qualified_name not in events_dict, event  # No duplicate events
        events_dict[event.qualified_name] = event.value

    output: List[MetricInstanceValue] = []

    for mi in metric_instances:
        events = [e for e in stat_data if (e.metric == mi.metric or e.metric is None) and (e.group == mi.group or e.group is None)]
        formula = mi.metric.formula

        def event_value(match: Match):
            event = next(e for e in events if e.name == match.group(0))
            return str(event.value)
        formula = IDENTIFIER_REGEX.sub(event_value, mi.metric.formula)

        value = simple_maths.evaluate(formula)
        output.append(MetricInstanceValue(**field_dict(mi), value=value))

    return output


def indent_lines(text: str, indent: int, line_length=100):
    """Indent all lines in `text` by `indent` spaces"""

    def wrap_line(line: str):
        return "\n".join(" " * indent + line for line in textwrap.wrap(line, line_length - indent))

    return "\n".join(wrap_line(line) for line in text.splitlines())


def print_nested_metrics(metric_instances: Iterable[SeparateMetricInstance],
                         stages: List[int],
                         show_descriptions: bool,
                         show_sample_events: bool):

    last_group: Dict[int, str] = {}
    last_level = -1
    last_stage = -1

    if stages:
        metric_instances = ([i for i in metric_instances if i.stage == 1 and 1 in stages]
                            + combine_instances([i for i in metric_instances if i.stage == 2 and 2 in stages]))

    if not metric_instances:  # e.g. trying to display stages on a group without a stage
        print("No metrics to display")
        return

    if show_descriptions:
        max_width = DESCRIPTION_LINE_LENGTH
    else:
        max_width = max(
            max(INDENT_LEVEL * (getattr(instance, "level", 1) - 1) + len(instance.metric.title) for instance in metric_instances),
            max(INDENT_LEVEL * (getattr(instance, "level", 1) - 1) + len(instance.group.title) + 2 for instance in metric_instances if instance.group)
        )

    for instance in metric_instances:
        instance_level = getattr(instance, "level", 1)
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
        if last_group.get(instance_level) != instance.group.title:
            if instance_level == last_level:
                print()

            group_types = f"{' ' * (max_width-indent-2-len(instance.group.title))} [{STAGE_LABELS[instance.stage]} group]" if not stages else ""
            print(indent_lines(f"[{instance.group.title}]{group_types}", indent))
            if (stages and 1 in stages) and isinstance(instance, (CombinedMetricInstance, CombinedMetricInstanceValue)):
                for parent in instance.parents:
                    print(indent_lines(f"(follows {parent.metric.title})", indent + INDENT_LEVEL))
            if show_descriptions:
                print(indent_lines(instance.group.description, indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        if isinstance(instance, (MetricInstanceValue, CombinedMetricInstanceValue)):
            print(indent_lines(f"{instance.metric.title.ljust(max_width-indent, '.')} {instance.metric.format_value(instance.value)}", indent))
        else:
            print(indent_lines(instance.metric.title, indent))

        if show_descriptions:
            print(indent_lines(instance.metric.description, indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        if show_sample_events and instance.sample_events:
            print(indent_lines("Sample events: " + ", ".join(e.name for e in instance.sample_events), indent + INDENT_LEVEL, DESCRIPTION_LINE_LENGTH))

        last_group[instance_level] = instance.group.title
        last_level = instance_level
        last_stage = instance.stage


def get_arg_parser():
    class ProcessStageArgs(argparse.Action):
        stage_names = {"topdown": 1, "uarch": 2, "1": 1, "2": 2}

        def __call__(self, parser: argparse.ArgumentParser, namespace: argparse.Namespace, values: Union[str, Sequence, None], option_string: Optional[str] = None) -> None:
            if isinstance(values, str):
                if values.lower() == "all":
                    value = DEFAULT_ALL_STAGES
                elif values.lower() == "combined":
                    value = []
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

    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", default=None, nargs=argparse.REMAINDER, help='command to analyse. Subsequent arguments are passed as program arguments. e.g. "sleep 10"')
    parser.add_argument("--all-cpus", "-a", action="store_true", help="System-wide collection for all CPUs.")
    parser.add_argument("--pid", "-p", type=pid_list, dest="pids", help='comman separated list of process IDs to monitor.')
    parser.add_argument("--perf-args", help="additional command line arguments to pass to Perf")
    parser.add_argument("--cpu", help="CPU name to use to look up event data (auto-detect by default)")
    query = parser.add_argument_group("query options")
    query.add_argument("--list-cpus", action="store_true", help="list available CPUs and exit")
    query.add_argument("--list-groups", action="store_true", help="list available metric groups and exit")
    query.add_argument("--list-metrics", action="store_true", help="list available metrics and exit")
    collection_group = parser.add_argument_group("collection options")
    collection_group.add_argument("-c", "--collect-by", type=collect_by_value, choices=list(CollectBy), default=CollectBy.METRIC, help='when multiplexing, collect events groupped by "none", "metric" (default), or "group". This can avoid comparing data collected during different time periods.')
    collection_group.add_argument("--max-events", type=int, help="Maximum simultaneous events. If more events are required, <command> will be run multiple times. ")
    collection_group.add_argument("--raw", action="store_true", help='pass raw event code to perf. e.g. "r01"')
    collection_group.add_argument("-m", "--metric-group", dest="metric_groups", type=lambda x: x.split(","), help="comma separated list of metric groups to collect. See --list-groups for available groups")
    collection_group.add_argument("-n", "--node", help='name of topdown node as well as its descendents (e.g. "frontend_bound"). See --list-metrics for available nodes')
    collection_group.add_argument("-l", "--level", type=int, choices=[1, 2], help=argparse.SUPPRESS)
    collection_group.add_argument("-s", "--stages", action=ProcessStageArgs, nargs="?", help='control which stages to display, separated by a comma. e.g. "topdown,uarch". "all" may also be specified, or "combined" to display all, but without separated the output in to stages.')
    output_group = parser.add_argument_group("output options")
    output_group.add_argument("-d", "--descriptions", action="store_true", help="show group/metric descriptions")
    output_group.add_argument("--show-sample-events", action="store_true", help="show sample events for metrics")
    output_group.add_argument("--perf-output", default="perf.stat.txt", help="output file for perf event data")
    output_group.add_argument("--csv", help="output file for metric CSV data")
    output_group.add_argument("-v", "--verbose", action="store_const", dest="loglevel", const=logging.INFO, help="enable verbose output")
    output_group.add_argument("--debug", action="store_const", dest="loglevel", const=logging.DEBUG, help="enable debug output")
    return parser


def write_csv(metric_values: List[MetricInstanceValue], filename: str):
    with open(filename, "w") as f:  # pylint: disable=unspecified-encoding
        writer = csv.writer(f)
        writer.writerow(["level", "stage", "group", "metric", "value", "units"])
        for instance in metric_values:
            group = instance.group.title if instance.group else ""
            value = instance.value
            writer.writerow([instance.level, instance.stage, group, instance.metric.title, value, instance.metric.units])


def main():
    parser = get_arg_parser()
    args = parser.parse_args()

    logging.basicConfig(level=args.loglevel)

    if args.list_cpus:
        print("\n".join(MetricData.list_cpus()))
        sys.exit(0)

    # Handle mutually exclusive arguments
    if args.command and args.pids:
        parser.error("Cannot specify a command and a PID")
    if len([x for x in [args.metric_groups, args.level, args.node] if x]) > 1:
        parser.error("Only one metric group or topdown metric can be specified.")

    # Get CPU metric data
    cpu = args.cpu
    if not cpu:
        cpu = cpu_mapping.get_cpu()
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
        metrics = metric_data.all_metrics()
        print_nested_metrics(metrics, args.stages or DEFAULT_ALL_STAGES, args.descriptions, args.show_sample_events)
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
        if args.stages is None:  # distinguish from [] which denotes combined stages
            args.stages = DEFAULT_ALL_STAGES
        metric_instances = metric_data.all_metrics()

    if args.stages:
        metric_instances = [m for m in metric_instances if m.stage in args.stages]

    if not metric_instances:
        print("No metrics to collect.", file=sys.stderr)
        sys.exit(1)

    if args.raw and not metric_data.events:
        print(f"No event data available for {args.cpu}", file=sys.stderr)
        sys.exit(1)

    try:
        perf_options = PerfOptions.from_args(args)
        stat_data = collect_events(metric_instances, perf_options)
    except GroupScheduleError as e:
        print(f'The "{e.group.title}" group contains {len(e.events)} unique events, but only {e.available_events} can be collected at once.\n\nChoose different groups/metrics or avoid collecting by group.', file=sys.stderr)
        sys.exit(1)
    except MetricScheduleError as e:
        print(f'The "{e.metric.name}" metric contains {len(e.events)} unique events, but only {e.available_events} can be collected at once.\n\nChoose different metrics or avoid collecting by metric.', file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f'"{e.cmd}" finished with exit code {e.returncode}.', file=sys.stderr)
        sys.exit(1)
    except UncountedEventsError as e:
        print(("The following events could not be counted:\n  %s\n\n"
               "If you program completes very quickly, try running one that takes longer to complete." % "\n  ".join(e.uncounted_events)), file=sys.stderr)
        sys.exit(1)

    metric_values = calculate_metrics(stat_data, metric_instances)

    logging.debug("\n".join(f"{v.group.name}/{v.metric.name} = {v.value}" for v in metric_values))

    if args.csv:
        write_csv(metric_values, args.csv)

    print_nested_metrics(metric_values, args.stages, args.descriptions, args.show_sample_events)


if __name__ == "__main__":
    main()
