# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import dataclasses
import event_codes
import json
import os
from dataclasses import dataclass
from typing import Optional, Dict, Type, List

CPUS_FILENAME = "cpus.json"


@dataclass(frozen=True)
class MrsEvent:
    description: Optional[str]
    code: Optional[int] = None  # Some events don't have an event code
    name: Optional[str] = None  # Some events don't have a name
    impdef: bool = False
    title: Optional[str] = None  # telemetry data has titles as well

    @property
    def common(self):
        return event_codes.is_common(self.code)

    @property
    def recommended(self):
        return event_codes.is_recommended(self.code)


@dataclass(frozen=True)
class MrsMetric:
    name: str
    title: str
    formula: str
    units: str
    description: Optional[str] = None
    groups: Optional[List[str]] = None


def to_mrs_event(cls: Type, dict: Dict):
    """
    Convert dict to specified data class, ignoring additional fields

    Works with both arm-data style and telemetry entries. One difference is that telemetry has a
    title and a description, but arm-data only has a short description.
    """
    values = {field.name: dict[field.name]
              for field in dataclasses.fields(cls) if field.name in dict}
    # Descriptions contain " +ICI " and " +//0 " to represent newline characters.
    if "description" in values:
        values["description"] = values["description"].replace(" +//0 ", "\n") \
                                                     .replace(" +ICI ", "\n")

    # Event codes are integers
    if "code" in values and isinstance(values["code"], str):
        values["code"] = int(values["code"], 0)  # 0 to guess base from prefix

    return cls(**values)


def read_json(path):
    with open(path, 'r') as f:
        return json.load(f)


def read_cpu_events(repository_path, cpu_name):
    cpu_data_path = os.path.join(repository_path, "pmu", cpu_name + ".json")
    cpu_data = read_json(cpu_data_path)
    return [to_mrs_event(MrsEvent, e) for e in cpu_data["events"]]


def read_common_events(repository_path):
    common_data_path = os.path.join(repository_path, "pmu", "common_armv9.json")
    common_data = read_json(common_data_path)
    return [to_mrs_event(MrsEvent, e) for e in common_data["events"]]


def read_telemetry_events(json_path):
    def accessible(event):
        return "PMU" in event["accesses"]

    def convert(event, name):
        return to_mrs_event(MrsEvent, dict(event, name=name))

    json = read_json(json_path)
    return [convert(e, name) for (name, e) in json["events"].items() if accessible(e)]


def read_telemetry_metrics(json_path):
    def assign_group(m, metric_groups):
        matching_groups = [name for (name, group)
                           in metric_groups.items() if m.name in group["metrics"]]
        return dataclasses.replace(m, groups=matching_groups)

    json = read_json(json_path)
    metrics = [to_mrs_event(MrsMetric, dict(name=name, **m)) for (name, m)
               in json["metrics"].items()]

    # Merge metric groups list with metrics list
    return [assign_group(m, json["groups"]["metrics"]) for m in metrics]


def read_telemetry_function_groups(json_path):
    json = read_json(json_path)
    return json["groups"]["function"]
