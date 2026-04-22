# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional, Tuple

from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf import Cpu, PerfRecordLocation

UNIT_REMAPPINGS = {"MPKI": "misses per 1,000 instructions"}

# Default stages, unless levels or metric groups are specified
DEFAULT_ALL_STAGES = [1, 2]
COMBINED_STAGES: List[int] = []


class CpuModifier(Enum):
    """Enumeration for different CPU events modifiers

    Attributes:
        USERSPACE: Events are collected only in EL0
        KERNEL: Events are collected only in EL1
    """

    USERSPACE = "u"
    KERNEL = "k"

    def __str__(self) -> str:
        return self.value

    @staticmethod
    def from_string(arg: str) -> "CpuModifier":
        """Converts a string to a CpuModifiers enum member.

        Args:
            arg: A string representing events capture EL.

        Returns:
            The corresponding CpuModifiers member.
        """
        return CpuModifier(arg.lower())


@dataclass
class CpuProbeConfiguration:
    cpu_dump_events: Optional[Any] = None
    # Combined CSV generation targets; allowed values: "metrics", "events"
    cpu_generate_csv: List[str] = field(default_factory=list)
    cpu_list_groups: bool = False
    cpu_list_metrics: bool = False
    cpu_list_events: bool = False
    multiplex: bool = False
    collect_by: CollectBy = CollectBy.METRIC
    metric_group: List[str] = field(default_factory=list)
    node: Optional[str] = None
    level: Optional[int] = None
    stages: List[int] = field(default_factory=DEFAULT_ALL_STAGES.copy)
    events_modifiers: Optional[Tuple[CpuModifier, ...]] = None
    descriptions: bool = False
    show_sample_events: bool = False
    pid_tracking_applicable: bool = False


@dataclass(frozen=True, order=True)
class CpuAggregate(PerfRecordLocation):
    cpus: Tuple[Cpu, ...]


@dataclass(frozen=True)
class CpuEventOptions:
    modifiers: Optional[Tuple[CpuModifier, ...]] = None
