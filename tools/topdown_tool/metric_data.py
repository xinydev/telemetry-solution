import dataclasses
from difflib import get_close_matches
import itertools
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union

METRICS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics")
IDENTIFIER_REGEX = re.compile(r"[a-zA-Z_]\w*")
UNIT_REMAPPINGS = {"MPKI": "misses per 1,000 instructions"}


@dataclass(frozen=True)
class Event:
    name: str
    code: int


@dataclass(frozen=True)
class Metric:
    name: str
    title: str
    description: str
    units: str
    formula: str
    events: Tuple[Event, ...]

    def format_value(self, value):
        if self.units == "percent":
            return f"{value:.2f}%"
        if self.units.startswith("percent of "):
            return f"{value:.2f}% {self.units[len('percent of '):]}"
        return f"{value:.3f} {UNIT_REMAPPINGS.get(self.units, self.units)}"


@dataclass(frozen=True)
class Group:
    name: str
    title: str
    description: str
    metrics: Tuple[Metric, ...]


@dataclass(frozen=True)
class Node:
    metric_data: "MetricData"
    name: str
    group: Group
    next: List[str]
    sample_events: Tuple[Event, ...]

    def next_nodes(self):
        return [self.metric_data.topdown.nodes[link] for link in self.next if link in self.metric_data.topdown.nodes]

    def next_groups(self):
        return [self.metric_data.groups[link] for link in self.next if link in self.metric_data.groups]


@dataclass(frozen=True)
class MetricInstance:
    """An instance of a Metric with associated data, such as level in the topdown hierarchy and the group it came from."""

    metric: Metric
    group: Group
    level: int = 1
    stage: int = 0
    sample_events: Tuple[Event, ...] = ()
    parent: Optional["MetricInstance"] = None


@dataclass(frozen=True)
class MetricInstanceValue(MetricInstance):
    value: float = 0.0


@dataclass
class CombinedMetricInstance:
    metric: Metric
    group: Group
    stage: int
    sample_events: Tuple[Event, ...]
    parents: List["MetricInstance"] = field(default_factory=list)


@dataclass
class CombinedMetricInstanceValue(CombinedMetricInstance):
    value: float = 0.0


COMBINED_TYPE = {
    MetricInstance: CombinedMetricInstance,
    MetricInstanceValue: CombinedMetricInstanceValue
}

AnyMetricInstance = Union[MetricInstance, MetricInstanceValue, CombinedMetricInstance, CombinedMetricInstanceValue]
SeparateMetricInstance = Union[MetricInstance, MetricInstanceValue]


def field_dict(obj):
    """Covnerts dataclass to a dictionary of field: value.

    Unlike dataclasses.asdict, this does not convert nested dataclasses - useful for expanding as kwargs
    """
    assert dataclasses.is_dataclass(obj)
    return {f.name: getattr(obj, f.name) for f in dataclasses.fields(obj)}


def combine_instances(instances: Iterable[SeparateMetricInstance]):
    """Replaces similar MetricInstance and MetricInstanceValue instances with a single CombinedMetricInstance/CombinedMetricInstanceValue object"""

    grouped = itertools.groupby(sorted(instances, key=lambda i: (i.group.name, i.metric.name)))

    def combined(similar_instances: Iterator[SeparateMetricInstance], instance) -> Union[CombinedMetricInstance, CombinedMetricInstanceValue]:
        return create_dataclass(
            COMBINED_TYPE[type(instance)],
            field_dict(instance),
            parents=[i.parent for i in similar_instances if i.parent]
        )

    return [combined(instances, instance) for instance, instances in grouped]


def to_key(name: str):
    """Maps a metric, group, or node name to a dictionary key. Used to provide case (and underscore/hyphen) insensitive lookup."""
    return name.lower().replace("_", "").replace("-", "")


class TopdownMethodology:
    def __init__(self, metric_data: "MetricData", data):
        self.nodes: Dict[str, Node] = {
            metric["name"]: Node(
                metric_data=metric_data,
                name=metric["name"],
                next=metric["next_items"],
                group=metric_data.groups[metric["group"]],
                sample_events=tuple(metric_data.events[e] for e in metric["sample_events"] if e in metric_data.events),
            )
            for metric in data["decision_tree"]["metrics"]
        }
        self.root_metrics = [metric_data.metrics[m] for m in data["decision_tree"]["root_nodes"]]

        self.stage_for_group: Dict[str, int] = {}
        for node in self.nodes.values():
            self.stage_for_group[node.group.name] = 1

            for uarch_group in node.next_groups():
                self.stage_for_group[uarch_group.name] = 2

        self.node_keys = {to_key(k): v for k, v in self.nodes.items()}

    def get_stage(self, group_name: str):
        return self.stage_for_group.get(group_name, 2)

    def find_node(self, node_name: str):
        return self.node_keys.get(to_key(node_name))


def create_dataclass(dataclass_type, data: Dict, **kwargs):
    fields = set(f.name for f in dataclasses.fields(dataclass_type))
    return dataclass_type(**{k: v for k, v in dict(data, **kwargs).items() if k in fields})


class MetricData:
    def __init__(self, cpu_or_json: str):
        if isinstance(cpu_or_json, str):
            with open(os.path.join(METRICS_DIR, f"{cpu_or_json}.json"), encoding="utf-8") as f:
                json_data: Dict = json.load(f)
        else:
            json_data = cpu_or_json

        self.events = {k: Event(name=k, code=int(v["code"], 16)) for k, v in json_data.get("events", {}).items()}

        self.metrics = {
            name: create_dataclass(
                Metric,
                metric_data,
                name=name,
                events=tuple(self.events[e] for e in metric_data["events"]),
            )
            for name, metric_data in json_data["metrics"].items()
        }

        self.groups: Dict[str, Group] = {
            group_name: create_dataclass(
                Group,
                group_data,
                name=group_name,
                metrics=tuple(self.metrics[m] for m in group_data["metrics"]),
            )
            for group_name, group_data in json_data["groups"]["metrics"].items()
        }

        self.topdown = TopdownMethodology(self, json_data["topdown_methodology"])

        self.group_keys = {to_key(k): v for k, v in self.groups.items()}
        self.metric_keys = {to_key(k): v for k, v in self.metrics.items()}

    @staticmethod
    def list_cpus():
        """List CPUs for which we have data"""
        return [fn[0:-5] for fn in os.listdir(METRICS_DIR) if fn.lower().endswith(".json") and fn != "mapping.json"]

    def find_group(self, group_name: str):
        """Returns group with the specified name, ignoring case and underscores"""
        return self.group_keys.get(to_key(group_name))

    def get_close_group_match(self, group_name: str):
        matches = get_close_matches(to_key(group_name), self.group_keys, 1)
        return self.group_keys[matches[0]].name if matches else None

    def find_metric(self, metric_name: str):
        return self.metric_keys.get(to_key(metric_name))

    def get_close_metric_match(self, metric_name: str):
        matches = get_close_matches(to_key(metric_name), self.metric_keys, 1)
        return self.metric_keys[matches[0]].name if matches else None

    def metrics_for_group(self, group_name):
        group = self.find_group(group_name)
        return [MetricInstance(group=group, metric=self.metrics[metric.name], stage=self.topdown.get_stage(group.name)) for metric in self.groups[group.name].metrics]

    def metrics_descended_from(self, node_name: str, max_depth: Optional[int] = None):
        metrics: List[MetricInstance] = []

        def _add_metrics(node: Node, current_level=1, parent: Optional[MetricInstance] = None):
            metric = self.metrics[node.name]
            instance = MetricInstance(group=node.group, metric=metric, level=current_level, stage=self.topdown.get_stage(node.group.name), sample_events=node.sample_events, parent=parent)
            metrics.append(instance)

            if max_depth is None or current_level < max_depth:
                for child in node.next_nodes():
                    _add_metrics(child, current_level + 1, parent=instance)

                for group in node.next_groups():
                    for m in group.metrics:
                        metrics.append(MetricInstance(group=group, metric=m, level=current_level + 1, stage=self.topdown.get_stage(group.name), parent=instance))

        node = self.topdown.find_node(node_name)
        if node:
            _add_metrics(node)
        else:
            metric = self.find_metric(node_name)
            if metric:
                metrics.append(MetricInstance(group=next(g for g in self.groups.values() if metric in g.metrics), metric=metric, level=0, stage=2))
        return metrics

    def metrics_up_to_level(self, level: int):
        assert level > 0
        metrics: List[MetricInstance] = []

        for metric in self.topdown.root_metrics:
            metrics += self.metrics_descended_from(metric.name, level)

        return metrics

    def methodology_metrics(self):
        return self.metrics_up_to_level(999)

    def uncateogirsed_metrics(self):
        """
        Metrics from groups that do no appear in the topdown methodlogy.

        Note: This does not include:
        * Metrics that are not part of any group.
        * Metrics that don't appear in the topdown methodology, but belong to a group that does.
        """
        methodology_groups = set(mi.group for mi in self.methodology_metrics())
        uncategorised_groups = [g for g in self.groups.values() if self.topdown.get_stage(g.name) == 2 and g not in methodology_groups]

        output: List[MetricInstance] = []
        for g in uncategorised_groups:
            output += self.metrics_for_group(g.name)
        return output

    def all_metrics(self):
        return self.methodology_metrics() + self.uncateogirsed_metrics()
