# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
This module implements the CpuProbe class, which is responsible for capturing and analyzing CPU
performance metrics. CpuProbe interacts with a TelemetryDatabase to retrieve events, metrics, and groups,
It leverages a PerfFactory to construct concrete Perf instances for recording hardware counters,
automatically selecting the appropriate backend (e.g., LinuxPerf or WindowsPerf).
The module integrates CPU-specific profiling logic into the larger telemetry/profiling system,
supporting use cases such as system-wide performance data capture and detailed metric computation via defined formulas.
"""

import re
from os.path import join
import logging
from sys import platform
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)

from rich import get_console
from topdown_tool.common import normalize_str, range_encode
from topdown_tool.cpu_probe.common import (
    COMBINED_STAGES,
    DEFAULT_ALL_STAGES,
    CpuAggregate,
    CpuProbeConfiguration,
    CpuEventOptions
)
from topdown_tool.cpu_probe.cpu_cli_renderer import CpuCliRenderer
from topdown_tool.cpu_probe.cpu_csv_renderer import CpuCsvRenderer  # added import
from topdown_tool.perf.event_scheduler import EventScheduler
from topdown_tool.cpu_probe.cpu_telemetry_database import (
    Event,
    Group,
    GroupLike,
    GroupView,
    Metric,
    TelemetryDatabase,
    TopdownMethodology,
)
from topdown_tool.perf import Cpu, PerfRecordLocation
import topdown_tool.probe as Base
from topdown_tool.perf.perf import Perf
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.perf import perf_factory, PerfFactory
from topdown_tool.common import simple_maths
from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification

logger = logging.getLogger(__name__)

# Each tuple of Event(s) is uniquely mapped to a tuple of captured float values.
EventResults = Dict[Tuple[Event, ...], Tuple[Optional[float], ...]]

# For each location, a set of results with their timestamp.
EventTimedResults = Dict[Optional[float], EventResults]

# Global performance mapping for all locations.
EventRecords = Dict[PerfRecordLocation, EventTimedResults]

# Computed metrics mapping
ComputedMetrics = Dict[
    PerfRecordLocation,
    Dict[
        Union[float, None],
        Dict[GroupLike, Dict[Metric, Union[float, None]]],
    ],
]


class CpuProbe(Base.Probe):
    """CPU Probe for capturing and processing CPU performance telemetry.

    This class implements the public probe API to capture CPU performance events, compute metrics
    using predefined formulas, and optionally output the results in various formats. It supports different
    capture modes based on configuration options and integrates with a Perf instance for event recording.
    """

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def __init__(
        self,
        conf: CpuProbeConfiguration,
        spec: TelemetrySpecification,
        core_indices: List[int],
        capture_data: bool,
        base_csv_dir: Optional[str],
        perf_factory_instance: "PerfFactory" = perf_factory,
    ):
        """Initializes a CpuProbe instance.

        Args:
            conf: CPU probe configuration.
            spec: Telemetry specification containing events and metric definitions.
            core_indices: List of CPU cores to profile.
            capture_data: Flag indicating whether to capture performance data.
            perf_factory_instance: Factory used to instantiate the appropriate Perf implementation.

        Usage Example:
            probe = CpuProbe(conf, spec, [0, 1], True, perf_factory)
        """
        super().__init__()

        self._conf = conf
        self._product_name = spec.product_configuration.product_name
        self._cores = sorted(core_indices)
        self._max_events = perf_factory_instance.get_pmu_counters(self._cores[0])
        self._perf_factory = perf_factory_instance
        self._pid: Set[int] = set()
        self._pid_tracking: bool = False
        self._perf_instance: Optional[Perf] = None
        self._capture_data: bool = capture_data
        self._base_csv_dir: Optional[str] = base_csv_dir

        options = CpuEventOptions(modifiers=self._conf.events_modifiers)

        self._db: TelemetryDatabase = TelemetryDatabase(spec, options)

        self._cli_renderer = CpuCliRenderer(get_console(), self._db)
        self._csv_renderer = CpuCsvRenderer()

        if not self._capture_data:
            return

        self._capture_groups: List[GroupLike] = self._build_capture_groups(
            conf=self._conf,
            db=self._db,
            max_events=self._max_events,
            cores=self._cores,
        )

        # If no valid groups/node for this CPU, skip capture for this probe
        if not self._capture_groups and (self._conf.metric_group or self._conf.node):
            if self._conf.metric_group:
                cpu_prefix = f'CPU "{self._product_name}"'
                cores_label = range_encode(self._cores)
                if cores_label:
                    cpu_prefix = f"{cpu_prefix} cores [{cores_label}]"
                logger.warning(
                    "%s: no valid metric groups from --cpu-metric-group. Skipping capture for this CPU.",
                    cpu_prefix,
                )
            # When node is invalid, a warning is already emitted in _build_capture_groups
            self._capture_data = False
            return

        # Create the scheduler based on the capture group
        self._event_scheduler = EventScheduler[Event](
            [group.metric_event_tuples() for group in self._capture_groups],
            self._conf.collect_by,
            self._max_events,
        )
        self._capture_it = self._event_scheduler.get_event_group_iterator(
            split=not self._conf.multiplex
        )

        # Instantiate the platform Perf once up-front and enable it
        self._perf_instance = self._perf_factory.create()
        if platform == "win32":
            cast(WindowsPerf, self._perf_instance).use_parser_for_class(self.__class__)
        self._perf_instance.enable()

        # Declare the perf records
        self._event_records: EventRecords = {}

        # Declare the computed metrics
        self.computed_metrics: ComputedMetrics = {}

    # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    @staticmethod
    def _build_capture_groups(
        conf: CpuProbeConfiguration,
        db: TelemetryDatabase,
        max_events: int,
        cores: Optional[Sequence[int]],
    ) -> List[GroupLike]:
        capture_groups: List[GroupLike] = []

        if conf.cpu_dump_events is not None:
            capture_groups = list(db.get_all_events_groups(max_events))
        elif conf.metric_group:
            found: List[GroupLike] = []
            missing: List[Tuple[str, Optional[str]]] = []
            for name in conf.metric_group:
                grp = db.find_group(name)
                if grp is not None:
                    found.append(grp)
                else:
                    missing.append((name, db.get_close_group_match(name)))
            if missing:
                cpu_prefix = f'CPU "{db.product_name}"'
                cores_label = range_encode(cores or [])
                if cores_label:
                    cpu_prefix = f"{cpu_prefix} cores [{cores_label}]"
                # Aggregate unknown names, include suggestions when available
                parts = [
                    f'"{name}" (did you mean "{sug}"?)' if sug else f'"{name}"'
                    for name, sug in missing
                ]
                logger.warning(
                    "%s: ignoring unknown --cpu-metric-group values: %s",
                    cpu_prefix,
                    ", ".join(parts),
                )
            capture_groups = found
        elif conf.node:
            node = db.topdown.find_node(conf.node)
            if node is None:
                cpu_prefix = f'CPU "{db.product_name}"'
                cores_label = range_encode(cores or [])
                if cores_label:
                    cpu_prefix = f"{cpu_prefix} cores [{cores_label}]"
                suggestion = db.get_close_metric_match(conf.node)
                if suggestion:
                    logger.warning(
                        '%s: node "%s" not found. Did you mean "%s"? Skipping capture for this CPU.',
                        cpu_prefix,
                        conf.node,
                        suggestion,
                    )
                else:
                    logger.warning(
                        '%s: node "%s" not found. Skipping capture for this CPU.',
                        cpu_prefix,
                        conf.node,
                    )
                return []

            def collect_metrics_and_groups(
                node: TopdownMethodology.Node,
                res: Dict[Group, Optional[List[Metric]]],
            ) -> Dict[Group, Optional[List[Metric]]]:
                if node.group not in res:
                    res[node.group] = [node.metric]
                else:
                    group_metrics = res[node.group]
                    if group_metrics is not None and node.metric not in group_metrics:
                        group_metrics.append(node.metric)

                for child in node.children:
                    if isinstance(child, Group):
                        res[child] = None
                    else:
                        collect_metrics_and_groups(child, res)

                return res

            # Convert the group mapping into a list of groups and subgroups
            for group, metrics in collect_metrics_and_groups(node, {}).items():
                if metrics:
                    capture_groups.append(GroupView.from_group(group, metrics))
                else:
                    capture_groups.append(group)
        else:  # We are looking at the stages
            if conf.stages in (DEFAULT_ALL_STAGES, COMBINED_STAGES):
                capture_groups = list(db.topdown.stage_1_groups + db.topdown.stage_2_groups)
            else:
                if 1 in conf.stages:
                    capture_groups.extend(db.topdown.stage_1_groups)
                if 2 in conf.stages:
                    capture_groups.extend(db.topdown.stage_2_groups)

        return capture_groups

    def name(self) -> str:
        """Returns the short name of the probe.

        Returns:
            A string representing the probe name ("CPU").
        """
        return "CPU"

    def need_capture(self) -> bool:
        """Determines if there are remaining event groups to capture.

        Returns:
            True if additional data capture is required; False otherwise.
        """
        if not self._capture_data:
            return False

        return self._capture_it.has_next()
        # return len(self.schedule) > 0

    def start_capture(self, run: int = 1, pids: Optional[Union[int, Set[int]]] = None) -> None:
        """Starts the capture session for performance events.

        Args:
            run: The current run iteration (default is 1).
            pid: Optional process ID or set of process IDs to monitor.

        Raises:
            Exception: When system-wide profiling with multiple runs is attempted without multiplexing.
            Exception: When multiple PIDs are provided in an unsupported configuration.

        Inline Notes:
            - Retrieves the current schedule chunk and initializes a Perf instance.
            - Builds the output filename based on the product name and scheduling index.
        """
        if not self._capture_data:
            return

        if self._capture_it.remaining_chunks() >= 2:
            if pids is None:
                raise RuntimeError(
                    f"Can't do system-wide profiling with multiple runs. Allow {self.name()} events multiplexing or choose fewer metrics to collect."
                )

            if isinstance(pids, set):
                raise RuntimeError(
                    f"Can't do external PID monitoring with multiple runs. Allow {self.name()} events multiplexing or choose fewer metrics to collect."
                )

        if pids is not None:
            if isinstance(pids, int):
                self._pid = {pids}
            else:
                self._pid = set(pids)
        else:
            self._pid = set()

        # FIXME: PID tracking is not possible yet, due to Windows Perf limitations
        self._pid_tracking = self._conf.pid_tracking_applicable and len(self._pid) == 1 and platform != "win32"

        index = self._capture_it.index()
        schedule_unit = next(self._capture_it)

        product_name = normalize_str(self._product_name).replace(" ", "_")
        filename: str = f"perf.stat.cpu.{product_name}.{index}.txt"
        assert self._perf_instance is not None
        self._perf_instance.start(
            tuple(schedule_unit),
            filename,
            next(iter(self._pid)) if self._pid_tracking else None,
            cores=self._cores if not self._pid_tracking else None,
        )

    def stop_capture(
        self, run: int = 1, pid: Optional[int] = None, interrupted: bool = False
    ) -> None:
        """Stops the capture session and processes the performance results.

        Args:
            run: The current run iteration (default is 1).
            pid: Optional process ID to stop monitoring.
            interrupted: Flag indicating if the capture was interrupted.

        Raises:
            AssertionError: If no Perf instance exists when attempting to stop capture.

        Inline Notes:
            - Merges the newly captured results into existing records.
            - Once all capture groups have been processed, aggregate metrics are computed.
        """
        if not self._capture_data:
            return

        assert self._perf_instance is not None

        # Remove pid from a set of monitored processes
        if pid is not None:
            self._pid.discard(pid)

        # More processes to monitor, exit early
        if self._pid:
            return

        # If there are no more processes to monitor, stop perf and get output
        self._perf_instance.stop()
        results = self._perf_instance.get_perf_result()

        # first we need to update the local perf_records with the one acquired by perf
        def dict_deep_merge(reference: Dict[Any, Any], new: Dict[Any, Any]) -> Dict[Any, Any]:
            for new_key, new_value in new.items():
                if new_key in reference:
                    # We do not expect to override values here, that would be a programming error
                    assert isinstance(new_value, dict)
                    reference[new_key] = dict_deep_merge(reference[new_key], new_value)
                else:
                    reference[new_key] = new_value
            return reference

        self._event_records = cast(EventRecords, dict_deep_merge(self._event_records, results))

        # If more capture groups remain:
        #   • normal case → return and keep collecting
        #   • interrupted → compute partial output now
        if self._capture_it.has_next() and not interrupted:
            return

        # If this was the last run for this probe (or interrupted), mark as no more runs
        try:
            self._perf_instance.disable()
        except Exception:  # pylint: disable=broad-except
            pass
        # All the events have been captured, start their aggregation then compute the metrics
        self._update_aggregate()
        self.computed_metrics = self._compute_metrics(
            self._capture_groups, self._event_records, self._event_scheduler
        )
        return

    # pylint: disable=too-many-locals
    @staticmethod
    def _compute_aggregate(
        event_records: EventRecords, recorded_groups: Sequence[Tuple[Event, ...]]
    ) -> Tuple[CpuAggregate, EventTimedResults]:
        # Compute aggregate over the provided records and groups. In input the events
        # recorded by perf and the events groups that were recorded are present.
        # This function create a CpuAggregate that represents all the Cpus in the input
        # and the TimedResults which is the aggregation of all the events values in the
        # input.
        cpus = tuple(sorted(k for k in event_records.keys() if isinstance(k, Cpu)))
        out_loc = CpuAggregate(cpus)
        out_timed_records: EventTimedResults = {}
        # Reconstruct the output based on the groupds requested
        for in_group in recorded_groups:
            for in_loc, in_timed_records in event_records.items():
                # Skip non Cpu records
                if in_loc not in out_loc.cpus:
                    continue
                # traverse each timed records
                for in_time, in_records in in_timed_records.items():
                    # retrieve/create the aggregate records
                    out_records = out_timed_records.setdefault(in_time, {})
                    # If the group is missing in this record, fill with None. it may happen if the record
                    # was incomplete/
                    if in_group not in in_records:
                        out_records[in_group] = tuple(None for _ in range(len(in_group)))
                        continue
                    # Retrieve the aggregated value or set its default values to zeroes
                    current_tuple = out_records.setdefault(
                        in_group, tuple(0 for _ in range(len(in_group)))
                    )
                    out_record_result = list(current_tuple)
                    # Update the aggregate record record being iterated.
                    for i, v in enumerate(in_records[in_group]):
                        current_value = out_record_result[i]
                        if v is None or current_value is None:
                            out_record_result[i] = None
                        else:
                            out_record_result[i] = current_value + v
                    out_records[in_group] = tuple(out_record_result)
        return out_loc, out_timed_records

    def _update_aggregate(self) -> None:
        # Skip aggregation if only one core is present.
        if self._pid_tracking or len(self._cores) <= 1:
            return
        # Update the instance's event_records with the computed aggregate
        aggregate, aggregated_records = self._compute_aggregate(
            self._event_records, self._event_scheduler.optimized_event_groups
        )
        self._event_records[aggregate] = aggregated_records

    @staticmethod
    def _compute_metrics(
        metric_groups: List[GroupLike],
        records: EventRecords,
        scheduler: EventScheduler,
    ) -> ComputedMetrics:
        # Regex to match symbols (event names) in the formula
        output: ComputedMetrics = {}
        for loc, timed_results in records.items():
            output[loc] = {}
            for time, perf_result in timed_results.items():
                output[loc][time] = {}
                for grp in metric_groups:
                    output[loc][time][grp] = {}
                    for metric in grp.metrics:
                        output[loc][time][grp][metric] = CpuProbe._compute_metric(
                            scheduler, perf_result, grp, metric
                        )
        return output

    @staticmethod
    def _compute_metric(
        scheduler: EventScheduler,
        perf_result: EventResults,
        grp: GroupLike,
        metric: Metric,
    ) -> Optional[float]:
        pattern = re.compile(r"[a-zA-Z_]\w*")
        result_val: Optional[float] = None

        # Try to retrieve the values for this metric; if the group never appeared
        # (e.g., partial/interrupt), treat the metric as not computed.
        # This might happen with windows perf if it returns partial result
        try:
            values = scheduler.retrieve_event_results(
                perf_result, grp.metric_event_tuples(), metric.events
            )
        except KeyError:
            values = None

        if values is None:
            # Group missing entirely → leave result_val as None
            pass
        elif any(v is None for v in values):
            # Some required event didn’t produce a value → None
            result_val = None
        else:
            mapping = {ev.name: str(v) for ev, v in zip(metric.events, values)}

            def repl(match: re.Match) -> str:
                token = match.group(0)
                return mapping.get(token, token)

            substituted_formula = pattern.sub(repl, metric.formula)
            try:
                result_val = simple_maths.evaluate(substituted_formula)
            except Exception:  # pylint: disable=broad-exception-caught
                result_val = None

        return result_val

    def output(self) -> None:
        """Outputs the captured performance data or lists available events/metrics/groups.

        Depending on the configuration, this method either renders events, metrics, or groups via the
        CLI renderer, or writes raw data to CSV files.

        Inline Notes:
            - Uses conditions to select the output format (e.g. CSV dump, tree view, etc.).
        """
        if self._conf.cpu_list_groups:
            self._cli_renderer.list_groups(self._conf.descriptions, self._conf.stages)

        if self._conf.cpu_list_metrics:
            self._cli_renderer.list_metrics(self._conf.descriptions, self._conf.show_sample_events)

        if self._conf.cpu_list_events:
            self._cli_renderer.list_events(self._conf.descriptions)

        if not self._capture_data:
            return

        if ("events" in self._conf.cpu_generate_csv) or self._conf.cpu_dump_events:
            assert self._base_csv_dir is not None
            self._csv_renderer.dump_events(
                self._event_records, self._db, self._product_name, join(self._base_csv_dir, "cpu")
            )

        # Bail early if only the events had to be captured
        if self._conf.cpu_dump_events:
            return

        if "metrics" in self._conf.cpu_generate_csv:
            assert self._base_csv_dir is not None
            self._csv_renderer.render_metric_groups(
                self.computed_metrics,
                self._capture_groups,
                self._db,
                join(self._base_csv_dir, "cpu"),
            )

        # Suppress CLI rendering when any CSV flag is set AND the computed metrics contain timeline data.
        # We infer timeline presence if any time key is not None. In non-timeline runs, each location maps
        # to a single None key. If needed in future, we can normalize empty mappings to {None: {...}} here.
        has_timeline = any(
            (time is not None)
            for loc_map in self.computed_metrics.values()
            for time in loc_map.keys()
        )
        if self._conf.cpu_generate_csv and has_timeline:
            return
        if self._conf.stages is COMBINED_STAGES or self._conf.node is not None:
            self._cli_renderer.render_metrics_tree(
                self.computed_metrics,
                self._conf.descriptions,
                self._conf.node,
            )
        else:
            self._cli_renderer.render_metric_groups_stages(
                self.computed_metrics, self._capture_groups, self._conf.descriptions
            )
