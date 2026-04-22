# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""CPU Telemetry Database Module.

This module provides the TelemetryDatabase class as the main entry point for all CPU
performance analysis operations. Users can query events, groups, metrics, and navigate
the top-down methodology decision tree through the TelemetryDatabase interface.

Intended usage:
    - Create a TelemetrySpecification from a JSON file.
    - Initialize TelemetryDatabase with the specification.
    - Access events, metrics, groups, and the top-down methodology tree.

Example:
    spec = TelemetrySpecification.load_from_json_file("path/to/config.json")
    db = TelemetryDatabase(spec)
    events = db.events
    groups = db.groups
    node = db.topdown.find_node("MetricName")
    # ... additional queries ...

The module is useful for integrating CPU performance specification into analysis tools.
"""

from dataclasses import dataclass
from difflib import get_close_matches
from functools import cached_property, total_ordering
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

from topdown_tool.common import normalize_str
from topdown_tool.cpu_probe.cpu_model import (
    TelemetrySpecification,
    TopdownMethodology as TopdownMethodologyModel,
)
from topdown_tool.cpu_probe.common import CpuEventOptions, CpuModifier
from topdown_tool.perf import PerfEvent


# Note: We can't use `order=True` otherwise python's type system scream at us.
# There is no elegant solution to overcome this issue.
@total_ordering
@dataclass(frozen=True)
class Event(PerfEvent):
    """Represents a performance-monitoring event.

    Attributes (data class properties):
        name (str): Property. Unique identifier of the event.
        title (str): Property. Short title describing the event.
        description (str): Property. Detailed description of the event.
        code (int): Property. Numeric event code.
    """

    name: str
    title: str
    description: str
    code: int
    modifiers: Optional[Tuple[CpuModifier, ...]]

    def perf_name(self) -> str:
        """Return a performance-compatible event name."""
        modifiers: Optional[str] = None
        if self.modifiers is not None and len(self.modifiers) > 0:
            modifiers = "".join(map(str, self.modifiers))
        return f"r{self.code:x}{':' + modifiers if modifiers is not None else ''}"

    def __repr__(self) -> str:
        return self.name

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return (self.name, self.title, self.description, self.code) == (
            other.name,
            other.title,
            other.description,
            other.code,
        )

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        return (self.name, self.title, self.description, self.code) < (
            other.name,
            other.title,
            other.description,
            other.code,
        )

    def __hash__(self) -> int:
        return hash((self.name, self.title, self.description, self.code))


@dataclass(frozen=True, order=True)
class Metric:
    """Represents a computed metric based on one or more events.

    Attributes (data class properties):
        db (TelemetryDatabase): Property. The telemetry database instance.
        name (str): Property. Unique metric identifier.
        title (str): Property. Title of the metric.
        description (str): Property. Detailed metric description.
        units (str): Property. Unit in which the metric is expressed.
        formula (str): Property. Formula for computing the metric.
        events (Tuple[Event, ...]): Property. Tuple of events contributing to this metric.
        sample_events (Tuple[Event, ...]): Property. Tuple of events used for demonstration purposes.

    Computed Attributes (cached_property):
        stage (int): Cached property. Determines the stage (1 or 2) of the metric based on its group.
        groups (Tuple[Group, ...]): Cached property. Returns all groups that include this metric.
    """

    db: "TelemetryDatabase"
    name: str
    title: str
    description: str
    units: str
    formula: str
    events: Tuple[Event, ...]
    sample_events: Tuple[Event, ...]

    def __post_init__(self) -> None:
        # Sort events to ensure consistent ordering.
        sorted_ev = tuple(sorted(self.events))
        object.__setattr__(self, "events", sorted_ev)
        sorted_sample = tuple(sorted(self.sample_events))
        object.__setattr__(self, "sample_events", sorted_sample)

    @cached_property
    def stage(self) -> int:
        """Determine the stage (1 or 2) of the metric based on its group."""
        for group in self.db.topdown.stage_1_groups:
            if self in group.metrics:
                return 1

        for group in self.db.topdown.stage_2_groups:
            if self in group.metrics:
                return 2

        return 0

    @cached_property
    def groups(self) -> Tuple["Group", ...]:
        """Return all groups that include this metric."""
        return tuple(group for group in self.db.groups.values() if self in group.metrics)

    def __repr__(self) -> str:
        return self.name


@dataclass(frozen=True, order=True)
class Group:
    """Represents a grouping of metrics.

    Attributes (data class properties):
        name (str): Property. Unique identifier for the group.
        title (str): Property. Human-readable title.
        description (str): Property. Description of what the group represents.
        metrics (Tuple[Metric, ...]): Property. Tuple of metrics contained in the group.

    Computed Attributes (cached_property):
        events (Tuple[Event, ...]): Cached property. Sorted tuple of unique events from the group's metrics.
    """

    name: str
    title: str
    description: str
    metrics: Tuple[Metric, ...]

    def __post_init__(self) -> None:
        # Sort metrics by name to ensure consistent ordering.
        sorted_metrics = tuple(sorted(self.metrics, key=lambda m: m.name))
        object.__setattr__(self, "metrics", sorted_metrics)

    @cached_property
    def events(self) -> Tuple[Event, ...]:
        """Return a sorted tuple of unique events from the group's metrics."""
        unique_events = {e for m in self.metrics for e in m.events}
        sorted_events = sorted(unique_events)
        return tuple(sorted_events)

    def __repr__(self) -> str:
        return self.name

    def metric_event_tuples(self) -> Sequence[Tuple[Event, ...]]:
        """Return a sequence where each tuple contains the events for a metric in this group."""
        return [m.events for m in self.metrics]


@dataclass(frozen=True)
class GroupView:
    """Provides a filtered view of a Group.

    This view restricts the metrics of a Group to a selected subset and exposes
    all attributes of the underlying Group via __getattr__. In effect, all the properties
    defined in the Group dataclass (e.g. name, title, description, metrics, and events)
    are accessible through the GroupView instance.

    Instances should be created using the `from_group` class method.

    Computed Attributes:
        events (Tuple[Event, ...]): Cached property. Sorted tuple of unique events from the view's metrics.
        original (Group): Property. Returns the original Group instance.
    """

    _orig: Group
    _metrics: Tuple[Metric, ...]

    @classmethod
    def from_group(cls, grp: Group, keep: Sequence[Metric]) -> "GroupView":
        """Creates a view of the given Group with a restricted set of metrics.

        Args:
            grp: The original Group.
            keep: The list of metrics to be included in the view.

        Returns:
            A GroupView instance that behaves like the original Group with only the specified metrics.
        """
        intersect = tuple(m for m in (set(keep) & set(grp.metrics)))
        assert len(intersect) == len(keep)
        return cls(grp, intersect)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._orig, name)

    @property
    def metrics(self) -> Tuple[Metric, ...]:
        """Return the filtered metrics for the group view."""
        return self._metrics

    @cached_property
    def events(self) -> Tuple[Event, ...]:
        """Return a sorted tuple of unique events from the view's metrics."""
        unique_events = set(e for m in self.metrics for e in m.events)
        return tuple(sorted(unique_events))

    @property
    def original(self) -> Group:
        """Return the original Group instance."""
        return self._orig


# Type with a Group interface
GroupLike = Union[Group, GroupView]


class TopdownMethodology:
    """Contains the top-down analysis methodology and related decision tree.

    This class creates nodes for decision making within a top-down methodology tree.

    Attributes:
        db (TelemetryDatabase): Reference to the TelemetryDatabase.
        nodes (Dict[str, TopdownMethodology.Node]): Mapping of node names to their Node instances.
        root_metrics (Tuple[Metric]): Tuple of metrics representing the roots of the decision tree.
        stage_1_groups (Tuple[Group]): Tuple of groups assigned to stage 1.
        stage_2_groups (Tuple[Group]): Tuple of groups assigned to stage 2.
        _stage_for_group (Dict[str, int]): Mapping of group names to stage numbers determined from the decision tree.
        _node_normalized_keys (Dict[str, str]): Mapping of normalized node names to their actual names for lookup.
    """

    @dataclass(frozen=True, order=True)
    class Node:
        """Node in the top-down decision tree.

        Attributes:
            methodology (TopdownMethodology): The TopdownMethodology instance.
            name (str): Name of the node, corresponding to a metric.
            group (Group): The group associated with the node.
            next (Tuple[str, ...]): Tuple of names for the next nodes or groups.
            sample_events (Tuple[Event, ...]): Tuple of events used for demonstration.
        Computed Attributes (cached_property):
            children (LisTuplet[Union[Node, Group]]): Cached property. Tuple of child nodes or groups based on the 'next' links.
            metric (Metric): Cached property. Returns the metric associated with this node.
        """

        methodology: "TopdownMethodology"
        name: str
        group: Group
        next: Tuple[str, ...]
        sample_events: Tuple[Event, ...]

        @cached_property
        def children(self) -> Tuple[Union["TopdownMethodology.Node", Group], ...]:
            """Return a list of child nodes or groups based on the 'next' links."""
            result: List[Union["TopdownMethodology.Node", Group]] = []
            for child_name in self.next:
                if child_name in self.methodology.nodes:
                    result.append(self.methodology.nodes[child_name])
                else:
                    group = self.methodology.db.find_group(child_name)
                    assert group is not None
                    result.append(group)
            return tuple(result)

        @cached_property
        def metric(self) -> Metric:
            """Return the metric associated with this node."""
            m = self.methodology.db.find_metric(self.name)
            assert m is not None
            return m

        def _next_nodes(self) -> Sequence["TopdownMethodology.Node"]:
            # Return a list of subsequent Node instances referenced by the 'next' links.
            return [
                self.methodology.nodes[link] for link in self.next if link in self.methodology.nodes
            ]

        def _next_groups(self) -> Sequence[Group]:
            # Return a list of subsequent Group instances referenced by the 'next' links.
            return [
                self.methodology.db.groups[link]
                for link in self.next
                if link in self.methodology.db.groups
            ]

    def __init__(self, db: "TelemetryDatabase", data: TopdownMethodologyModel):
        """Initialize TopdownMethodology with database and methodology model data.

        Args:
            db (TelemetryDatabase): A TelemetryDatabase instance.
            data (TopdownMethodologyModel): Methodology configuration loaded from specification.

        Sets:
            self._stage_for_group (Dict[str, int]): Mapping of group names to stage numbers.
            self._node_normalized_keys (Dict[str, str]): Mapping of normalized node names to actual node names.
        """
        self.db = db
        self.nodes: Dict[str, TopdownMethodology.Node] = {
            metric.name: TopdownMethodology.Node(
                methodology=self,
                name=metric.name,
                next=tuple(metric.next_items),
                group=self.db.groups[metric.group],
                sample_events=tuple(
                    self.db.events[e] for e in metric.sample_events if e in self.db.events
                ),
            )
            for metric in data.decision_tree.metrics
        }
        self.root_metrics = [self.db.metrics[m] for m in data.decision_tree.root_nodes]

        # Initialize private fields
        self.stage_1_groups = [
            self.db.groups[group_name] for group_name in data.metric_grouping.stage_1
        ]
        self.stage_2_groups = [
            self.db.groups[group_name] for group_name in data.metric_grouping.stage_2
        ]

        self._stage_for_group: Dict[str, int] = {}
        for node in self.nodes.values():
            self._stage_for_group[node.group.name] = 1
            for uarch_group in node._next_groups():
                self._stage_for_group[uarch_group.name] = 2

        self._node_normalized_keys: Dict[str, str] = {
            normalize_str(k): k for k in self.nodes.keys()
        }

    def get_stage_for_group(self, group_name: str) -> int:
        """Return the stage number for a specified group.

        Args:
            group_name: The name of the group.

        Returns:
            The stage number (default is 2 if not explicitly mapped).
        """
        return self._stage_for_group.get(group_name, 2)

    def find_node(self, node_name: str) -> Optional[Node]:
        """Find and return a decision tree node by name.

        Args:
            node_name: Name of the node to be found.

        Returns:
            A Node instance if found, otherwise None.
        """
        key = self._node_normalized_keys.get(normalize_str(node_name))
        return self.nodes.get(key) if key is not None else None

    def get_all_parents(
        self, entity: Union[Node, Group, GroupView]
    ) -> Tuple["TopdownMethodology.Node", ...]:
        """Return all parent nodes for a given entity.

        Iterates over all nodes in the decision tree and, for each node, checks if any child node or group
        matches the provided entity (by type and name). If so, the node is considered a parent.

        Args:
            entity: An instance of Node, Group, or GroupView for which to find parent nodes.

        Returns:
            A tuple of Node instances that are parents of the provided entity.
        """
        result: Set["TopdownMethodology.Node"] = set()
        for node in self.nodes.values():
            for child in node.children:
                if (
                    type(child) is type(entity)
                    or (isinstance(child, Group) and isinstance(entity, GroupView))
                ) and child.name == entity.name:
                    result.add(node)
                    break
        return tuple(sorted(result))


class TelemetryDatabase:
    """Loads and organizes telemetry specification data for CPU performance analysis.

    This class parses the telemetry specification and constructs events, metrics, and groups.
    It also initializes the top-down analysis methodology for decision tree operations.

    Attributes:
        product_name (str): The product name from the specification.
        events (Dict[str, Event]): Mapping of event identifiers to Event instances.
        metrics (Dict[str, Metric]): Mapping of metric identifiers to Metric instances.
        groups (Dict[str, Group]): Mapping of group names to Group instances.
        topdown (TopdownMethodology): Instance of the top-down methodology analysis.
    """

    def __init__(self, spec: TelemetrySpecification, options: Optional[CpuEventOptions] = None) -> None:
        """Initialize TelemetryDatabase with a telemetry specification.

        Parses the specification to build events, metrics, and groups and initializes the top-down methodology.

        Args:
            spec: A TelemetrySpecification instance containing configuration data.
        """
        self.product_name = spec.product_configuration.product_name
        self.events: Dict[str, Event] = {}
        self.metrics: Dict[str, Metric] = {}
        self.groups: Dict[str, Group] = {}

        self.options: CpuEventOptions = options if options is not None else CpuEventOptions()

        self._import_spec(spec)
        self.topdown = TopdownMethodology(self, spec.methodologies.topdown_methodology)

        self._metrics_normalized_keys = self._get_normalized_key_mapping(self.metrics)
        self._group_normalized_keys = self._get_normalized_key_mapping(self.groups)

    def _import_spec(self, spec: TelemetrySpecification) -> None:
        # Import events, metrics, and groups from the telemetry specification.
        self.events = {
            k: Event(
                name=k,
                code=int(v.code, 16),
                title=v.title,
                description=v.description,
                modifiers=self.options.modifiers
            )
            for k, v in spec.events.items()
        }

        self.metrics = {
            name: Metric(
                self,
                name,
                metric_data.title,
                metric_data.description,
                metric_data.units,
                metric_data.formula,
                events=tuple(self.events[e] for e in metric_data.events),
                sample_events=tuple(self.events[e] for e in metric_data.sample_events),
            )
            for name, metric_data in spec.metrics.items()
        }

        self.groups = {
            group_name: Group(
                group_name,
                group_data.title,
                group_data.description,
                metrics=tuple(self.metrics[m] for m in group_data.metrics),
            )
            for group_name, group_data in spec.groups.metrics.items()
        }

    def _get_normalized_key_mapping(self, d: Dict[str, Any]) -> Dict[str, str]:
        # Create a mapping of normalized keys for a given dictionary.
        return {normalize_str(name): name for name, _ in d.items()}

    def find_group(self, group_name: str) -> Optional[Group]:
        """Find and return a group by name, ignoring case and underscores.

        Args:
            group_name: The name of the group to find.

        Returns:
            The corresponding Group instance if found; otherwise, None.
        """
        n = normalize_str(group_name)
        real_name = self._group_normalized_keys.get(n)
        return self.groups.get(real_name) if real_name else None

    def find_metric(self, metric_name: str) -> Optional[Metric]:
        """Find and return a metric by name, ignoring case and underscores.

        Args:
            metric_name: The name of the metric to find.

        Returns:
            The corresponding Metric instance if found; otherwise, None.
        """
        n = normalize_str(metric_name)
        real_name = self._metrics_normalized_keys.get(n)
        return self.metrics.get(real_name) if real_name else None

    def get_close_group_match(self, group_name: str) -> Optional[str]:
        """Return the closest matching group name.

        Uses the difflib.get_close_matches utility to suggest a close match.

        Args:
            group_name: The group name to look up.

        Returns:
            The name of a close matching group, or None if no match is found.
        """
        matches = get_close_matches(normalize_str(group_name), self._group_normalized_keys, 1)
        return self.groups[self._group_normalized_keys[matches[0]]].name if matches else None

    def get_close_metric_match(self, metric_name: str) -> Optional[str]:
        """Return the closest matching metric name.

        Uses the difflib.get_close_matches utility to suggest a close match.

        Args:
            metric_name: The metric name to look up.

        Returns:
            The name of a close matching metric, or None if no match is found.
        """
        matches = get_close_matches(normalize_str(metric_name), self._metrics_normalized_keys, 1)
        return self.metrics[self._metrics_normalized_keys[matches[0]]].name if matches else None

    def get_groups(self, group_names: Sequence[str]) -> Tuple[Group, ...]:
        """Return a tuple of groups matching the specified names.

        Args:
            group_names: A sequence of group names to retrieve.

        Returns:
            A tuple of Group instances corresponding to the provided names.

        Raises:
            Exception: If any group name is invalid.
        """
        result: List[Group] = []
        for group_name in group_names:
            group = self.find_group(group_name)
            if group is not None:
                result.append(group)
            else:
                suggestion = self.get_close_group_match(group_name)
                suggestion = f' Did you mean "{suggestion}"?' if suggestion else ""
                raise RuntimeError(f'"{group_name}" is not a valid group.{suggestion}')
        return tuple(result)

    def get_all_events_groups(self, max_events: int) -> Tuple[Group, ...]:
        """Return all groups for event capture including standalone events.

        This method returns all predefined groups and appends additional groups
        created for standalone events (events not included in any group). Each standalone
        group contains up to `max_events` events.

        Args:
            max_events: Maximum number of events per standalone group.

        Returns:
            A list of Group instances including those with standalone events.
        """
        result: List[Group] = list(self.groups.values())

        # Create multiple standalone metrics for events not part of any group.
        metric_events = {e for g in result for e in g.events}
        all_events = set(self.events.values())
        standalone_events = all_events.difference(metric_events)

        # Sort to ensure consistent ordering.
        standalone_events_list = sorted(standalone_events)
        metrics = []
        for i in range(0, len(standalone_events_list), max_events):
            chunk = tuple(standalone_events_list[i : i + max_events])
            metrics.append(
                Metric(
                    db=self,
                    name=f"STANDALONE_EVENTS_METRIC_{i // max_events}",
                    title=f"Standalone Events Metric {i // max_events}",
                    description="",
                    units="",
                    formula="",
                    events=chunk,
                    sample_events=tuple(),
                )
            )

        standalone_events_group = Group(
            name="STANDALONE_EVENTS_GROUP",
            title="Standalone Events Group",
            description="",
            metrics=tuple(metrics),
        )
        result.append(standalone_events_group)
        return tuple(result)
