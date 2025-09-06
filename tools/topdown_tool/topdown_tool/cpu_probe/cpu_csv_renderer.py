import os
import csv
from typing import Dict, Union, List, Optional, Tuple, cast

# Type aliases (as in the original code)
from topdown_tool.cpu_probe.common import CpuAggregate
from topdown_tool.cpu_probe.cpu_telemetry_database import (
    Event,
    Group,
    GroupView,
    Metric,
    TelemetryDatabase,
    TopdownMethodology,
    GroupLike,
)
from topdown_tool.perf import Cpu, PerfRecordLocation, Uncore
from topdown_tool.common import range_encode


# Helper: DFS to find target node level in the TopdownMethodology tree.
def _dfs_find_level(
    node: TopdownMethodology.Node,
    target_metric: Metric,
    target_group: Group,
    current_level: int,
    visited: set,
) -> Optional[int]:
    if node in visited:
        return None
    visited.add(node)
    # If node matches metric and group, return current depth.
    if node.metric.name == target_metric.name and node.group.name == target_group.name:
        return current_level
    for child in node.children:
        if hasattr(child, "group"):
            res = _dfs_find_level(
                cast(TopdownMethodology.Node, child),
                target_metric,
                target_group,
                current_level + 1,
                visited,
            )
            if res is not None:
                return res
    return None


def get_node_level(topdown: TopdownMethodology, group: Group, metric: Metric) -> Optional[int]:
    # Iterate over all root nodes; roots are defined by topdown.root_metrics.
    for root_metric in topdown.root_metrics:
        root_node = topdown.nodes.get(root_metric.name)
        if root_node is None:
            continue
        level = _dfs_find_level(root_node, metric, group, 0, set())
        if level is not None:
            return level
    return None


class CpuCsvRenderer:
    # pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
    def render_metric_groups(
        self,
        computed_metrics: Dict[
            PerfRecordLocation,
            Dict[Union[float, None], Dict[GroupLike, Dict[Metric, Union[float, None]]]],
        ],
        capture_groups: List[GroupLike],
        db: TelemetryDatabase,
        output_dir: str,
    ) -> None:
        # Ensure output directory exists.
        os.makedirs(output_dir, exist_ok=True)

        for loc, time_dict in computed_metrics.items():
            # Determine filename based on PerfRecordLocation type.
            if isinstance(loc, Cpu):
                filename = f"core_{loc.id}.csv"
            elif isinstance(loc, CpuAggregate):
                # Use range_encode to encode the aggregate CPU ids.
                cpu_range = range_encode([cpu.id for cpu in loc.cpus])
                filename = f"aggregate_({cpu_range}).csv"
            elif isinstance(loc, Uncore):
                filename = "results.csv"
            else:
                continue

            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w", newline="", encoding="utf-8") as csv_file:
                writer = csv.writer(csv_file)
                # Write header
                writer.writerow(["time", "group", "stage", "level", "metric", "value", "units"])

                # For each timestamp in the computed metrics.
                for t, groups_data in time_dict.items():
                    # Iterate over each capture group provided.
                    for cap_group in capture_groups:
                        # Check if the current capture group exists in computed results.
                        # Using group name comparison to manage potential GroupView wrapping.
                        group_name = cap_group.name
                        # Iterate over groups in computed result.
                        for grp_key, metrics_data in groups_data.items():
                            if grp_key.name != group_name:
                                continue
                            # Determine group stage.
                            # For GroupView, use its original group.
                            if isinstance(cap_group, GroupView):
                                underlying = cap_group.original
                            else:
                                underlying = cap_group
                            stage = ""
                            if underlying in db.topdown.stage_1_groups:
                                stage = "1"
                            elif underlying in db.topdown.stage_2_groups:
                                stage = "2"

                            # For stage_1 groups, compute level per metric.
                            for metric, value in metrics_data.items():
                                level = ""
                                if stage == "1":
                                    level_val = get_node_level(db.topdown, underlying, metric)
                                    if level_val is not None:
                                        # Convert to 1-indexed.
                                        level = str(level_val + 1)
                                writer.writerow(
                                    [
                                        str(t) if t is not None else "",
                                        cap_group.name,
                                        stage,
                                        level,
                                        metric.name,
                                        str(value) if value is not None else "",
                                        metric.units,
                                    ]
                                )

    def dump_events(
        self,
        event_records: Dict[
            PerfRecordLocation,
            Dict[Union[float, None], Dict[Tuple[Event, ...], Tuple[Optional[float], ...]]],
        ],
        _db: TelemetryDatabase,
        product_name: str,
        output_dir: str,
    ) -> None:
        os.makedirs(output_dir, exist_ok=True)
        for loc, events_values_for_core in event_records.items():
            if isinstance(loc, Cpu):
                core_id = f"{loc.id}"
                filename = f"{product_name.lower().replace(' ', '_')}_core_{core_id}.csv"
            elif isinstance(loc, CpuAggregate):
                core_id = f"aggregate_({range_encode([cpu.id for cpu in loc.cpus])})"
                filename = f"{product_name.lower().replace(' ', '_')}_core_{core_id}.csv"
            elif isinstance(loc, Uncore):
                filename = f"{product_name.lower().replace(' ', '_')}_results.csv"
            else:
                continue

            filepath = os.path.join(output_dir, filename)
            with open(filepath, "w", encoding="utf-8") as csv_file:
                csv_writer = csv.writer(csv_file)
                csv_writer.writerow(["run", "time", "event", "value"])
                for time, event_results in events_values_for_core.items():
                    for events, results in event_results.items():
                        for i, event in enumerate(events):
                            csv_writer.writerow(
                                [
                                    1,
                                    time if time is not None else "",
                                    event.name,
                                    results[i],
                                ]
                            )
