# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf import Cpu, PerfRecordLocation

UNIT_REMAPPINGS = {"MPKI": "misses per 1,000 instructions"}

# Default stages, unless levels or metric groups are specified
DEFAULT_ALL_STAGES = [1, 2]
COMBINED_STAGES: List[int] = []


@dataclass
class CpuProbeConfiguration:
    csv: Optional[str] = None  # Path to the csv
    cpu_dump_events: Optional[Any] = None
    cpu_list_groups: bool = False
    cpu_list_metrics: bool = False
    cpu_list_events: bool = False
    multiplex: bool = False
    collect_by: CollectBy = CollectBy.METRIC
    metric_group: List[str] = field(default_factory=list)
    node: Optional[str] = None
    level: Optional[int] = None
    stages: List[int] = field(default_factory=DEFAULT_ALL_STAGES.copy)
    descriptions: bool = False
    show_sample_events: bool = False
    events_csv: Optional[str] = None


@dataclass(frozen=True, order=True)
class CpuAggregate(PerfRecordLocation):
    cpus: Tuple[Cpu, ...]
