# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=missing-class-docstring

"""
Common data classes for use with CMN
"""

from dataclasses import dataclass
from functools import total_ordering
from typing import Any, Dict, FrozenSet, List, Optional, Union

from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf import PerfEvent


# pylint: disable=too-many-instance-attributes
@dataclass
class CmnProbeFactoryConfig:
    cmn_generate_metrics_csv: bool = False
    cmn_generate_events_csv: bool = False
    cmn_list: bool = False
    cmn_list_devices: bool = False
    cmn_list_groups: Optional[List[str]] = None
    cmn_list_metrics: Optional[List[str]] = None
    cmn_list_events: Optional[List[str]] = None
    collect_by: CollectBy = CollectBy.NONE
    metrics: Optional[List[str]] = None
    groups: Optional[List[str]] = None
    capture_per_device_id: bool = False
    descriptions: bool = False
    show_sample_events: bool = False
    debug_path: Optional[str] = None
    cmn_index: Optional[List[int]] = None
    cmn_mesh_layout_input: Optional[str] = None
    cmn_mesh_layout_output: Optional[str] = None
    cmn_specification: Optional[str] = None


@dataclass(frozen=True)
class CmnLocation:
    cmn_index: int

    def __str__(self) -> str:
        return "Global"


@dataclass(frozen=True)
class XpLocation:
    cmn_index: int
    xp_id: int

    def __str__(self) -> str:
        return f"XP 0x{self.xp_id:03X}"


@dataclass(frozen=True)
class PortLocation:
    cmn_index: int
    xp_id: int
    port: int

    def __str__(self) -> str:
        return f"XP 0x{self.xp_id:03X} Port #{self.port}"


@dataclass(frozen=True)
class NodeLocation:
    cmn_index: int
    xp_id: int
    port: int
    node_id: int

    def __str__(self) -> str:
        return f"XP 0x{self.xp_id:03X} Port #{self.port} Node 0x{self.node_id:03X}"


Location = Union[CmnLocation, XpLocation, PortLocation, NodeLocation]


@dataclass(frozen=True)
class MetricDetails:
    title: str
    description: str
    sample_events: FrozenSet[str]
    formula: str
    units: str
    node_device_id: Optional[int]
    port_device_id: Optional[int]


@dataclass(frozen=True)
class TopdownMetricDetails:
    title: str
    description: str
    formula: str
    units: str
    base_metrics: FrozenSet[str]
    topdown_metrics: FrozenSet[str]


@dataclass(frozen=True)
class JsonEvent:
    name: str
    title: str
    description: str
    type: int
    eventid: int


# pylint: disable=missing-function-docstring
@dataclass(frozen=True)
class JsonWatchpoint:
    name: str
    description: str
    mesh_flit_dir: int
    wp_chn_sel: int
    wp_grp: int
    wp_mask: int
    wp_val: FrozenSet[int]

    def mesh_flit_dir_str(self) -> str:
        mapping: Dict[int, str] = {0: "Upload", 2: "Download"}
        return mapping[self.mesh_flit_dir]

    def wp_chn_sel_str(self) -> str:
        mapping: Dict[int, str] = {0: "REQ", 1: "RSP", 2: "SNP", 3: "DAT"}
        return mapping[self.wp_chn_sel]

    def wp_grp_str(self) -> str:
        mapping: Dict[int, str] = {0: "Primary", 1: "Secondary", 2: "Tertiary", 3: "Quaternary"}
        return mapping[self.wp_grp]

    def wp_mask_normalized(self) -> int:
        return self.wp_mask if self.wp_mask >= 0 else self.wp_mask + 2 ** 64

    def wp_val_normalized(self) -> FrozenSet[int]:
        return frozenset(wp_val if wp_val >= 0 else (wp_val + 2 ** 64) for wp_val in self.wp_val)


@dataclass(frozen=True)
class JsonMetric:
    name: str
    title: str
    description: str
    formula: str
    units: str
    events: FrozenSet[str]
    watchpoints: FrozenSet[str]
    sample_events: FrozenSet[str]


@dataclass(frozen=True)
class JsonGroup:
    name: str
    title: str
    description: str
    metrics: FrozenSet[str]


@dataclass(frozen=True)
class JsonTopdownMetric:
    name: str
    title: str
    formula: str
    metrics: FrozenSet[str]


@dataclass(frozen=True)
class JsonTopdownGroup:
    name: str
    title: str
    metrics: FrozenSet[str]


# Note: We can't use `order=True` otherwise python's type system scream at us.
# There is no elegant solution to overcome this issue.
@total_ordering
@dataclass(frozen=True)
# pylint: disable=duplicate-code
class Event(PerfEvent):
    name: str
    title: str
    description: str
    cmn_index: int
    type: int
    eventid: Optional[int]
    occupid: Optional[int]
    nodeid: Optional[int]
    xp_id: Optional[int] = None

    LINUX_FIX_MAP = {13: 10}

    @classmethod
    def linux_perf_type(cls, event_type: int) -> int:
        return cls.LINUX_FIX_MAP.get(event_type, event_type)

    def __post_init__(self) -> None:
        if not isinstance(self.type, int) or self.type < 0:
            raise TypeError("type must be int >= 0")
        if not isinstance(self.cmn_index, int) or self.cmn_index < 0:
            raise TypeError("cmn_index must be int >= 0")
        if self.occupid is not None and (not isinstance(self.occupid, int) or self.occupid < 0):
            raise TypeError("occupid must be int >= 0 or None")
        if self.is_cycle():
            if self.eventid is not None:
                raise TypeError("eventid must be None for cycle events")
            if self.nodeid is not None or self.xp_id is not None:
                raise TypeError("nodeid/xp_id must be None for cycle events")
            return
        if self.eventid is None or not isinstance(self.eventid, int) or self.eventid < 0:
            raise TypeError("eventid must be int >= 0 for non-cycle events")
        if self.nodeid is None and self.xp_id is None:
            return
        if self.nodeid is None or self.xp_id is None:
            raise TypeError("nodeid and xp_id must both be set for local events")
        if not isinstance(self.nodeid, int) or self.nodeid < 0:
            raise TypeError("nodeid must be int >= 0 for local events")
        if not isinstance(self.xp_id, int) or self.xp_id < 0:
            raise TypeError("xp_id must be int >= 0 for local events")

    def is_cycle(self) -> bool:
        return self.type == 3

    def is_global(self) -> bool:
        return self.nodeid is None and self.xp_id is None and not self.is_cycle()

    def key(self) -> str:
        if self.is_cycle():
            event_str = "cycle"
        else:
            event_str = f"{self.type}:{self.eventid}"
            if self.occupid not in (None, 0):
                event_str += f":{self.occupid}"
        location = f"I{self.cmn_index}"
        if not self.is_cycle() and not self.is_global():
            location += f":XP{self.xp_id}:N{self.nodeid}"
        return f"{event_str}@{location}"

    def __repr__(self) -> str:
        return (
            "Event("
            + self.name
            + ", CMN #"
            + str(self.cmn_index)
            + ", Node "
            + (f"0x{self.nodeid:03X}" if self.nodeid is not None else "-----")
            + ", Occupid "
            + (f"#{self.occupid}" if self.occupid is not None else "--")
            + ")"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        self_occupid = 0 if self.occupid is None else self.occupid
        other_occupid = 0 if other.occupid is None else other.occupid
        return (
            self.cmn_index,
            self.type,
            self.eventid,
            self_occupid,
            self.nodeid,
            self.xp_id,
        ) == (
            other.cmn_index,
            other.type,
            other.eventid,
            other_occupid,
            other.nodeid,
            other.xp_id,
        )

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Event):
            return NotImplemented
        self_occupid = 0 if self.occupid is None else self.occupid
        other_occupid = 0 if other.occupid is None else other.occupid
        return (
            self.cmn_index,
            self.type,
            self.eventid,
            self_occupid,
            self.nodeid,
            self.xp_id,
        ) < (
            other.cmn_index,
            other.type,
            other.eventid,
            other_occupid,
            other.nodeid,
            other.xp_id,
        )

    def __hash__(self) -> int:
        occupid = 0 if self.occupid is None else self.occupid
        return hash(
            (
                self.cmn_index,
                self.type,
                self.eventid,
                occupid,
                self.nodeid,
                self.xp_id,
            )
        )

    def perf_name(self) -> str:
        event_type = self.linux_perf_type(self.type)
        event_str = f"type={event_type}"
        if self.eventid is not None:
            event_str += f",eventid={self.eventid}"
        if self.occupid is not None:
            event_str += ",occupid=" + hex(self.occupid)
        if self.nodeid is not None:
            event_str += ",bynodeid" + ",nodeid=" + hex(self.nodeid)
        return f"arm_cmn_{self.cmn_index}/{event_str}/"


# Note: We can't use `order=True` otherwise python's type system scream at us.
# There is no elegant solution to overcome this issue.
@total_ordering
@dataclass(frozen=True)
# pylint: disable=invalid-name
class Watchpoint(PerfEvent):
    name: str
    title: str
    description: str
    cmn_index: int
    mesh_flit_dir: int
    wp_chn_sel: int
    wp_grp: int
    wp_mask: int
    wp_val: int
    xp_id: Optional[int]
    port: Optional[int]
    device: Optional[str]

    mesh_flit_dir_mapping = {
        0: "up",
        2: "down",
    }

    # pylint: disable=too-many-branches,
    def __post_init__(self) -> None:
        if not isinstance(self.cmn_index, int) or self.cmn_index < 0:
            raise TypeError("cmn_index must be int >= 0")
        if self.mesh_flit_dir not in self.mesh_flit_dir_mapping:
            raise ValueError("mesh_flit_dir must be 0 (up) or 2 (down)")
        if self.device is not None:
            if self.xp_id is not None or self.port is not None:
                raise ValueError("device watchpoints cannot set xp_id or port")
        else:
            if self.xp_id is None or self.port is None:
                raise ValueError("local watchpoints require xp_id and port")
            if not isinstance(self.xp_id, int) or self.xp_id < 0:
                raise TypeError("xp_id must be int >= 0")
            if not isinstance(self.port, int) or self.port < 0:
                raise TypeError("port must be int >= 0")
        if not isinstance(self.wp_chn_sel, int) or self.wp_chn_sel < 0:
            raise TypeError("wp_chn_sel must be int >= 0")
        if not isinstance(self.wp_grp, int) or self.wp_grp < 0:
            raise TypeError("wp_grp must be int >= 0")
        if not isinstance(self.wp_mask, int):
            raise TypeError("wp_mask must be int")
        if not isinstance(self.wp_val, int):
            raise TypeError("wp_val must be int")
        if self.wp_mask < 0:
            object.__setattr__(self, "wp_mask", self.wp_mask + 2 ** 64)
        if self.wp_val < 0:
            object.__setattr__(self, "wp_val", self.wp_val + 2 ** 64)

    def is_cycle(self) -> bool:
        return False

    def is_global(self) -> bool:
        return self.device is not None

    def key(self) -> str:
        direction = "UP" if self.mesh_flit_dir == 0 else "DOWN"
        event_str = (
            f"WP{self.wp_val}:M{self.wp_mask}:{direction}"
            f":CHN{self.wp_chn_sel}:GRP{self.wp_grp}"
        )
        location = f"I{self.cmn_index}"
        if self.device is not None:
            location += f":DEV{self.device}"
        else:
            location += f":XP{self.xp_id}:P{self.port}"
        return f"{event_str}@{location}"

    def __repr__(self) -> str:
        return (
            "Watchpoint("
            + self.name
            + ", CMN #"
            + str(self.cmn_index)
            + ", XP "
            + (f"0x{self.xp_id:03X}" if self.xp_id is not None else "-----")
            + ", Port "
            + (f"#{self.port}" if self.port is not None else "--")
            + ")"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Watchpoint):
            return NotImplemented
        return (
            self.cmn_index,
            self.mesh_flit_dir,
            self.wp_chn_sel,
            self.wp_grp,
            self.wp_mask,
            self.wp_val,
            self.xp_id,
            self.port,
            self.device,
        ) == (
            other.cmn_index,
            other.mesh_flit_dir,
            other.wp_chn_sel,
            other.wp_grp,
            other.wp_mask,
            other.wp_val,
            other.xp_id,
            other.port,
            other.device,
        )

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Watchpoint):
            return NotImplemented
        return (
            self.cmn_index,
            self.mesh_flit_dir,
            self.wp_chn_sel,
            self.wp_grp,
            self.wp_mask,
            self.wp_val,
            self.xp_id,
            self.port,
            self.device,
        ) < (
            other.cmn_index,
            other.mesh_flit_dir,
            other.wp_chn_sel,
            other.wp_grp,
            other.wp_mask,
            other.wp_val,
            other.xp_id,
            other.port,
            other.device,
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.cmn_index,
                self.mesh_flit_dir,
                self.wp_chn_sel,
                self.wp_grp,
                self.wp_mask,
                self.wp_val,
                self.xp_id,
                self.port,
                self.device,
            )
        )

    def perf_name(self) -> str:
        assert (self.xp_id is not None and self.port is not None) != (self.device is not None)
        event_str = f"watchpoint_{self.mesh_flit_dir_mapping[self.mesh_flit_dir]}"
        if self.device is None and self.xp_id is not None and self.port is not None:
            event_str += ",bynodeid" + ",nodeid=" + hex(self.xp_id)
            event_str += ",wp_dev_sel=" + str(self.port)
        elif self.device is not None and self.xp_id is None and self.port is None:
            event_str += ",wp_dev_sel=" + self.device
        event_str += ",wp_chn_sel=" + str(self.wp_chn_sel)
        event_str += ",wp_grp=" + str(self.wp_grp)
        event_str += ",wp_mask=" + hex(
            self.wp_mask if self.wp_mask >= 0 else self.wp_mask + 2 ** 64
        )
        event_str += ",wp_val=" + hex(self.wp_val if self.wp_val >= 0 else self.wp_val + 2 ** 64)
        return f"arm_cmn_{self.cmn_index}/{event_str}/"
