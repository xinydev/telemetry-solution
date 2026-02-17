# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=too-many-lines, too-many-locals, too-many-branches, too-many-statements, too-many-nested-blocks

"""
CMN Event Scheduler and Group Optimizer
=======================================

This module provides a robust, DTC-aware event "packing scheduler" for CMN.
It enables users to:

- Register a list of metric event tuples (e.g., performance counters, node events).
- Deduplicate and optimize their collection into hardware-valid groups ("event cohorts") according to constraints (8 events/group per DTC, max 4 events/node/group).
- Retrieve results strictly by original tuple.

The packing, event grouping, and retrieval logic is strictly defined:
    - **Device-only tuples may not mix events from different nodes** (except the "cycle" event, which may also appear alone).
      Mixed tuples may span nodes but must share a single xp_id.
    - **Deduplication**: Duplicate metric tuples are eliminated (order-insensitive, but only the registered form is retrievable).
    - Event **groups** are computed to minimize runs while respecting per-node and overall slot constraints (see below).
    - **Retrieval is strict**: Only the *original exact* metric tuples may be retrieved (no order-insensitive lookups).
    - The user must supply perf results that are strictly keyed and ordered to match the optimized groups.
    - Empty tuples and cycle-only tuples are supported as edge cases.
    - All error/invariant violations are enforced with explicit, descriptive exceptions.

**Hardware Constraints:**
    - Max {MAX_EVENTS_PER_DTC} events per DTC
    - Max {MAX_EVENTS_PER_XP} events per crosspoint/xp
    - Max {MAX_EVENTS_PER_NODE} events per node
    - Special handling for a single "cycle" event (global in linux perf)
    - Uniform key syntax: every non-cycle event key is formatted as
      `"{event_type}:{event_id}@I{cmn_index}:XP{xp_id}:N{node_id}"`

**Exceptions and Errors:**
    - Incorrect mixing of multiple nodes in a device-only tuple → ValueError
    - Non-int node_id (except for cycle/None) → TypeError
    - Retrieval request for unregistered (or differently ordered) tuple → KeyError
    - perf_result/group mismatch (extra/missing keys, wrong order) → KeyError or IndexError
    - Structural input invariants are enforced with explicit exceptions. Tuples that cannot be
      scheduled together due to hardware limits are deterministically split for collection.

**Strictness Principle:** All public-facing invariants are always enforced. Any consumer or maintainer can rely on all checks and behaviors in this contract.

Example Usage:
--------------
    from cmn_scheduler import CmnInfo, CmnScheduler, Event

    metrics = [(Event(1, 0), Event(2, 0)), ...]
    cmn = CmnInfo(dtc_count=2, dtc_of=lambda nid: nid % 2)
    sched = CmnScheduler(metrics, cmn)
    event_groups = sched.get_optimized_event_groups()
    # → Pass event_groups to perf collection in order...
    perf_result = {group: (perf values ...), ...}
    results = sched.retrieve_metric_result(perf_result, metrics[0])
    # → Results are returned in exact order of metrics[0]

All guarantees, constraints, and error behaviors are directly tested in `tests/test_cmn_scheduler.py`.
See class and method docstrings below for more specifics.

"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    Final,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
    cast,
)

from .common import Event as CmnEvent, Watchpoint as CmnWatchpoint

GLOBAL_DTC_SENTINEL: Final[int] = -1

# Centralized hardware constraint constants (used for PMU/CMN packing logic)
MAX_EVENTS_PER_NODE: Final[int] = 4
MAX_EVENTS_PER_XP: Final[int] = 4
MAX_EVENTS_PER_DTC: Final[int] = 8
MAX_WATCHPOINTS_PER_DIRECTION: Final[int] = 2
WATCHPOINT_STR_TO_DIR: Final[dict[str, int]] = {"UP": 0, "DOWN": 2}

# --------------------------------------------------------------------
# 1.  Basic data-structures
# --------------------------------------------------------------------


Event = Union[CmnEvent, CmnWatchpoint]


@dataclass(frozen=True)
class WatchpointPort:
    xp_id: int
    port: int


WatchpointPortMap = Dict[str, List[WatchpointPort]]


def cycle_event(cmn_index: int) -> CmnEvent:
    return CmnEvent(
        name="cycle",
        title="cycle",
        description="cycle",
        cmn_index=cmn_index,
        type=3,
        eventid=None,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )


def _is_cycle_key(key: str) -> bool:
    return key.startswith("cycle@")


def _parse_location(location: str) -> tuple[int, Optional[int], Optional[int], Optional[int], Optional[str]]:
    parts = location.split(":")
    if not parts or not parts[0].startswith("I"):
        raise ValueError(f"Malformed location string: {location!r}")
    cmn_index = int(parts[0][1:])
    xp_id: Optional[int] = None
    node_id: Optional[int] = None
    port: Optional[int] = None
    device: Optional[str] = None
    idx = 1
    if idx < len(parts):
        part = parts[idx]
        if part.startswith("XP"):
            xp_id = int(part[2:])
            idx += 1
            if idx < len(parts):
                part = parts[idx]
                if part.startswith("N"):
                    node_id = int(part[1:])
                    idx += 1
                elif part.startswith("P"):
                    port = int(part[1:])
                    idx += 1
                else:
                    raise ValueError(f"Malformed location segment: {part!r}")
        elif part.startswith("DEV"):
            device = part[3:]
            idx += 1
        else:
            raise ValueError(f"Malformed location segment: {part!r}")
    if idx != len(parts):
        raise ValueError(f"Malformed location string: {location!r}")
    return cmn_index, xp_id, node_id, port, device


def event_from_key(key: str) -> Event:
    try:
        event_str, location = key.split("@", 1)
    except ValueError as exc:
        raise ValueError(f"Malformed event key (missing @): {key!r}") from exc
    cmn_index, xp_id, node_id, port, device = _parse_location(location)
    if event_str.startswith("WP"):
        if node_id is not None:
            raise ValueError(f"Watchpoint key cannot include node_id: {key!r}")
        try:
            meta = event_str[2:]
            value_s, post_val = meta.split(":M", 1)
            mask_s, post_mask = post_val.split(":", 1)
            direction_s, post_direction = post_mask.split(":CHN", 1)
            chn_sel_s, post_chn = post_direction.split(":GRP", 1)
            grp_s = post_chn
            return CmnWatchpoint(
                name="",
                title="",
                description="",
                cmn_index=cmn_index,
                mesh_flit_dir=WATCHPOINT_STR_TO_DIR[direction_s],
                wp_chn_sel=int(chn_sel_s),
                wp_grp=int(grp_s),
                wp_mask=int(mask_s),
                wp_val=int(value_s),
                xp_id=xp_id,
                port=port,
                device=device,
            )
        except Exception as exc:
            raise ValueError(f"Failed to parse Watchpoint from key: {key!r}: {exc}") from exc
    if device is not None or port is not None:
        raise ValueError(f"Device event key cannot include watchpoint location: {key!r}")
    if event_str == "cycle":
        if xp_id is not None or node_id is not None:
            raise ValueError(f"Cycle key cannot include node or xp: {key!r}")
        return cycle_event(cmn_index=cmn_index)
    parts = event_str.split(":")
    if len(parts) not in (2, 3):
        raise ValueError(f"Malformed device event key: {key!r}")
    event_type = int(parts[0])
    event_id = int(parts[1])
    occupid = int(parts[2]) if len(parts) == 3 else None
    return CmnEvent(
        name="",
        title="",
        description="",
        cmn_index=cmn_index,
        type=event_type,
        eventid=event_id,
        occupid=occupid,
        nodeid=node_id,
        xp_id=xp_id,
    )


@dataclass(frozen=True)
class NodeEntry:
    dtc: int
    xp: int
    node: int
    node_type: int
    port: int


@dataclass
class CmnInfo:
    """
    Describes the hardware topology for the event scheduler.

    Args:
        dtc_count (int): Number of DTC (event controller) domains present in the CMN.
        dtc_of (Callable[[int], int]): Function mapping xp_id → DTC id.
        nodes (Sequence[NodeEntry]): Sequence of node topology entries. Each is a 5-tuple:
            (dtc, xp, node, node_type, port).
        watchpoint_ports_by_device (WatchpointPortMap): Mapping from watchpoint device name to eligible
            (xp_id, port) locations.
    Constants:
        MAX_EVENTS_PER_DTC: Max total events per DTC (all types).
        MAX_EVENTS_PER_XP: Max total events per crosspoint (Device + Watchpoint).
        MAX_EVENTS_PER_NODE: Max device events per node.
        MAX_WATCHPOINTS_PER_DIRECTION: Max watchpoints per direction (0=UP, 2=DOWN) per XP.
    """

    dtc_count: int
    dtc_of: Callable[[int], int]
    nodes: Sequence[NodeEntry]
    watchpoint_ports_by_device: WatchpointPortMap = field(default_factory=dict)
    global_type_aliases: Dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for entry in self.nodes:
            if not isinstance(entry, NodeEntry):
                raise ValueError("CmnInfo.nodes entries must be NodeEntry instances")
            if entry.port is None:
                raise ValueError("CmnInfo nodes must include port for each node")
        if self.watchpoint_ports_by_device is None:
            self.watchpoint_ports_by_device = {}
        for device, ports in self.watchpoint_ports_by_device.items():
            seen: Set[WatchpointPort] = set()
            unique_ports: List[WatchpointPort] = []
            for port in ports:
                if port in seen:
                    continue
                seen.add(port)
                unique_ports.append(port)
            self.watchpoint_ports_by_device[device] = unique_ports

    def watchpoint_ports_for_device(self, device: str) -> List[WatchpointPort]:
        """
        Return eligible watchpoint (xp_id, port) locations for a global device watchpoint.

        Raises:
            ValueError if no mapping is configured or the device is unknown.
        """
        ports = self.watchpoint_ports_by_device.get(device)
        if not ports:
            raise ValueError(f"Unknown watchpoint device {device!r}")
        return ports

    def global_type_of(self, t: int) -> int:
        """
        Return the effective global device type as seen by the collection backend.

        Some platforms alias multiple raw CMN node types onto the same backend type.
        The scheduler must use this effective type for global capacity checks.
        """
        return self.global_type_aliases.get(t, t)

    def nodes_of_type(self, t: int) -> list[tuple[int, int, int]]:
        """
        Return a list of (dtc, xp, node) triples where node_type == t.

        Args:
            t (int): The node_type to match.

        Returns:
            List of (dtc, xp, node) for matching nodes.
        """
        return [
            (entry.dtc, entry.xp, entry.node)
            for entry in self.nodes
            if entry.node_type == t
        ]

    def nodes_of_global_type(self, t: int) -> list[tuple[int, int, int]]:
        """
        Return a list of (dtc, xp, node) triples whose effective global type matches ``t``.
        """
        effective_type = self.global_type_of(t)
        return [
            (entry.dtc, entry.xp, entry.node)
            for entry in self.nodes
            if self.global_type_of(entry.node_type) == effective_type
        ]

    def unique_xps_of_type(self, t: int) -> list[tuple[int, int]]:
        """
        Return a list of (dtc, xp) tuples, once each, for nodes of node_type == t.

        Args:
            t (int): The node_type to match.

        Returns:
            List of (dtc, xp) unique pairs.
        """
        seen = set()
        result = []
        for entry in self.nodes:
            if entry.node_type == t and (entry.dtc, entry.xp) not in seen:
                seen.add((entry.dtc, entry.xp))
                result.append((entry.dtc, entry.xp))
        return result

    def unique_dtcs_of_type(self, t: int) -> set[int]:
        """
        Return a set of unique DTC ids where at least one node of node_type == t is present.

        Args:
            t (int): The node_type to match.

        Returns:
            Set of DTC ids.
        """
        return {entry.dtc for entry in self.nodes if entry.node_type == t}

    # ----------------------------------------------------------------
    # Helper: per-XP node count for a given node_type
    # ----------------------------------------------------------------
    def xp_node_counts(self, t: int) -> dict[int, int]:
        """
        Return a dictionary mapping xp_id → number of nodes of node_type == t
        that reside on that crosspoint.

        Args:
            t (int): The node_type to inspect.

        Returns:
            Dict where key is xp_id and value is node count.
        """
        cnt: dict[int, int] = {}
        for entry in self.nodes:
            if entry.node_type == t:
                cnt[entry.xp] = cnt.get(entry.xp, 0) + 1
        return cnt

    def xp_type_node_counts(self) -> dict[int, dict[int, int]]:
        """
        Return {xp_id: {event_type: node_count}} for all nodes in topology.
        Types with zero nodes are omitted.
        """
        counts: dict[int, dict[int, int]] = {}
        for entry in self.nodes:
            if entry.xp not in counts:
                counts[entry.xp] = {}
            counts[entry.xp][entry.node_type] = counts[entry.xp].get(entry.node_type, 0) + 1
        return counts

    def xp_global_type_node_counts(self) -> dict[int, dict[int, int]]:
        """
        Return {xp_id: {effective_global_type: node_count}} for all nodes in topology.
        Types with zero nodes are omitted.
        """
        counts: dict[int, dict[int, int]] = {}
        for entry in self.nodes:
            effective_type = self.global_type_of(entry.node_type)
            if entry.xp not in counts:
                counts[entry.xp] = {}
            counts[entry.xp][effective_type] = counts[entry.xp].get(effective_type, 0) + 1
        return counts

    def port_of(self, xp_id: int, node_id: int) -> int:
        """
        Return the port for a given (xp_id, node_id) pair.

        Raises:
            ValueError if the node is not present in topology.
        """
        for entry in self.nodes:
            if entry.xp == xp_id and entry.node == node_id:
                return entry.port
        raise ValueError(f"Unknown node {node_id} on XP {xp_id}")


# --------------------------------------------------------------------
# 2.  Packing helper classes
# --------------------------------------------------------------------


@dataclass(frozen=True, eq=True)
class _TupleReq:
    """INTERNAL: Associates a unique identifier and a candidate tuple of events for packing and cohort assignment.

    Attributes:
        id (int): Unique tuple id in scheduler context.
        events (tuple[Event, ...]): Metric events.

    Invariants:
        - Device-only tuples must have a single xp_id and node_id.
        - Mixed tuples may span nodes but must share a single xp_id.

    Used for per-xp-group tracking during packing. Not part of the scheduler’s external API.
    """

    id: int
    events: tuple[Event, ...]

    @property
    def node_id(self) -> int | None:
        """
        Returns the node_id for this metric tuple.
        Returns None if all events are cycle/empty.
        Raises:
            ValueError if events from multiple nodes (excluding "cycle") are present.
        """
        node = None
        for ev in self.events:
            if isinstance(ev, CmnWatchpoint):
                raise ValueError("Attempt to get the node_id of a watchpoint event")

            if not ev.is_cycle():
                if node is None:
                    node = ev.nodeid
                elif ev.nodeid != node:
                    raise ValueError(
                        "Tuple contains events from multiple nodes, which is not allowed."
                    )
        return node

    @property
    def xp_id(self) -> int | None:
        """
        Returns the xp_id for this metric tuple.
        Returns None if all events are cycle/empty.
        Raises:
            ValueError if events from multiple crosspoints (excluding "cycle") are present.
        """
        xp = None
        for ev in self.events:
            if not ev.is_cycle():
                ev_xp = ev.xp_id
                if xp is None:
                    xp = ev_xp
                elif ev_xp != xp:
                    raise ValueError(
                        "Tuple contains events from multiple crosspoints, which is not allowed."
                    )
        return xp

    def __repr__(self) -> str:
        evs = ", ".join(e.key() for e in self.events)
        return f"{self.id}: [{evs}]"


@dataclass
class _XpPack:
    """INTERNAL: One-unit packing of events for a specific XP.

    Attributes:
        xp_id (int): Target crosspoint ID.
        events (set[str]): Unique event keys (cycle NOT included).
        cycle_key (Optional[str]): Canonical cycle key (if present).
        tuple_ids (list[int]): Metric tuple IDs packed in this pack.
        spill (bool): True for singleton packs from oversized tuples; each forms its own cohort.

    Used only by the scheduler's internal packing/planning phases.
    """

    xp_id: int
    events: Set[str]  # event keys (cycle NOT included)
    cycle_key: Optional[str]
    tuple_ids: List[int]
    spill: bool = (
        False  # True for singleton packs from oversized tuples (each forms its own cohort)
    )

    def size(self) -> int:
        """Number of (unique) events (not including cycle event)."""
        return len(self.events)

    def __repr__(self) -> str:
        evs = ", ".join(sorted(self.events))
        if self.cycle_key is not None:
            return f"[{evs}, {self.cycle_key}]"
        return f"[{evs}]"


@dataclass
class _Cohort:
    """INTERNAL: Represents a group of XpPacks (crosspoint event packs) to be scheduled/collected as a hardware-legal unit.

    Attributes:
        cmn_info (CmnInfo): Hardware topology/constraints.
        xppacks (list[_XpPack]): Constituent crosspoint event packs.
        total_events (int): Total unique events count.
        per_xp_cnt (dict[int, int]): Dict of XP to current number of events.
        event_list (list[str]): Cohort event keys in deterministic order.
        event_index (dict[str, int]): Maps event key to its index within group.

    Notes:
        For internal scheduler use only; not API/public facing.
    """

    cmn_info: "CmnInfo"
    xppacks: list[_XpPack] = field(default_factory=list)
    total_events: int = 0
    per_xp_cnt: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    cycle_key: Optional[str] = None

    # These fields are populated deterministically on finalisation
    event_list: List[str] = field(default_factory=list)
    event_index: Dict[str, int] = field(default_factory=dict)

    # ───── Helpers ────────────────────────────────────────────────
    def fits(self, pack: _XpPack) -> bool:
        """
        Can the given _XpPack be added to this cohort, under hardware constraints,
        including XP and watchpoint per-direction constraints?
        """
        # Use centralized violation checking with candidate events (all cohort events plus this pack)
        candidate_events: List[Event] = []
        for p in self.xppacks:
            candidate_events.extend(event_from_key(k) for k in p.events)
        candidate_events.extend(event_from_key(k) for k in pack.events)
        # All constraints (node, XP, DTC, and watchpoint per-direction) centralized in violates_local_constraints.
        return not violates_local_constraints(candidate_events, self.cmn_info)

    def add(self, pack: _XpPack) -> None:
        """
        Add an _XpPack to this cohort. Constraints must be checked before calling.
        """
        self.xppacks.append(pack)
        self.total_events += pack.size()
        self.per_xp_cnt[pack.xp_id] += pack.size()
        if pack.cycle_key is not None:
            if self.cycle_key is None:
                self.cycle_key = pack.cycle_key
            elif self.cycle_key != pack.cycle_key:
                raise ValueError("Multiple cycle keys detected in cohort")

    def finalise(self) -> None:
        """
        Compute a stable deterministic order for the events inside this cohort.
        Cycle event, if present, is always first.
        """
        ev: Set[str] = set()
        for p in self.xppacks:
            ev |= p.events

        ordered = sorted(ev, key=_event_sort_key)  # deterministic, xp, node, event_id numeric order
        if self.cycle_key is not None:
            ordered.insert(0, self.cycle_key)
        self.event_list = ordered
        self.event_index = {k: i for i, k in enumerate(ordered)}

    def __repr__(self) -> str:
        if self.event_list:
            base = ", ".join(self.event_list)
        else:
            ev_set: Set[str] = set()
            for p in self.xppacks:
                ev_set |= p.events
            base = ", ".join(sorted(ev_set))
            if self.cycle_key is not None and self.cycle_key not in base:
                base = f"{self.cycle_key}, " + base
        return f"[{base}]"


def _event_sort_key(key: str) -> Tuple[Any, ...]:
    """
    Sort key for deterministic ordering of event keys within a cohort.

    Order: cycle first, then by xp_id; device events first (node_id, event_type,
    event_id) then watchpoints (port, direction, chn_sel, grp, mask, value).
    """
    if _is_cycle_key(key):
        return (-1,)  # always first if present
    event = event_from_key(key)

    xp_id = event.xp_id
    if isinstance(event, CmnEvent):
        # Events: xp_id, 0, node_id, event_type, event_id
        return (
            xp_id,
            0,
            event.nodeid,
            event.type,
            event.eventid,
        )
    if isinstance(event, CmnWatchpoint):
        dir_sort = event.mesh_flit_dir
        # Watchpoints: xp_id, 1, port, dir_sort, chn_sel, grp, mask, value
        return (
            xp_id,
            1,
            event.port,
            dir_sort,
            event.wp_chn_sel,
            event.wp_grp,
            event.wp_mask,
            event.wp_val,
        )
    raise RuntimeError(f"Unknown event type for cohort sorting: {type(event)} {event!r}")


@dataclass
class _GGroup:
    """
    Helper for composing global groups with events from multiple event_types.
    Tracks per-type event membership as sets of keys. Does not enforce constraints; just for packing.
    """

    events: dict[int, set[str]] = field(default_factory=dict)
    cycle_key: Optional[str] = None

    def k_t(self, t: int) -> int:
        """
        Return the number of unique events for event_type t in this group.
        """
        return len(self.events.get(t, set()))

    def k_total(self) -> int:
        """
        Return the total count of non-cycle events in this group.
        """
        return sum(len(evs) for evs in self.events.values())

    def size(self) -> int:
        """
        Alias for k_total(), ignores cycle.
        """
        return self.k_total()

    def freeze(self) -> tuple:
        """
        Return canonical group: (cycle_event_if_present, then all keys sorted as Event)
        """
        all_keys: List[str] = []
        for key_set in self.events.values():
            all_keys.extend(key_set)
        sorted_keys = sorted(all_keys)
        tup = tuple(event_from_key(k) for k in sorted_keys)
        if self.cycle_key is not None:
            return (event_from_key(self.cycle_key),) + tup
        return tup

    def can_accept(
        self,
        extra: dict[int, set[str]],
        cmn_info: "CmnInfo",
    ) -> bool:
        """
        True if this group can accept all events in `extra` (by type: set of key strings)
        without violating global constraints.

        Args:
            extra: Dict[event_type, set of event keys] to try adding.
            cmn_info: The CmnInfo hardware topology.

        Returns:
            bool: True if all slot constraints pass, False if any violated.
        """
        combined: dict[int, set[str]] = {}
        all_types = set(self.events) | set(extra)
        for t in all_types:
            combined[t] = set(self.events.get(t, set())) | set(extra.get(t, set()))
        combined_events: List[Event] = []
        for keys in combined.values():
            combined_events.extend(event_from_key(k) for k in keys)
        return not violates_global_constraints(combined_events, cmn_info)

    def add(self, extra: dict[int, set[str]], cycle_key: Optional[str] = None) -> None:
        """
        Mutate this group by adding all extra non-cycle event keys to their type sets.
        """
        for t, keys in extra.items():
            if t not in self.events:
                self.events[t] = set()
            self.events[t].update(keys)
        if cycle_key is not None:
            self.cycle_key = cycle_key


def _find_exact_ggroup(
    groups: Sequence["_GGroup"],
    event_keys: Set[str],
    cycle_key: Optional[str],
) -> Optional[int]:
    for idx, grp in enumerate(groups):
        grp_keys: Set[str] = set()
        for key_set in grp.events.values():
            grp_keys.update(key_set)
        if grp_keys == event_keys and grp.cycle_key == cycle_key:
            return idx
    return None


def _expand_global_device_events(
    events: Sequence[CmnEvent],
    cmn_info: "CmnInfo",
) -> List[CmnEvent]:
    expanded: List[CmnEvent] = []
    for ev in events:
        if not ev.is_global():
            raise ValueError("Expected global event without xp_id/nodeid")
        for _dtc, xp, node in cmn_info.nodes_of_global_type(ev.type):
            expanded.append(
                CmnEvent(
                    name=ev.name,
                    title=ev.title,
                    description=ev.description,
                    cmn_index=ev.cmn_index,
                    type=ev.type,
                    eventid=ev.eventid,
                    occupid=ev.occupid,
                    nodeid=node,
                    xp_id=xp,
                )
            )
    return expanded


def _expand_global_watchpoints(
    watchpoints: Sequence[CmnWatchpoint],
    cmn_info: "CmnInfo",
) -> List[CmnWatchpoint]:
    expanded: List[CmnWatchpoint] = []
    for wp in watchpoints:
        if wp.device is None:
            raise ValueError("Expected global watchpoint with device")
        ports = cmn_info.watchpoint_ports_for_device(wp.device)
        for port in ports:
            expanded.append(
                CmnWatchpoint(
                    name=wp.name,
                    title=wp.title,
                    description=wp.description,
                    cmn_index=wp.cmn_index,
                    mesh_flit_dir=wp.mesh_flit_dir,
                    wp_chn_sel=wp.wp_chn_sel,
                    wp_grp=wp.wp_grp,
                    wp_mask=wp.wp_mask,
                    wp_val=wp.wp_val,
                    xp_id=port.xp_id,
                    port=port.port,
                    device=None,
                )
            )
    return expanded


def _expand_global_mixed_events(
    events: Sequence[Event],
    cmn_info: "CmnInfo",
) -> List[Event]:
    device_events = [ev for ev in events if isinstance(ev, CmnEvent)]
    watchpoints = [ev for ev in events if isinstance(ev, CmnWatchpoint)]
    expanded: List[Event] = []
    if device_events:
        expanded.extend(_expand_global_device_events(device_events, cmn_info))
    if watchpoints:
        expanded.extend(_expand_global_watchpoints(watchpoints, cmn_info))
    return expanded


def _expand_global_watchpoint_keys(keys: Set[str], cmn_info: "CmnInfo") -> List[CmnWatchpoint]:
    watchpoints = [cast(CmnWatchpoint, event_from_key(k)) for k in keys]
    return _expand_global_watchpoints(watchpoints, cmn_info)


@dataclass
class _GlobalWatchpointGroup:
    keys: Set[str] = field(default_factory=set)
    cycle_key: Optional[str] = None

    def can_accept(self, extra_keys: Set[str], cmn_info: "CmnInfo") -> bool:
        candidate_keys = self.keys | extra_keys
        events = [cast(CmnWatchpoint, event_from_key(k)) for k in candidate_keys]
        return not violates_global_constraints(events, cmn_info)

    def add(self, extra_keys: Set[str], cycle_key: Optional[str] = None) -> None:
        self.keys.update(extra_keys)
        if cycle_key is not None:
            self.cycle_key = cycle_key

    def freeze(self) -> tuple:
        sorted_keys = sorted(self.keys)
        tup = tuple(event_from_key(k) for k in sorted_keys)
        if self.cycle_key is not None:
            return (event_from_key(self.cycle_key),) + tup
        return tup


def _find_exact_global_watchpoint_group(
    groups: Sequence["_GlobalWatchpointGroup"],
    keys: Set[str],
    cycle_key: Optional[str],
) -> Optional[int]:
    for idx, grp in enumerate(groups):
        if grp.keys == keys and grp.cycle_key == cycle_key:
            return idx
    return None


def _is_small_global_tuple(tup: "_TupleReq", cmn_info: "CmnInfo") -> bool:
    """
    Returns True if the tuple of events can fit into an empty _GGroup, i.e., does not violate
    any global event constraint.

    Args:
        tup: The tuple of events (internal scheduler format).
        cmn_info: Hardware topology/constraint info.

    Returns:
        bool: True if tuple fits as a single global group.

    """
    # Classify events by type (ignore cycle for constraints)
    events = [ev for ev in tup.events if not ev.is_cycle()]
    return not violates_global_constraints(events, cmn_info)


# ---- Hardware Constraint Evaluation ----


# pylint: disable=too-many-return-statements
def violates_global_constraints(events: Iterable["Event"], cmn_info: "CmnInfo") -> bool:
    """
    Returns True if the given events violate any node, crosspoint (XP), or DTC
    capacity constraints for GLOBAL events/watchpoints.

    Device events:
    - Node: #distinct events per type > MAX_EVENTS_PER_NODE? (fail)
    - XP: sum_t (num_nodes type t on this XP) * (num events for type t)

    Watchpoints:
    - XP: count of global watchpoint keys targeting this XP
    - Per-direction: count per XP+direction (UP/DOWN)

    DTC:
    - device usage: #event types present in this DTC and in input
    - watchpoint usage: #global watchpoint keys present in this DTC
    - total > MAX_EVENTS_PER_DTC? (fail)
    """
    events_by_type: dict[int, set[str]] = {}
    device_events: List[CmnEvent] = []
    watchpoints: List[CmnWatchpoint] = []

    for ev in events:
        if ev.is_cycle():
            continue
        if isinstance(ev, CmnWatchpoint):
            if ev.device is None:
                raise ValueError("Expected global watchpoint with device")
            watchpoints.append(ev)
        else:
            device_ev = cast(CmnEvent, ev)
            effective_type = cmn_info.global_type_of(device_ev.type)
            events_by_type.setdefault(effective_type, set()).add(device_ev.key())
            device_events.append(device_ev)

    for t, keys in events_by_type.items():
        if len(keys) > MAX_EVENTS_PER_NODE:
            return True
    if _has_occupid_conflict(device_events):
        return True

    xp_type_node_cnt = cmn_info.xp_global_type_node_counts()
    xp_wp_count: dict[int, int] = {}
    xp_wp_dir_count: dict[tuple[int, int], int] = {}
    dtc_wp_keys: dict[int, Set[str]] = {}
    for wp in watchpoints:
        device = cast(str, wp.device)
        ports = cmn_info.watchpoint_ports_for_device(device)
        key = wp.key()
        for port in ports:
            xp = port.xp_id
            xp_wp_count[xp] = xp_wp_count.get(xp, 0) + 1
            xp_wp_dir_count[(xp, wp.mesh_flit_dir)] = (
                xp_wp_dir_count.get((xp, wp.mesh_flit_dir), 0) + 1
            )
            dtc = cmn_info.dtc_of(xp)
            dtc_wp_keys.setdefault(dtc, set()).add(key)

    for count in xp_wp_dir_count.values():
        if count > MAX_WATCHPOINTS_PER_DIRECTION:
            return True

    # XP-level: For every XP, check slot consumption (device + watchpoint)
    for xp, node_cnt_by_type in xp_type_node_cnt.items():
        s = 0
        for t, num_nodes in node_cnt_by_type.items():
            s += num_nodes * len(events_by_type.get(t, set()))
        s += xp_wp_count.get(xp, 0)
        if s > MAX_EVENTS_PER_XP:
            return True
    for xp, count in xp_wp_count.items():
        if xp not in xp_type_node_cnt and count > MAX_EVENTS_PER_XP:
            return True

    xp_to_dtc = {xp: cmn_info.dtc_of(xp) for xp in set(xp_type_node_cnt) | set(xp_wp_count)}
    for dtc in set(xp_to_dtc.values()):
        types_for_dtc: Set[int] = set()
        for xp, type_map in xp_type_node_cnt.items():
            if xp_to_dtc[xp] == dtc:
                types_for_dtc.update(type_map.keys())
        used_types = [t for t in types_for_dtc if events_by_type.get(t)]
        device_slots = len(used_types)
        wp_slots = len(dtc_wp_keys.get(dtc, set()))
        if device_slots + wp_slots > MAX_EVENTS_PER_DTC:
            return True

    return False


def violates_local_constraints(events: Iterable["Event"], cmn_info: "CmnInfo") -> bool:
    """
    Returns True if local events would violate any hardware-imposed constraints
    on packing: node slots, crosspoint slots, or DTC slots for this configuration.

    Args:
        events: Iterable of Event objects (without cycle) to test.
        cmn_info: Hardware configuration/topology.

    Returns:
        bool: True if any slot constraint is exceeded; False otherwise.
    """
    # For device events
    # Device event: track per-node count
    node_event_count: dict[tuple[int, int], int] = {}
    # Watchpoint: per-XP, per-direction count
    xp_wp_dir: dict[tuple[int, int], int] = {}
    # All events (device + WP): per-XP, per-DTC count
    xp_event_count: dict[int, int] = {}
    dtc_event_sum: dict[int, int] = {}

    for ev in events:
        if isinstance(ev, CmnEvent):
            if ev.xp_id is None:
                raise ValueError("Invalid local event without location information")
            # Only device events are constrained by per-node limit
            if ev.nodeid is None:
                raise ValueError("Invalid local device event without nodeid information")
            node_key = (ev.xp_id, ev.nodeid)
            node_event_count.setdefault(node_key, 0)
            node_event_count[node_key] += 1
            xp_id = ev.xp_id
        elif isinstance(ev, CmnWatchpoint):
            if ev.xp_id is None:
                raise ValueError("Invalid local event without location information")
            # Only watchpoints constrained by per-XP/per-dir limit
            xp_wp_dir.setdefault((ev.xp_id, ev.mesh_flit_dir), 0)
            xp_wp_dir[(ev.xp_id, ev.mesh_flit_dir)] += 1
            xp_id = ev.xp_id
        else:
            raise RuntimeError(f"Unknown event type in violates_local_constraints: {type(ev)}")
        # All events (device and WP) count toward per-XP and per-DTC limits
        xp_event_count.setdefault(xp_id, 0)
        xp_event_count[xp_id] += 1
        dtc = cmn_info.dtc_of(xp_id)
        dtc_event_sum.setdefault(dtc, 0)
        dtc_event_sum[dtc] += 1

    # Device event node cap
    for _, cnt in node_event_count.items():
        if cnt > MAX_EVENTS_PER_NODE:
            return True

    # Watchpoint XP cap (MAX_WATCHPOINTS_PER_DIRECTION for each direction)
    for _, cnt in xp_wp_dir.items():
        if cnt > MAX_WATCHPOINTS_PER_DIRECTION:
            return True

    # Device + WP XP cap (max 4 total)
    for _, cnt in xp_event_count.items():
        if cnt > MAX_EVENTS_PER_XP:
            return True

    # DTC sum cap (both WPs and device events)
    for dtc, total_events in dtc_event_sum.items():
        if total_events > MAX_EVENTS_PER_DTC:
            return True

    return _has_occupid_conflict(events)


def _has_occupid_conflict(events: Iterable["Event"]) -> bool:
    """
    Returns True if *events* contain two device events that address the same
    (event_type, xp_id, node_id) but with different occupid values
    (None and 0 are considered identical).
    """
    occ: dict[tuple[int | None, int | None, int | None], int] = {}
    for ev in events:
        if isinstance(ev, CmnEvent) and not ev.is_cycle():
            key = (ev.type, ev.xp_id, ev.nodeid)
            o = 0 if ev.occupid is None else ev.occupid
            if key in occ and occ[key] != o:
                return True
            occ[key] = o
    return False


# --------------------------------------------------------------------
# 3.  Packing algorithm  (identical logic to *packer_demo_2.py*)
# --------------------------------------------------------------------


def _build_xppacks(tuples: List[_TupleReq], cmn_info: "CmnInfo") -> List[_XpPack]:
    """
    Stage 1:
        Pack each xp's events into XpPacks.
        Any tuple for which violates_local_constraints(events, cmn_info)
        is True is split/scattered, all others are packed greedily together.
    """
    small: List[_TupleReq] = []
    large: List[_TupleReq] = []
    for t in tuples:
        noncycle_events = [e for e in t.events if not e.is_cycle()]
        if violates_local_constraints(noncycle_events, cmn_info):
            large.append(t)
        else:
            small.append(t)

    packs: List[_XpPack] = []
    # 1) Greedy pack small tuples
    if small:
        sorted_small = sorted(
            small,
            key=lambda t: sum(1 for e in t.events if not e.is_cycle()),
            reverse=True,
        )
        curr_events: Set[str] = set()
        curr_cycle_key: Optional[str] = None
        curr_ids: List[int] = []
        xp_id = cast(int, sorted_small[0].xp_id)
        for t in sorted_small:
            ev_set = {e.key() for e in t.events if not e.is_cycle()}
            new_events = ev_set - curr_events
            candidate_event_objs = [event_from_key(k) for k in (curr_events | new_events)]
            if violates_local_constraints(candidate_event_objs, cmn_info):
                packs.append(
                    _XpPack(
                        xp_id=xp_id,
                        events=set(curr_events),
                        cycle_key=curr_cycle_key,
                        tuple_ids=curr_ids[:],
                    )
                )
                curr_events.clear()
                curr_cycle_key = None
                curr_ids.clear()
            curr_events.update(ev_set)
            if any(e.is_cycle() for e in t.events):
                cycle_key = next(e.key() for e in t.events if e.is_cycle())
                if curr_cycle_key is None:
                    curr_cycle_key = cycle_key
                elif curr_cycle_key != cycle_key:
                    raise ValueError("Multiple cycle keys detected in xp pack")
            curr_ids.append(t.id)
        if curr_events or curr_cycle_key is not None:
            packs.append(
                _XpPack(
                    xp_id=xp_id,
                    events=set(curr_events),
                    cycle_key=curr_cycle_key,
                    tuple_ids=curr_ids[:],
                )
            )

    def _is_mixed_tuple(tup: _TupleReq) -> bool:
        noncycle = [e for e in tup.events if not e.is_cycle()]
        has_event = any(isinstance(e, CmnEvent) for e in noncycle)
        has_wp = any(isinstance(e, CmnWatchpoint) for e in noncycle)
        return has_event and has_wp

    # 2) Scatter large tuples (exceeding any HW constraint): attach to existing small packs, leftover events (and cycle) become singleton spill packs
    original_packs = list(packs)
    for t in large:
        if _is_mixed_tuple(t):
            xp = cast(int, t.xp_id)
            mixed_cycle_key = next((e.key() for e in t.events if e.is_cycle()), None)
            event_groups: dict[tuple[int, int], Set[str]] = {}
            watchpoint_groups: List[Set[str]] = []
            for ev in t.events:
                if ev.is_cycle():
                    continue
                if isinstance(ev, CmnWatchpoint):
                    watchpoint_groups.append({ev.key()})
                    continue
                event_id = cast(int, ev.eventid)
                event_groups.setdefault((ev.type, event_id), set()).add(ev.key())
            group_sets = list(event_groups.values()) + watchpoint_groups
            expanded_groups: List[Set[str]] = []
            for group_set in group_sets:
                candidate_events = [event_from_key(k) for k in group_set]
                if violates_local_constraints(candidate_events, cmn_info):
                    for key in sorted(group_set):
                        expanded_groups.append({key})
                else:
                    expanded_groups.append(group_set)
            assigned_packs: List[_XpPack] = []
            for group_set in expanded_groups:
                assigned = False
                for p in original_packs:
                    if p.xp_id != xp:
                        continue
                    if group_set.issubset(p.events):
                        if t.id not in p.tuple_ids:
                            p.tuple_ids.append(t.id)
                        assigned_packs.append(p)
                        assigned = True
                        break
                if assigned:
                    continue
                new_pack = _XpPack(
                    xp_id=xp,
                    events=set(group_set),
                    cycle_key=None,
                    tuple_ids=[t.id],
                    spill=True,
                )
                packs.append(new_pack)
                assigned_packs.append(new_pack)
            if mixed_cycle_key is not None:
                if assigned_packs:
                    pack = assigned_packs[0]
                    if pack.cycle_key is None:
                        pack.cycle_key = mixed_cycle_key
                    elif pack.cycle_key != mixed_cycle_key:
                        raise ValueError("Multiple cycle keys detected in xp pack")
                else:
                    packs.append(
                        _XpPack(
                            xp_id=xp,
                            events=set(),
                            cycle_key=mixed_cycle_key,
                            tuple_ids=[t.id],
                            spill=True,
                        )
                    )
            continue
        xp = cast(int, t.xp_id)
        ev_keys = [e.key() for e in t.events if not e.is_cycle()]
        ev_set = set(ev_keys)
        scatter_cycle_key = next((e.key() for e in t.events if e.is_cycle()), None)
        # 2a) assign cycle: try to attach to first small pack on same xp; otherwise standalone
        if scatter_cycle_key is not None:
            assigned_cycle = False
            for p in original_packs:
                if p.xp_id == xp:
                    if t.id not in p.tuple_ids:
                        p.tuple_ids.append(t.id)
                    if p.cycle_key is None:
                        p.cycle_key = scatter_cycle_key
                    elif p.cycle_key != scatter_cycle_key:
                        raise ValueError("Multiple cycle keys detected in xp pack")
                    assigned_cycle = True
                    break
            if not assigned_cycle:
                packs.append(
                    _XpPack(
                        xp_id=xp,
                        events=set(),
                        cycle_key=scatter_cycle_key,
                        tuple_ids=[t.id],
                        spill=True,
                    )
                )
        # 2b) record tuple_id in any original pack sharing events
        for p in original_packs:
            if p.xp_id != xp:
                continue
            common = p.events & ev_set
            if not common:
                continue
            if t.id not in p.tuple_ids:
                p.tuple_ids.append(t.id)
            ev_set -= common
        # 2c) leftover event keys become singleton spill packs
        for key in ev_keys:
            if key in ev_set:
                packs.append(
                    _XpPack(
                        xp_id=xp,
                        events={key},
                        cycle_key=None,
                        tuple_ids=[t.id],
                        spill=True,
                    )
                )
                ev_set.remove(key)

    # Deduplicate identical XpPacks
    dedup: Dict[tuple[int | None, frozenset[str], Optional[str], bool], _XpPack] = {}
    for p in packs:
        p_key = (p.xp_id, frozenset(p.events), p.cycle_key, p.spill)
        if p_key in dedup:
            for tid in p.tuple_ids:
                if tid not in dedup[p_key].tuple_ids:
                    dedup[p_key].tuple_ids.append(tid)
        else:
            dedup[p_key] = p
    return list(dedup.values())


def _pack_xppacks_to_cohorts(xppacks: List[_XpPack], cmn_info: "CmnInfo") -> List[_Cohort]:
    """
    Stage 2:
        Pack XpPacks into minimal number of Cohorts per DTC,
        such that each cohort satisfies hardware-wide group constraints.
        Order and packing are deterministic (sort by size, pack until full, then new cohort).
        Spill packs (singleton leftover events) each form their own cohort.
    """
    # 1) Pack primary (non-spill) XpPacks
    primary = [p for p in xppacks if not p.spill]
    primary_sorted = sorted(primary, key=lambda p: p.size(), reverse=True)
    cohorts: List[_Cohort] = []
    for p in primary_sorted:
        placed = False
        for c in cohorts:
            if c.fits(p):
                c.add(p)
                placed = True
                break
        if not placed:
            c = _Cohort(cmn_info=cmn_info)
            c.add(p)
            cohorts.append(c)
    for c in cohorts:
        c.finalise()
    # 2) Spill packs each in their own cohort
    for p in xppacks:
        if p.spill:
            c = _Cohort(cmn_info=cmn_info)
            c.add(p)
            c.finalise()
            cohorts.append(c)

    merged: List[_Cohort] = []
    cycle_only_idx_by_key: Dict[str, int] = {}
    for cohort in cohorts:
        if cohort.cycle_key is None or cohort.total_events != 0:
            merged.append(cohort)
            continue

        cycle_key = cohort.cycle_key
        existing_idx = cycle_only_idx_by_key.get(cycle_key)
        if existing_idx is None:
            cycle_only_idx_by_key[cycle_key] = len(merged)
            merged.append(cohort)
            continue

        # Cycle-only cohorts all flatten to the same public group `(cycle,)`,
        # so merge their tuple ownership into a single canonical cohort.
        merged[existing_idx].xppacks.extend(cohort.xppacks)

    for idx in cycle_only_idx_by_key.values():
        merged[idx].finalise()
    return merged


def _build_decode_map(
    dtc_cohorts: Dict[int, List[_Cohort]], tuples: List[_TupleReq]
) -> Dict[int, dict[str, Tuple[int, int, int]]]:
    """
    Stage 3: Build mapping for result retrieval.
    For every user metric tuple, builds:
        tuple_id -> dict of event.key() -> (dtc, cohort_idx, event_idx_in_group).

    This allows retrieval to preserve the user's order (and register) regardless of how group events are packed.
    """
    id2tuple = {t.id: t for t in tuples}
    decode: Dict[int, dict[str, Tuple[int, int, int]]] = {t.id: {} for t in tuples}

    for dtc, cohorts in dtc_cohorts.items():
        for c_idx, cohort in enumerate(cohorts):
            # map only events actually in this cohort
            cohort_keys = set(cohort.event_list)
            for pack in cohort.xppacks:
                for t_id in pack.tuple_ids:
                    tup = id2tuple[t_id]
                    for ev in tup.events:  # preserve user order
                        key = ev.key()
                        if key not in cohort_keys:
                            continue
                        e_idx = cohort.event_index[key]
                        decode[t_id][key] = (dtc, c_idx, e_idx)
    return decode


def _merge_cycle_only_cohorts_across_dtcs(
    dtc_cohorts: Dict[int, List[_Cohort]]
) -> Dict[int, List[_Cohort]]:
    merged_by_dtc: Dict[int, List[_Cohort]] = {}
    cycle_owner_by_key: Dict[str, tuple[int, _Cohort]] = {}

    for dtc, cohorts in dtc_cohorts.items():
        merged_cohorts: List[_Cohort] = []
        for cohort in cohorts:
            if cohort.cycle_key is None or cohort.total_events != 0:
                merged_cohorts.append(cohort)
                continue

            cycle_key = cohort.cycle_key
            owner = cycle_owner_by_key.get(cycle_key)
            if owner is None:
                cycle_owner_by_key[cycle_key] = (dtc, cohort)
                merged_cohorts.append(cohort)
                continue

            _, owner_cohort = owner
            owner_cohort.xppacks.extend(cohort.xppacks)
            owner_cohort.finalise()
        merged_by_dtc[dtc] = merged_cohorts

    return merged_by_dtc


class CmnScheduler:
    """
    CMN Event Packing Scheduler and Result Retriever
    ================================================
    Optimizes and manages event cohort scheduling for multi-node, DTC aware.
    Provides deduplication, cohort packing under CMN constraints, and strict result retrieval.

    Invariants and Contract:
        - All input metric tuples must be composed entirely of valid Events.
        - Device-only tuples may not mix events from different nodes (except "cycle" event).
          Mixed tuples may span nodes but must share a single xp_id.
        - Duplicates and permutations are deduplicated, but retrieval is only allowed in the registered, exact tuple order.
        - Each unique event tuple requested is packed with others under hardware rules:
            * Max MAX_EVENTS_PER_DTC events per DTC (plus one 'cycle')
            * Max MAX_EVENTS_PER_XP events per node per group
        - get_optimized_event_groups() returns the minimal list of event tuples you must pass to your PMU/perf collection.
        - You MUST supply perf_result whose keys exactly match (no more, no less) and whose values exactly match length and event order in these groups.
        - Retrieval is strict and deterministic: you always get back exactly what you put in, in the same order, or an error is raised.

    Errors:
        - ValueError, TypeError, or KeyError for all contract violations. See individual method docstrings for details.

    Example:

        sched = CmnScheduler([...metrics...], cmn_info)
        optimized = sched.get_optimized_event_groups()
        # ...collect perf_result...
        values = sched.retrieve_metric_result(perf_result, input_tuple)

    """

    @classmethod
    def _canonicalize_metrics(
        cls,
        metrics: Sequence[Tuple[Event, ...]],
    ) -> Tuple[List[Tuple[Event, ...]], Dict[Tuple[str, ...], int], Set[Tuple[Event, ...]]]:
        """
        Return ordered, deduplicated metric tuples alongside lookup structures.

        Deduplication is order-insensitive (based on event keys), but retrieval is
        only allowed using the exact user-provided tuple order.
        """
        ordered_metrics: List[Tuple[Event, ...]] = []
        seen_keys: Set[Tuple[str, ...]] = set()
        for metric in metrics:
            sorted_metric = tuple(sorted(metric, key=lambda e: e.key()))
            key = tuple(e.key() for e in sorted_metric)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            ordered_metrics.append(sorted_metric)

        metric_key_to_id = {
            tuple(e.key() for e in metric): idx for idx, metric in enumerate(ordered_metrics)
        }
        input_tuples_set = set(metrics)
        return ordered_metrics, metric_key_to_id, input_tuples_set

    @staticmethod
    def _validate_device_tuple(noncycle: Sequence[CmnEvent], cmn_info: CmnInfo) -> None:
        node_types = {ev.type for ev in noncycle}
        if len(node_types) != 1:
            raise ValueError(
                "All non-cycle events in a device event tuple must have the same event_type (node_type)"
            )
        is_global = all(ev.is_global() for ev in noncycle)
        is_local = all(not ev.is_global() for ev in noncycle)
        if not (is_global or is_local):
            raise ValueError("Cannot mix global and local events in a device tuple")
        if is_global:
            event_type = noncycle[0].type
            # Check that no XP in cmn_info hosts >MAX_EVENTS_PER_XP effective nodes for this
            # backend-visible global event type.
            xp_count: dict[int, int] = {}
            for _dtc, xp, _ in cmn_info.nodes_of_global_type(event_type):
                xp_count[xp] = xp_count.get(xp, 0) + 1
            for xp, count in xp_count.items():
                if count > MAX_EVENTS_PER_XP:
                    raise ValueError(
                        f"XP {xp} hosts {count} effective nodes of global event type {event_type} in topology—global event tuple not allowed"
                    )
            return
        xp0 = noncycle[0].xp_id
        node0 = noncycle[0].nodeid
        for ev in noncycle:
            if ev.xp_id != xp0:
                raise ValueError("All events in a local tuple must have the same xp_id")
            if ev.nodeid != node0:
                raise ValueError("All events in a local tuple must have the same node_id")

    @staticmethod
    def _validate_watchpoint_tuple(
        noncycle: Sequence[CmnWatchpoint],
        cmn_info: CmnInfo,
    ) -> None:
        # Watchpoint tuple rules:
        # - All must have the same xp_id for local watchpoints
        # - Global watchpoints must all use the same device
        # - Tuple can be empty
        is_global = all(ev.is_global() for ev in noncycle)
        is_local = all(not ev.is_global() for ev in noncycle)
        if not (is_global or is_local):
            raise ValueError("Cannot mix global and local watchpoints in a tuple")
        if is_global:
            devices = {ev.device for ev in noncycle}
            if len(devices) != 1:
                raise ValueError("All global Watchpoints in a tuple must have the same device")
            device = next(iter(devices))
            if device is None:
                raise ValueError("Global Watchpoints must define a device")
            cmn_info.watchpoint_ports_for_device(device)
            return
        xp0 = noncycle[0].xp_id
        if xp0 is None:
            raise ValueError("Watchpoints must have an xp_id for local tuples")
        for ev in noncycle:
            if not isinstance(ev, CmnWatchpoint):
                raise ValueError("All events in a watchpoint tuple must be Watchpoint")
            if ev.xp_id != xp0:
                raise ValueError("All Watchpoints in a tuple must have the same xp_id")

    @staticmethod
    def _validate_mixed_tuple(
        noncycle: Sequence[Event],
        cmn_info: CmnInfo,
    ) -> None:
        is_global = all(ev.is_global() for ev in noncycle)
        is_local = all(not ev.is_global() for ev in noncycle)
        if not (is_global or is_local):
            raise ValueError("Cannot mix local and global events in a tuple")
        if is_global:
            watchpoints = [ev for ev in noncycle if isinstance(ev, CmnWatchpoint)]
            if watchpoints:
                devices = {wp.device for wp in watchpoints}
                if len(devices) != 1:
                    raise ValueError(
                        "All global Watchpoints in a tuple must have the same device"
                    )
                device = next(iter(devices))
                if device is None:
                    raise ValueError("Global Watchpoints must define a device")
                cmn_info.watchpoint_ports_for_device(device)
            return
        xp_ids = {ev.xp_id for ev in noncycle}
        if None in xp_ids:
            raise ValueError("Mixed local tuples require xp_id for all events")
        if len(xp_ids) != 1:
            raise ValueError("All events in a local mixed tuple must have the same xp_id")
        for ev in noncycle:
            if isinstance(ev, CmnEvent):
                cmn_info.port_of(cast(int, ev.xp_id), cast(int, ev.nodeid))
            elif isinstance(ev, CmnWatchpoint) and ev.device is not None:
                raise ValueError("Local mixed tuples cannot contain global watchpoints")

    @staticmethod
    def _partition_local_global_tuples(
        tuples: Sequence[_TupleReq],
    ) -> Tuple[List[_TupleReq], List[_TupleReq], List[_TupleReq], List[_TupleReq]]:
        local_tuples: List[_TupleReq] = []
        global_device_tuples: List[_TupleReq] = []
        global_watchpoint_tuples: List[_TupleReq] = []
        global_mixed_tuples: List[_TupleReq] = []
        for tup in tuples:
            noncycle = [e for e in tup.events if not e.is_cycle()]
            if not noncycle:
                # Tuples with only cycles treated like local
                local_tuples.append(tup)
            elif any(isinstance(e, CmnWatchpoint) for e in noncycle) and any(
                isinstance(e, CmnEvent) for e in noncycle
            ):
                if all(e.is_global() for e in noncycle):
                    global_mixed_tuples.append(tup)
                else:
                    local_tuples.append(tup)
            elif all(isinstance(e, CmnWatchpoint) and e.is_global() for e in noncycle):
                global_watchpoint_tuples.append(tup)
            elif all(e.is_global() for e in noncycle):
                global_device_tuples.append(tup)
            else:
                local_tuples.append(tup)
        return local_tuples, global_device_tuples, global_watchpoint_tuples, global_mixed_tuples

    def __init__(self, metrics: Sequence[Tuple[Event, ...]], cmn_info: CmnInfo):
        """
        Constructs scheduler and computes all packing.

        Args:
            metrics: List of metric event tuples.
                - Each tuple may contain only Event, only Watchpoint, or a mix of both.
                - Mixed tuples must be entirely local or entirely global.
            cmn_info: CmnInfo describing the topology and DTC assignment.

        Performs:
            - Deduplication (order-insensitive across tuples)
            - Validation of input invariants:
                - Event tuples: all must match xp_id/node_id rules, global/local node type constraints.
                - Watchpoint tuples: all-local tuples must use the same xp_id; global tuples must use a
                  single device.
            - Packing into XpPacks (per xp, over device + WP), then Cohorts (DTC-legal groups)
            - Finalization of all event groups for downstream collection

        Errors:
            - Raises ValueError if tuple contains events from multiple crosspoints or nodes,
              or mixes local/global kinds.
            - Raises TypeError if xp_id or node_id is not int/None as permitted.
            - Hardware constraint violations raise errors at retrieval/packing.

        Returns:
            Instance ready to optimize or unpack event groups.
        """

        # 1. Deduplicate metrics (order-insensitive but only user-entered tuple may be retrieved)
        (
            self._metrics,
            self._metric_key_to_id,
            self._input_tuples_set,
        ) = self._canonicalize_metrics(metrics)
        self._cmn_info = cmn_info

        # Validate kind and tuple rules for each user-supplied metric
        for m in self._metrics:
            if not m:
                continue
            noncycle = [e for e in m if not e.is_cycle()]
            if not noncycle:
                continue

            # Determine event kind
            is_all_device = all(isinstance(e, CmnEvent) for e in noncycle)
            is_all_watchpoint = all(isinstance(e, CmnWatchpoint) for e in noncycle)
            if is_all_device:
                self._validate_device_tuple(
                    cast(Sequence[CmnEvent], noncycle),
                    self._cmn_info,
                )
            elif is_all_watchpoint:
                self._validate_watchpoint_tuple(
                    cast(Sequence[CmnWatchpoint], noncycle),
                    self._cmn_info,
                )
            else:
                self._validate_mixed_tuple(noncycle, self._cmn_info)

        # Internal representation: only non-empty
        tuples: List[_TupleReq] = [
            _TupleReq(idx, tuple_ev) for idx, tuple_ev in enumerate(self._metrics) if tuple_ev
        ]

        # Separate local and global tuples
        (
            self._local_tuples,
            self._global_tuples,
            self._global_watchpoint_tuples,
            self._global_mixed_tuples,
        ) = self._partition_local_global_tuples(tuples)

        # 2. Bucket by DTC and crosspoint for packing (local tuples only)
        buckets: Dict[int, Dict[int | None, List[_TupleReq]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for t in self._local_tuples:
            if t.xp_id is None:
                dtc = 0
                xp_id = None
            else:
                dtc = self._cmn_info.dtc_of(t.xp_id)
                xp_id = t.xp_id
            buckets[dtc][xp_id].append(t)

        # 3. Compose XpPacks and Cohorts per DTC (now crosspoint-packs)
        self._dtc_cohorts: Dict[int, List[_Cohort]] = {}
        for dtc in range(self._cmn_info.dtc_count):
            xppacks: List[_XpPack] = []
            for _, tlist in buckets[dtc].items():
                xppacks.extend(_build_xppacks(tlist, self._cmn_info))
            self._dtc_cohorts[dtc] = _pack_xppacks_to_cohorts(xppacks, self._cmn_info)
        self._dtc_cohorts = _merge_cycle_only_cohorts_across_dtcs(self._dtc_cohorts)

        # 4. Flatten to event groups for user/PMU flow
        self._event_groups: List[Tuple[Event, ...]] = []
        self._cohort_to_group: Dict[Tuple[int, int], Tuple[Event, ...]] = {}

        for dtc, cohorts in self._dtc_cohorts.items():
            for idx, cohort in enumerate(cohorts):
                ev_tuple = tuple(event_from_key(k) for k in cohort.event_list)
                self._event_groups.append(ev_tuple)
                self._cohort_to_group[(dtc, idx)] = ev_tuple

        # No special explicit empty groups: Only non-empty event tuples are optimized/collected groups.

        # 5. Build decode map for retrieval (user tuple to raw perf_result)
        self._decode_map = _build_decode_map(self._dtc_cohorts, self._local_tuples)

        # 6. Global tuple pipeline (global-event-only tuples)
        groups, _, _ = self._build_global_groups(self._cmn_info)
        for idx, group in enumerate(groups):
            self._event_groups.append(group)
            # Use GLOBAL_DTC_SENTINEL index for global groups
            self._cohort_to_group[(GLOBAL_DTC_SENTINEL, idx)] = group

        # 7. Global mixed tuples (device + watchpoint)
        mixed_groups = self._build_global_mixed_groups(self._cmn_info, len(groups))
        for idx, group in enumerate(mixed_groups):
            self._event_groups.append(group)
            self._cohort_to_group[(GLOBAL_DTC_SENTINEL, len(groups) + idx)] = group

        # 8. Global watchpoint tuples
        wp_groups = self._build_global_watchpoint_groups(
            self._cmn_info, len(groups) + len(mixed_groups)
        )
        for idx, group in enumerate(wp_groups):
            self._event_groups.append(group)
            self._cohort_to_group[
                (GLOBAL_DTC_SENTINEL, len(groups) + len(mixed_groups) + idx)
            ] = group
        # Decode map for global tuples is already filled inline (see _build_global_groups/_build_global_mixed_groups/_build_global_watchpoint_groups).

        if len(self._event_groups) != len(set(self._event_groups)):
            raise RuntimeError("Scheduler produced duplicate optimized groups")

    def get_optimized_event_groups(self) -> Sequence[Tuple[Event, ...]]:
        """
        Returns:
            Ordered list of event groups (tuples of Event objects) that your
            measurement backend (perf, PMU, collection code) must record, in exact order.
            These are the keys which you must supply in perf_result when retrieving.

        Invariant:
            - Each group will contain no more than MAX_EVENTS_PER_DTC events and will conform to all hardware packing constraints.
            - Only non-empty groups are returned.
        """
        return self._event_groups

    def retrieve_metric_result(
        self,
        perf_result: Dict[Tuple[Event, ...], Tuple[Optional[float], ...]],
        metric_events: Tuple[Event, ...],
    ) -> Tuple[Optional[float], ...]:
        """
        Retrieve result for a requested metric tuple, using the raw perf_result.

        Args:
            perf_result: Dictionary mapping from every group (from get_optimized_event_groups())
                         to its corresponding tuple of counter values (ordered as Events in group).
            metric_events: Tuple of Event objects, exactly as passed in during scheduler construction.

        Returns:
            Tuple of values matching original user tuple order.
            (Where an event is duplicated in the tuple, value is repeated; where absent in perf_result, error is raised.)

        Errors:
            - Raises KeyError if:
                * The user tuple was not registered in scheduler (no permutations; must match exact).
                * The perf_result keys do not exactly match scheduler's required groups.
                * The value tuple length is incorrect for the group.
            - Raises IndexError if the results in perf_result are the wrong length.
            - Raises KeyError for non-existent events/groupings.
        """
        # Enforce strict retrieval: only allow if the provided tuple was part of input (exact order)
        if metric_events not in self._input_tuples_set:
            raise KeyError("Metric tuple not part of scheduler input.")

        # Strict check: perf_result keys must exactly match optimized groups
        expected_keys = set(self.get_optimized_event_groups())
        actual_keys = set(perf_result.keys())
        if expected_keys != actual_keys:
            raise KeyError(
                f"perf_result keys do not match expected optimized groups: expected {expected_keys}, got {actual_keys}"
            )

        # Canonical dedup key for mapping to optimized groups
        lookup_key = tuple(sorted(e.key() for e in metric_events))
        if len(metric_events) == 0:
            # Always allow retrieval of empty tuple as ()
            return ()
        if lookup_key not in self._metric_key_to_id:
            raise KeyError(
                "Metric tuple unknown to this scheduler (not present in optimization map)"
            )
        metric_id = self._metric_key_to_id[lookup_key]
        decode_map = self._decode_map[metric_id]
        result = []
        for e in metric_events:
            ek = e.key()
            dtc, cohort_idx, event_idx = decode_map[ek]
            grp = self._cohort_to_group[(dtc, cohort_idx)]
            result.append(perf_result[grp][event_idx])
        return tuple(result)

    def _build_global_groups(
        self, cmn_info: "CmnInfo"
    ) -> Tuple[List[Tuple[Event, ...]], Dict[_TupleReq, int], List[_TupleReq]]:
        """
        Optimal global group builder (on scheduler instance).
        - Partitions self._global_tuples into small and large.
        - Packs all 'small' using _GGroup logic (cover/extend/new-group, cycle merge).
        - Leaves 'large' as unhandled for stepwise expansion.
        - Fills decode map: self._decode_map_cross
        Returns:
        - groups: list of tuple of Events (optimized, ready for PMU/perf).
        - tuple_to_group_idx: {_TupleReq -> int} (for test/assert/debug).
        - large: [_TupleReq, ...] (needing scattering, not yet supported).
        """
        # Partition tuples
        small = []
        large = []
        for t in self._global_tuples:
            if _is_small_global_tuple(t, cmn_info):
                small.append(t)
            else:
                large.append(t)
        # Pack smalls using GGroupX (cover/extend/new)
        groups: list[_GGroup] = []
        tuple_to_group_idx = {}
        # Clean decode map for each call
        # self._decode_map_cross = {}
        for t in small:
            by_type: dict[int, set[str]] = {}
            cycle_key: Optional[str] = None
            for ev in t.events:
                if ev.is_cycle():
                    cycle_key = ev.key()
                else:
                    by_type.setdefault(cast(CmnEvent, ev).type, set()).add(ev.key())
            # Cover: all keys already present in a group
            found = None
            # Cover: all keys already present in a group
            for idx, grp in enumerate(groups):
                has_all = all(
                    k in grp.events.get(t_, set()) for t_, keys in by_type.items() for k in keys
                )
                if has_all:
                    # Also must attach cycle flag if present in the tuple but missing in group
                    if cycle_key is not None:
                        if grp.cycle_key is None:
                            grp.cycle_key = cycle_key
                        elif grp.cycle_key != cycle_key:
                            raise ValueError("Multiple cycle keys detected in global group")
                    found = idx
                    break
            if found is not None:
                tuple_to_group_idx[t] = found
                # decode map filled after all groups are finalized for predictable event order
                continue
            # Extend: any group that can accept the whole set
            found = None
            for idx, grp in enumerate(groups):
                if grp.can_accept(by_type, cmn_info):
                    grp.add(by_type, cycle_key=cycle_key)
                    tuple_to_group_idx[t] = idx
                    found = idx
                    break
            if found is not None:
                continue
            # Make new
            grp = _GGroup()
            grp.add(by_type, cycle_key=cycle_key)
            groups.append(grp)
            tuple_to_group_idx[t] = len(groups) - 1
        # Compute group event tuples (stable ordering for decode)
        result_tuples = [grp.freeze() for grp in groups]
        # --- Build (simple) decode mapping (tuple_to_group_idx, ready for roundtrip) ---
        for t, idx in tuple_to_group_idx.items():
            group = result_tuples[idx]
            key_map = {e.key(): i for i, e in enumerate(group)}
            val_map = []
            for ev in t.events:
                val_map.append(key_map[ev.key()])
            # Insert directly into the canonical decode map
            if t.id not in self._decode_map:
                self._decode_map[t.id] = {}
            group = result_tuples[idx]
            for pos, ev in enumerate(t.events):
                ev_key = ev.key()
                self._decode_map[t.id][ev_key] = (GLOBAL_DTC_SENTINEL, idx, val_map[pos])

        # ----- Large tuple logic: split into singleton components, reusing exact groups -----
        for t in large:
            cycle_key = next((e.key() for e in t.events if e.is_cycle()), None)
            event_group_idx: Dict[str, int] = {}
            cycle_group_idx: Optional[int] = None
            attach_cycle = True
            for ev in t.events:
                if ev.is_cycle():
                    continue
                desired_cycle_key = cycle_key if attach_cycle else None
                gidx = _find_exact_ggroup(groups, {ev.key()}, desired_cycle_key)
                if gidx is None:
                    grp = _GGroup()
                    grp.add({cast(CmnEvent, ev).type: {ev.key()}}, cycle_key=desired_cycle_key)
                    groups.append(grp)
                    gidx = len(groups) - 1
                event_group_idx[ev.key()] = gidx
                if desired_cycle_key is not None:
                    cycle_group_idx = gidx
                    attach_cycle = False
            if cycle_key is not None and cycle_group_idx is None:
                gidx = _find_exact_ggroup(groups, set(), cycle_key)
                if gidx is None:
                    grp = _GGroup()
                    grp.add({}, cycle_key=cycle_key)
                    groups.append(grp)
                    gidx = len(groups) - 1
                cycle_group_idx = gidx
            result_tuples = [g.freeze() for g in groups]
            if t.id not in self._decode_map:
                self._decode_map[t.id] = {}
            group_key_maps = [
                {e.key(): i for i, e in enumerate(group_tuple)} for group_tuple in result_tuples
            ]
            for ev in t.events:
                if ev.is_cycle():
                    if cycle_group_idx is None:
                        raise ValueError("Missing cycle group for global tuple")
                    gidx = cycle_group_idx
                else:
                    gidx = event_group_idx[ev.key()]
                idx_in_group = group_key_maps[gidx][ev.key()]
                self._decode_map[t.id][ev.key()] = (GLOBAL_DTC_SENTINEL, gidx, idx_in_group)
        # Build result tuple list after all packing
        result_tuples = [grp.freeze() for grp in groups]
        return result_tuples, tuple_to_group_idx, large

    def _build_global_mixed_groups(
        self,
        cmn_info: "CmnInfo",
        group_offset: int,
    ) -> List[Tuple[Event, ...]]:
        """
        Build event groups for global tuples that mix device events and watchpoints.

        Global mixed tuples are validated by global constraints; if a tuple cannot fit, it is
        split into singleton groups (cycle attaches to the first group).
        """
        groups: List[Tuple[Event, ...]] = []
        group_to_idx: Dict[Tuple[Event, ...], int] = {}
        for t in self._global_mixed_tuples:
            noncycle = [e for e in t.events if not e.is_cycle()]
            if not noncycle:
                continue
            for ev in noncycle:
                if violates_global_constraints([ev], cmn_info):
                    raise ValueError(f"Global mixed event {ev.key()} cannot fit on this topology")
            oversized = violates_global_constraints(noncycle, cmn_info)
            cycle_ev = next((e for e in t.events if e.is_cycle()), None)
            event_group_idx: Dict[str, int] = {}
            cycle_group_idx: Optional[int] = None
            group: Tuple[Event, ...]
            if oversized:
                attach_cycle = True
                for ev in noncycle:
                    if attach_cycle and cycle_ev is not None:
                        group = (cycle_ev, ev)
                        attach_cycle = False
                        cycle_group_idx = group_to_idx.get(group)
                    else:
                        group = (ev,)
                    gidx = group_to_idx.get(group)
                    if gidx is None:
                        groups.append(group)
                        gidx = len(groups) - 1
                        group_to_idx[group] = gidx
                    event_group_idx[ev.key()] = gidx
                    if cycle_ev is not None and group[0].is_cycle():
                        cycle_group_idx = gidx
                if cycle_ev is not None and cycle_group_idx is None:
                    group = (cycle_ev,)
                    gidx = group_to_idx.get(group)
                    if gidx is None:
                        groups.append(group)
                        gidx = len(groups) - 1
                        group_to_idx[group] = gidx
                    cycle_group_idx = gidx
            else:
                ordered = tuple(sorted(noncycle, key=lambda e: e.key()))
                if cycle_ev is not None:
                    group = (cycle_ev,) + ordered
                else:
                    group = ordered
                gidx = group_to_idx.get(group)
                if gidx is None:
                    groups.append(group)
                    gidx = len(groups) - 1
                    group_to_idx[group] = gidx
                for ev in noncycle:
                    event_group_idx[ev.key()] = gidx
                if cycle_ev is not None:
                    cycle_group_idx = gidx

            if t.id not in self._decode_map:
                self._decode_map[t.id] = {}
            for ev in t.events:
                if ev.is_cycle():
                    if cycle_group_idx is None:
                        raise ValueError("Missing cycle group for global mixed tuple")
                    gidx = cycle_group_idx
                else:
                    gidx = event_group_idx[ev.key()]
                group = groups[gidx]
                key_map = {group_ev.key(): i for i, group_ev in enumerate(group)}
                self._decode_map[t.id][ev.key()] = (
                    GLOBAL_DTC_SENTINEL,
                    group_offset + gidx,
                    key_map[ev.key()],
                )
        return groups

    def _build_global_watchpoint_groups(
        self,
        cmn_info: "CmnInfo",
        group_offset: int,
    ) -> List[Tuple[Event, ...]]:
        """
        Builds global watchpoint groups using greedy merging and spill behavior.

        Args:
            cmn_info: Hardware topology/constraint info.
            group_offset: Index offset for GLOBAL_DTC_SENTINEL keys.

        Returns:
            List of event tuples representing global watchpoint groups.
        """
        small: List[_TupleReq] = []
        large: List[_TupleReq] = []
        for t in self._global_watchpoint_tuples:
            wp_keys = {ev.key() for ev in t.events if isinstance(ev, CmnWatchpoint)}
            if not wp_keys:
                continue
            for key in wp_keys:
                wp = cast(CmnWatchpoint, event_from_key(key))
                if violates_global_constraints([wp], cmn_info):
                    raise ValueError(f"Global watchpoint {key} cannot fit on this topology")
            events = [cast(CmnWatchpoint, event_from_key(k)) for k in wp_keys]
            if violates_global_constraints(events, cmn_info):
                large.append(t)
            else:
                small.append(t)

        groups: List[_GlobalWatchpointGroup] = []
        tuple_to_group_idx: Dict[_TupleReq, int] = {}

        for t in small:
            wp_keys = {ev.key() for ev in t.events if isinstance(ev, CmnWatchpoint)}
            cycle_key = next((e.key() for e in t.events if e.is_cycle()), None)
            found = None
            for idx, grp in enumerate(groups):
                if wp_keys.issubset(grp.keys):
                    if cycle_key is not None:
                        grp.add(set(), cycle_key=cycle_key)
                    found = idx
                    break
            if found is not None:
                tuple_to_group_idx[t] = found
                continue
            found = None
            for idx, grp in enumerate(groups):
                if grp.can_accept(wp_keys, cmn_info):
                    grp.add(wp_keys, cycle_key=cycle_key)
                    tuple_to_group_idx[t] = idx
                    found = idx
                    break
            if found is not None:
                continue
            grp = _GlobalWatchpointGroup()
            grp.add(wp_keys, cycle_key=cycle_key)
            groups.append(grp)
            tuple_to_group_idx[t] = len(groups) - 1

        result_tuples = [grp.freeze() for grp in groups]
        for t, idx in tuple_to_group_idx.items():
            group = result_tuples[idx]
            key_map = {e.key(): i for i, e in enumerate(group)}
            if t.id not in self._decode_map:
                self._decode_map[t.id] = {}
            for ev in t.events:
                self._decode_map[t.id][ev.key()] = (
                    GLOBAL_DTC_SENTINEL,
                    group_offset + idx,
                    key_map[ev.key()],
                )

        for t in large:
            cycle_key = next((e.key() for e in t.events if e.is_cycle()), None)
            event_group_idx: Dict[str, int] = {}
            cycle_group_idx: Optional[int] = None
            attach_cycle = True
            for ev in t.events:
                if ev.is_cycle():
                    continue
                desired_cycle_key = cycle_key if attach_cycle else None
                gidx = _find_exact_global_watchpoint_group(groups, {ev.key()}, desired_cycle_key)
                if gidx is None:
                    grp = _GlobalWatchpointGroup()
                    grp.add({ev.key()}, cycle_key=desired_cycle_key)
                    groups.append(grp)
                    gidx = len(groups) - 1
                event_group_idx[ev.key()] = gidx
                if desired_cycle_key is not None:
                    cycle_group_idx = gidx
                    attach_cycle = False
            if cycle_key is not None and cycle_group_idx is None:
                gidx = _find_exact_global_watchpoint_group(groups, set(), cycle_key)
                if gidx is None:
                    grp = _GlobalWatchpointGroup()
                    grp.add(set(), cycle_key=cycle_key)
                    groups.append(grp)
                    gidx = len(groups) - 1
                cycle_group_idx = gidx

            result_tuples = [g.freeze() for g in groups]
            if t.id not in self._decode_map:
                self._decode_map[t.id] = {}
            group_key_maps = [
                {e.key(): i for i, e in enumerate(group_tuple)} for group_tuple in result_tuples
            ]
            for ev in t.events:
                if ev.is_cycle():
                    if cycle_group_idx is None:
                        raise ValueError("Missing cycle group for global watchpoint tuple")
                    gidx = cycle_group_idx
                else:
                    gidx = event_group_idx[ev.key()]
                idx_in_group = group_key_maps[gidx][ev.key()]
                self._decode_map[t.id][ev.key()] = (
                    GLOBAL_DTC_SENTINEL,
                    group_offset + gidx,
                    idx_in_group,
                )

        return [grp.freeze() for grp in groups]
