# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Union, TypeVar
from rich.console import Console
from rich.table import Table
from rich.tree import Tree
from topdown_tool.common import range_encode
from topdown_tool.cpu_probe.common import CpuAggregate
from topdown_tool.cpu_probe.cpu_telemetry_database import (
    Group,
    GroupLike,
    Metric,
    TelemetryDatabase,
    TopdownMethodology,
)
from topdown_tool.layout.layout import SplitTable
from topdown_tool.perf import PerfRecordLocation, Cpu


RawComputedGroups = Dict[
    PerfRecordLocation,
    Dict[
        Union[float, None],
        Dict[GroupLike, Dict[Metric, Union[float, None]]],
    ],
]

ComputedAggregateGroups = Dict[CpuAggregate, Dict[GroupLike, Dict[Metric, Union[float, None]]]]
ComputedCpuGroups = Dict[Cpu, Dict[GroupLike, Dict[Metric, Union[float, None]]]]

T = TypeVar("T", ComputedAggregateGroups, ComputedCpuGroups)


def format_value(val: Optional[float], unit: str) -> str:
    """Format a numerical value for display depending on the metric unit.

    Args:
        val: The numerical value to format, or None if unavailable.
        unit: The metric's units (string).

    Returns:
        A string representing the value, formatted to appropriate precision, or
        a cross-symbol if value is None.

    """
    if val is None:
        return "❌"
    if unit.lower().startswith("percent"):
        return f"{val:.2f}"
    return f"{val:.3f}"


def adjust_unit(unit: str) -> str:
    """Adjust human-friendly string for metric units.

    Args:
        unit: The unit string from the metric specification.

    Returns:
        Adjusted unit string, e.g. percent as "%", "MPKI" expanded, or original unit if no remapping.

    """
    lower = unit.lower()
    if lower.startswith("percent"):
        return "%"
    if unit == "MPKI":
        return "misses per 1,000 instructions"
    return unit


def format_top_level_title(title: str) -> str:
    """Format a string as a top-level heading using rich color tags.

    Args:
        title: The title string.

    Returns:
        Formatted string for rich output with cyan color and bold font.
    """
    return f"[bold cyan]{title}[/bold cyan]"


@dataclass
class ComputedGroups:
    """Holds processed mapping of computed metric results for aggregate and per-core views.

    Attributes:
        aggregate: Mapping of CpuAggregate to metric group data.
        cpu: Mapping of Cpu to metric group data.

    Methods:
        from_raw: Constructs a ComputedGroups instance from raw computation output.

    """

    aggregate: ComputedAggregateGroups
    cpu: ComputedCpuGroups

    @staticmethod
    def from_raw(raw: RawComputedGroups) -> "ComputedGroups":
        """Convert RawComputedGroups into separate aggregate and per-CPU results.

        Args:
            raw: The nested mapping as computed by metric computation logic.

        Returns:
            A ComputedGroups containing separated aggregate and per-core results.

        """
        aggregated_results: ComputedAggregateGroups = {}
        core_results: ComputedCpuGroups = {}
        for loc, timed in raw.items():
            results = timed.get(None, {})
            if isinstance(loc, CpuAggregate):
                aggregated_results[loc] = results
            elif isinstance(loc, Cpu):
                core_results[loc] = results

        return ComputedGroups(aggregated_results, core_results)


class MetricTableBuilder:
    """Builds Rich tables for displaying metric group results in rich terminal format.

    Methods:
        __init__: Create a MetricTableBuilder.
        add_row: Add a metric/group row to the table.
        get_table: Get the constructed SplitTable for rendering.

    """

    def __init__(
        self,
        results: ComputedGroups,
        include_descriptions: bool,
        include_metric_names: bool = True,
    ):
        """Initialize a table builder for metric group results.

        Args:
            results: Processed computed groups for aggregate and per-core.
            include_descriptions: Whether to include descriptions in output.
            include_metric_names: Whether to include metric names in output table.

        """
        self.results = results
        self.include_descriptions = include_descriptions
        self.include_metric_names = include_metric_names
        header_prefixes = ["Metric"] if include_metric_names else []
        headers = self._get_table_headers()
        header_suffixes = ["Unit"] + (["Description"] if include_descriptions else [])
        self.table = SplitTable(
            header_prefixes=header_prefixes,
            headers=headers,
            header_suffixes=header_suffixes,
        )

    def _get_aggregated_headers(self) -> List[str]:
        return [
            f"Aggregated ({range_encode([cpu.id for cpu in loc.cpus])})"
            for loc in self.results.aggregate.keys()
        ]

    def _get_cpu_headers(self) -> List[str]:
        return [f"#{cid.id}" for cid in sorted(self.results.cpu.keys())]

    def _get_table_headers(self) -> List[str]:
        return self._get_aggregated_headers() + self._get_cpu_headers()

    def _get_table_values(self, group: GroupLike, metric: Metric) -> List[str]:
        def lookup_value(group_dict: dict) -> Optional[float]:
            # Find the key matching the group's name.
            key = next((g for g in group_dict if g.name == group.name), None)
            return group_dict.get(key, {}).get(metric, None) if key is not None else None

        def build_values(src: T) -> List[str]:
            return [
                format_value(lookup_value(src.get(k, {})), metric.units) for k in sorted(src.keys())
            ]

        agg_vals = build_values(self.results.aggregate)
        cpu_vals = build_values(self.results.cpu)
        return agg_vals + cpu_vals

    def add_row(self, group: GroupLike, metric: Metric) -> None:
        """Add a table row for the specified group and metric.

        Args:
            group: Group or GroupView the metric belongs to.
            metric: Metric to retrieve from the results and add.

        """
        unit_str = adjust_unit(metric.units)
        desc_str = metric.description if self.include_descriptions else ""
        self.table.add_row(
            row_prefixes=[metric.title] if self.include_metric_names else [],
            row=self._get_table_values(group, metric),
            row_suffixes=[unit_str] + ([desc_str] if self.include_descriptions else []),
        )

    def get_table(self) -> SplitTable:
        """Return the constructed SplitTable object."""
        return self.table


def build_metric_table(
    results: ComputedGroups,
    group: GroupLike,
    metrics: Sequence[Metric],
    descriptions: bool,
    metric_names: bool = True,
) -> SplitTable:
    """Construct a SplitTable for a metric group.

    Args:
        results: ComputedGroups instance with aggregate/cpu values.
        group: Group or GroupView instance.
        metrics: Sequence of metrics to include in the table.
        descriptions: Whether to include metric descriptions.
        metric_names: Whether to display the metric names as a column.

    Returns:
        A ready-to-render SplitTable.

    """
    table_builder = MetricTableBuilder(results, descriptions, metric_names)
    for m in metrics:
        table_builder.add_row(group, m)
    return table_builder.get_table()


class CpuCliRenderer:
    """Responsible for rendering CPU probe data and configuration using rich.

    Args:
        console: A rich Console for rendering outputs.
        db: The TelemetryDatabase for events, metrics, and topology.

    Methods:
        list_events: Display table of all CPU performance monitoring events.
        list_metrics: Display the full metric tree (topdown and other groups).
        list_groups: Render a table of metric groups, optionally including descriptions.
        render_metric_groups_stages: Render a tree/table organized by topdown/uarch/general groups.
        render_metric_groups_tree: Render combined stages in tree form depending on node.
    """

    def __init__(self, console: Console, db: TelemetryDatabase) -> None:
        """Initialize the CLI renderer.

        Args:
            console: The Rich Console instance for output.
            db: The loaded TelemetryDatabase with all metric/event configuration.
        """
        self.console = console
        self.db = db

    def list_events(self, include_description: bool) -> None:
        """Display the list of CPU events as a rich table.

        Args:
            include_description: If True, display event descriptions as a column.

        """
        console = self.console
        columns = ["Code", "Event", "Title"]
        if include_description:
            columns.append("Description")
        table = Table(title=format_top_level_title(f"CPU {self.db.product_name} events"))
        for column in columns:
            table.add_column(column)
        for event in self.db.events.values():
            row = [hex(event.code), event.name, event.title]
            if include_description:
                row.append(event.description)
            table.add_row(*row)
        console.print(table)

    def list_metrics(self, include_description: bool, include_sample_events: bool) -> None:
        """Display all CPU metrics in a tree view organized by stage/group.

        Args:
            include_description: Whether to include descriptions in metric tables.
            include_sample_events: Whether to include a column listing sample events.

        """
        console = self.console
        # Create a tree with CPU as root.
        tree = Tree(format_top_level_title(f"CPU {self.db.product_name} metrics"))

        # Define stage groups.
        stage1_groups = self.db.topdown.stage_1_groups
        stage2_groups = self.db.topdown.stage_2_groups
        all_groups = list(self.db.groups.values())
        non_topdown_groups = [
            grp for grp in all_groups if grp not in stage1_groups and grp not in stage2_groups
        ]

        # Add standalone metrics as a general group if any.
        standalone_metrics = [m for m in self.db.metrics.values() if len(m.groups) == 0]
        if standalone_metrics:
            standalone_metrics_group = Group(
                name="STANDALONE_METRICS",
                title="Standalone Metrics",
                description="Metrics not part of any group",
                metrics=tuple(standalone_metrics),
            )
            non_topdown_groups.append(standalone_metrics_group)

        # Helper to add a stage branch.
        def add_stage(stage_title: str, groups: list) -> None:
            if not groups:
                return
            stage_node = tree.add(f"[bold magenta]{stage_title}[/bold magenta]")  # Stage in magenta
            for group in groups:
                group_node = stage_node.add(
                    f"[bold yellow]{group.title} ({group.name})[/bold yellow]"
                )  # Group in yellow
                group_node.add(group.description)
                table = Table(show_header=True, header_style="bold")
                table.add_column("Key", no_wrap=True)
                table.add_column("Metric")
                if include_description:
                    table.add_column("Description")
                if include_sample_events:
                    table.add_column("Sample events")
                for metric in group.metrics:
                    row = [metric.name, metric.title]
                    if include_description:
                        row.append(metric.description)
                    if include_sample_events:
                        sample_str = ", ".join(e.name for e in metric.sample_events)
                        row.append(sample_str)
                    table.add_row(*row)
                group_node.add(table)

        add_stage("Stage 1 (Topdown metrics)", self.db.topdown.stage_1_groups)
        add_stage("Stage 2 (uarch metrics)", self.db.topdown.stage_2_groups)
        add_stage("General", non_topdown_groups)
        console.print(tree)

    def list_groups(self, include_description: bool, include_stages: List[int]) -> None:
        """Render a table of metric groups, optionally restricting by stage and including descriptions.

        Args:
            include_description: Whether to show group descriptions.
            include_stages: List of integer stage numbers to include (empty = all).

        """
        console = self.console
        columns = ["Key", "Group"]
        if include_description:
            columns.append("Description")
        table = Table(title=format_top_level_title(f"CPU {self.db.product_name} groups"))
        for column in columns:
            table.add_column(column)
        for name, group in self.db.groups.items():
            if include_stages and self.db.topdown.get_stage_for_group(name) not in include_stages:
                continue
            row = [name, group.title]
            if include_description:
                row.append(group.description)
            table.add_row(*row)
        console.print(table)

    def render_metric_groups_stages(
        self,
        computed: RawComputedGroups,
        capture_groups: List[GroupLike],
        include_descriptions: bool,
    ) -> None:
        """Render a tree/table showing metrics grouped by stage (topdown, uarch, or general).

        Args:
            computed: Computed metric values (raw mapping).
            capture_groups: List of metric groups (GroupLike) to display.
            include_descriptions: Whether to render descriptions for each metric.

        """
        console = self.console

        # Simplify the data structure as we have no timed values and we want to operate on groups and aggregate
        group_results = ComputedGroups.from_raw(computed)

        # Organize capture groups by stage.
        stage1_groups: List[GroupLike] = []
        stage2_groups: List[GroupLike] = []
        general_groups: List[GroupLike] = []
        for group in capture_groups:
            stage = self.db.topdown.get_stage_for_group(group.name)
            if stage == 1:
                stage1_groups.append(group)
            elif stage == 2:
                stage2_groups.append(group)
            else:
                general_groups.append(group)

        tree = Tree(format_top_level_title(f"CPU {self.db.product_name} metrics"))

        def render_stage(stage_title: str, groups: List[GroupLike]) -> None:
            if not groups:
                return
            stage_branch = tree.add(f"[bold magenta]{stage_title}[/bold magenta]")
            for group in groups:
                group_branch = stage_branch.add(
                    f"[bold yellow]{group.title} ({group.name})[/bold yellow]"
                )
                parents = self.db.topdown.get_all_parents(group)
                if parents:
                    parents_node = group_branch.add("Follows")
                    for parent in parents:
                        parents_node.add(f"{parent.metric.title} ({parent.metric.name})")

                group_branch.add(
                    build_metric_table(group_results, group, group.metrics, include_descriptions)
                )

        render_stage("Stage 1 (Topdown metrics)", stage1_groups)
        render_stage("Stage 2 (uarch metrics)", stage2_groups)
        render_stage("Non-Topdown metrics", general_groups)
        console.print(tree)

    # pylint: disable=too-many-locals
    def render_metrics_tree(
        self,
        computed: RawComputedGroups,
        include_descriptions: bool,
        root_node: Optional[str],
    ) -> None:
        """Render the full topdown tree, or a subtree for a specific node, with metrics in a rich tree/table.

        Args:
            computed: Computed metrics dictionary (raw results).
            include_descriptions: Whether to display metric descriptions.
            root_node: If specified, only display this metric node/subtree.

        """
        console = self.console

        # Simplify the data structure as we have no timed values and we want to operate on groups and aggregate
        group_results = ComputedGroups.from_raw(computed)

        root = Tree(format_top_level_title(f"CPU {self.db.product_name} topdown"))
        topdown_node = root.add("[bold magenta]Topdown metrics[/bold magenta]")

        rendered_groups = set()  # Record groups rendered in topdown

        # Recursive function to traverse the topdown tree nodes.
        def traverse_topdown(node: TopdownMethodology.Node, tree_node: Tree) -> None:
            node_branch = tree_node.add(f"{node.metric.title} ({node.metric.name})")
            node_branch.add(
                build_metric_table(
                    group_results,
                    node.group,
                    [node.metric],
                    include_descriptions,
                    metric_names=False,
                )
            )
            for child in node.children:
                if isinstance(child, TopdownMethodology.Node):
                    traverse_topdown(child, node_branch)
                else:
                    # Render the Group similar to render_metric_groups_stages.
                    rendered_groups.add(child.name)
                    group_branch = node_branch.add(f"{child.title} ({child.name})")
                    group_branch.add(
                        build_metric_table(
                            group_results, child, child.metrics, include_descriptions
                        )
                    )

        if root_node is not None:
            root_metrics = [root_node]
        else:
            root_metrics = [m.name for m in self.db.topdown.root_metrics]

        for root_metric in root_metrics:
            node = self.db.topdown.find_node(root_metric)
            if node:
                traverse_topdown(node, topdown_node)

        # Render uarch standalone metrics: only groups not rendered in topdown.
        if root_node is None:
            uarch_node = root.add("[bold magenta]uarch standalone metrics[/bold magenta]")
            for group in self.db.topdown.stage_2_groups:
                if group.name in rendered_groups:
                    continue
                group_branch = uarch_node.add(f"{group.title} ({group.name})")
                group_branch.add(
                    build_metric_table(group_results, group, group.metrics, include_descriptions)
                )

        console.print(root)
