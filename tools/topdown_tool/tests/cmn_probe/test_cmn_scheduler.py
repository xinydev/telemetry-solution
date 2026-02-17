# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

"""
Pytest suite for cmn_scheduler.CmnScheduler
===========================================

This module exhaustively tests the contract, error handling, and invariants of the
CMN Scheduler event optimizer.

What is covered here:
---------------------
- All deduplication rules (tuples, intra-tuple event duplication)
- Enforcement of packing/partitioning based on hardware constraints (max MAX_EVENTS_PER_NODE/node, max MAX_EVENTS_PER_XP/xp, MAX_EVENTS_PER_DTC/group)
- Strict input contract: only user-registered metric tuples (order-preserving, no permutations) can be retrieved
- Various edge/error/boundary conditions (cycle, empty tuple, non-int node ids, mixed nodes, etc)
- Error case coverage for perf_result mismatches (missing/extra group, order, length)

Every property listed in the main scheduler's docstring is directly asserted here:
    - Packing output is minimal, stable, and hardware-legal
    - Retrieval is order- and tuple-exact (strict, never inferred)
    - All error paths raise directly and informatively

This file acts as "literate contract": maintainers and users can trust every explicitly-documented guarantee.

Run with:  pytest tests -q

"""

from __future__ import annotations

import random
from typing import Tuple, Sequence

import pytest

from topdown_tool.cmn_probe.common import Event as CmnEvent, Watchpoint
from topdown_tool.cmn_probe.scheduler import (
    CmnInfo,
    CmnScheduler,
    Event,
    NodeEntry,
    WatchpointPort,
    _event_sort_key,
    _TupleReq,
    event_from_key,
    MAX_EVENTS_PER_XP,
    MAX_EVENTS_PER_DTC,
    MAX_EVENTS_PER_NODE,
    MAX_WATCHPOINTS_PER_DIRECTION,
)  # noqa: E402  pylint: disable=wrong-import-position
from .helpers import (
    cycle_ev,
    glob_ev,
    node,
    ev,
    tup_val,
    build_perf_result,
    make_cmn_info,
    wp,
)


def flatten(groups: Sequence[Tuple[Event, ...]]) -> set[str]:
    """Return a set of all `.key()` strings present in *groups*."""
    return {e.key() for g in groups for e in g}


def global_wp(
    device: str,
    *,
    direction: str = "UP",
    value: int = 1,
    mask: int = 0xFF,
    chn_sel: int = 0,
    grp: int = 0,
    cmn_index: int = 0,
) -> Watchpoint:
    dir_map = {"UP": 0, "DOWN": 2}
    return Watchpoint(
        name="wp",
        title="",
        description="",
        cmn_index=cmn_index,
        mesh_flit_dir=dir_map[direction],
        wp_chn_sel=chn_sel,
        wp_grp=grp,
        wp_mask=mask,
        wp_val=value,
        xp_id=None,
        port=None,
        device=device,
    )


def test_cmninfo_type_helpers():
    """
    Verify CmnInfo helpers nodes_of_type, unique_xps_of_type, unique_dtcs_of_type with a synthetic topology.
    """
    # Create nodes of two types (t0: type=0, t1: type=1) in a multi-dtc/xp scenario
    # Topology: (dtc, xp, node, type, port)
    nodes = [
        NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
        NodeEntry(dtc=0, xp=0, node=1, node_type=1, port=1),
        NodeEntry(dtc=1, xp=1, node=2, node_type=0, port=2),
        NodeEntry(dtc=1, xp=1, node=3, node_type=1, port=3),
        NodeEntry(dtc=0, xp=2, node=4, node_type=1, port=4),
        NodeEntry(dtc=1, xp=3, node=5, node_type=1, port=5),
        NodeEntry(dtc=0, xp=4, node=6, node_type=0, port=6),
    ]
    cmn = CmnInfo(dtc_count=2, dtc_of=lambda xp: xp % 2, nodes=nodes)
    # For type=0, should get dtcs: {0,1}; xps: (0,1,4); nodes: [(0,0,0),(1,1,2),(0,4,6)]
    assert set(cmn.unique_dtcs_of_type(0)) == {0, 1}
    assert set(cmn.unique_xps_of_type(0)) == {(0, 0), (1, 1), (0, 4)}
    assert set(cmn.nodes_of_type(0)) == {(0, 0, 0), (1, 1, 2), (0, 4, 6)}
    # For type=1, dtcs: {0,1}; xps: (0,0), (0,2), (1,1), (1,3); nodes: all with type==1
    assert set(cmn.unique_dtcs_of_type(1)) == {0, 1}
    assert set(cmn.unique_xps_of_type(1)) == {(0, 0), (0, 2), (1, 1), (1, 3)}
    assert set(cmn.nodes_of_type(1)) == {(0, 0, 1), (1, 1, 3), (0, 2, 4), (1, 3, 5)}


def test_cmninfo_requires_port() -> None:
    with pytest.raises(ValueError, match="port"):
        CmnInfo(
            dtc_count=1,
            dtc_of=lambda xp: 0,
            nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=None)],
        )
    with pytest.raises(ValueError, match="NodeEntry"):
        CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=[(0, 0, 0, 0, 0)])


def test_cmninfo_watchpoint_ports_for_device() -> None:
    ports = [WatchpointPort(xp_id=0, port=1), WatchpointPort(xp_id=1, port=2)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    assert ci.watchpoint_ports_for_device("DEV") == ports
    with pytest.raises(ValueError, match="Unknown watchpoint device"):
        ci.watchpoint_ports_for_device("MISSING")
    ci_no = CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=[])
    with pytest.raises(ValueError, match="Unknown watchpoint device"):
        ci_no.watchpoint_ports_for_device("DEV")


def test_xp_type_node_counts():
    """
    Test CmnInfo.xp_type_node_counts for simple node arrangements.
    """
    nodes = [
        NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
        NodeEntry(dtc=0, xp=0, node=1, node_type=0, port=1),
        NodeEntry(dtc=0, xp=1, node=2, node_type=1, port=2),
    ]
    ci = CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=nodes)
    assert ci.xp_type_node_counts() == {0: {0: 2}, 1: {1: 1}}


def test_global_event_key_roundtrip():
    """
    Event.key and Event.from_key roundtrip for global events.
    """
    # Regular global event: type 7, event_id 42
    e = glob_ev("E42", event_type=7)
    key = e.key()
    assert key == "7:42@I0"

    e2 = event_from_key(key)
    assert isinstance(e2, CmnEvent)
    assert e2.type == 7
    assert e2.eventid == 42
    assert e2.xp_id is None
    assert e2.nodeid is None
    assert not e2.is_cycle()
    assert e2.is_global()
    # Round trip must preserve
    assert e2.key() == key
    # Not confuse with local/cycle
    assert not ev("E1", 0, 0).is_global()
    assert not cycle_ev().is_global()


# ---------------------------------------------------------------------------#
# Global scheduler tests (global event support)
# ---------------------------------------------------------------------------#


def test_global_tuple_simple():
    # 1 event_type, multiple events, fits (no oversize)
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[node(dtc=0, xp=0, port=0), node(dtc=0, xp=1, port=1), node(dtc=0, xp=2, port=2)],
    )
    # 3 global events, type 0
    t = (glob_ev("E1"), glob_ev("E2"), glob_ev("E3"))
    sched = CmnScheduler([t], ci)
    # Exactly one group, exactly as input
    result = sched.get_optimized_event_groups()
    assert result == [t]
    # Retrieval roundtrip works
    values = (11.1, 22.2, 33.3)
    perf = {result[0]: values}
    out = sched.retrieve_metric_result(perf, t)
    assert out == values


def test_global_tuple_multi_dtc_stays_single():
    # Should not fragment across DTCs, still one group
    ci = make_cmn_info(
        dtc_count=2,
        nodes=[
            node(dtc=0, xp=0, type=2, port=0),
            node(dtc=1, xp=1, type=2, port=1),
            node(dtc=1, xp=2, type=2, port=2),
        ],
    )
    t = (glob_ev("E1", event_type=2), glob_ev("E2", event_type=2))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert set(groups[0]) == set(t)
    # Retrieval works
    v = (1.0, 2.0)
    perf = {groups[0]: v}
    assert sched.retrieve_metric_result(perf, t) == v


def test_global_tuple_node_capacity_split():
    # Tuple with >MAX_EVENTS_PER_NODE global events for a single-node xp: should split into singletons

    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, node=0, type=0, port=0)])
    t = tuple(glob_ev(f"E{i + 1}") for i in range(MAX_EVENTS_PER_NODE + 1))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    # Should yield MAX_EVENTS_PER_NODE + 1 singleton groups, one per event
    expect = tuple((glob_ev(f"E{i + 1}"),) for i in range(MAX_EVENTS_PER_NODE + 1))
    assert groups == list(expect)


def test_global_tuple_crosspoint_capacity_split_1():
    # Tuple whose |nodes_on_xp|*len(tuple) > MAX_EVENTS_PER_XP for a xp: should split
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=0, type=0, port=0),
            node(dtc=0, xp=0, node=2, type=0, port=2),
        ],
    )
    t = tuple(glob_ev(f"E{i + 1}") for i in range((MAX_EVENTS_PER_XP // 2) + 1))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    expect = list((glob_ev(f"E{i + 1}"),) for i in range((MAX_EVENTS_PER_XP // 2) + 1))
    assert groups == expect


def test_global_tuple_crosspoint_capacity_split_2():
    # As above, but node count even higher (3*2 > MAX_EVENTS_PER_XP)
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=0, type=0, port=0),
            node(dtc=0, xp=0, node=2, type=0, port=2),
            node(dtc=0, xp=0, node=4, type=0, port=4),
        ],
    )
    # Two events; but with 3 nodes, node_cnt[xp,type]=3, so 3*2=6 > MAX_EVENTS_PER_XP(=4)
    t = (glob_ev("E1"), glob_ev("E2"))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    expect = ((glob_ev("E1"),), (glob_ev("E2"),))
    assert groups == list(expect)


def test_global_tuple_crosspoint_capacity_split_with_linux_type_alias():
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=0, type=0x000A, port=0),
            node(dtc=0, xp=0, node=1, type=0x000D, port=1),
        ],
        global_type_aliases=dict(CmnEvent.LINUX_FIX_MAP),
    )
    t = tuple(glob_ev(f"E{i + 1}", event_type=0x000A) for i in range(3))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    expect = [(glob_ev(f"E{i + 1}", event_type=0x000A),) for i in range(3)]
    assert groups == expect


def test_global_excess_nodes_on_crosspoint_error():
    # More than MAX_EVENTS_PER_XP nodes of the same type on a crosspoint should error for global events
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=n, type=0, port=n)
            for n in range(0, 2 * (MAX_EVENTS_PER_XP + 1), 2)
        ],
    )
    t = (glob_ev("E1"),)
    with pytest.raises(ValueError):
        CmnScheduler([t], ci)


def test_global_ordering_and_retrieval_mapping():
    # Input event order is scrambled; output group should be sorted deterministically (by key)
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, port=0)])
    t = (glob_ev("E1"), glob_ev("E3"), glob_ev("E2"))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    # Should always be sorted: E1, E2, E3
    expect = (glob_ev("E1"), glob_ev("E2"), glob_ev("E3"))
    assert groups == [expect]
    # Retrieval mapping preserves correspondence for user-supplied order
    perf_result = {expect: (1.0, 2.0, 3.0)}
    # Input order: E1, E3, E2 => output: 1.0, 3.0, 2.0
    assert sched.retrieve_metric_result(perf_result, t) == (1.0, 3.0, 2.0)


def test_global_ordering_and_retrieval_multi_type():
    # Two nodes, different types (event_type 0 and 1), two metrics
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=0, type=0, port=0),
            node(dtc=0, xp=0, node=1, type=1, port=1),
        ],
    )
    # Each tuple targets only one type, but the scheduler may merge into a single group if constraints allow.
    tA = (glob_ev("E2", event_type=0), glob_ev("E1", event_type=0))
    tB = (glob_ev("E4", event_type=1), glob_ev("E3", event_type=1))
    sched = CmnScheduler([tA, tB], ci)
    groups = sched.get_optimized_event_groups()
    # Should have a single merged group, sorted by event key
    expect = (
        glob_ev("E1", event_type=0),
        glob_ev("E2", event_type=0),
        glob_ev("E3", event_type=1),
        glob_ev("E4", event_type=1),
    )
    assert groups == [expect]
    # Retrieval preserves correspondence for scrambled order in tA and tB
    perf_result = {expect: (10, 20, 30, 40)}
    # tA is E2, E1, so should yield (20, 10)
    assert sched.retrieve_metric_result(perf_result, tA) == (20, 10)
    # tB is E4, E3, so should yield (40, 30)
    assert sched.retrieve_metric_result(perf_result, tB) == (40, 30)


def test_global_duplicate_event_elimination_and_retrieval():
    # Duplicates in global tuple are deduped in optimized group, retrieval maps input order to proper values
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, node=0, type=0, port=0)])
    t = (glob_ev("E1", event_type=0), glob_ev("E2", event_type=0), glob_ev("E1", event_type=0))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    expect_group = (glob_ev("E1", event_type=0), glob_ev("E2", event_type=0))
    assert groups == [expect_group]
    # Retrieval should include value for duplicate
    perf_result = {expect_group: (10.0, 20.0)}
    assert sched.retrieve_metric_result(perf_result, t) == (10.0, 20.0, 10.0)


def test_global_cycle_tuple_group_and_retrieval():
    # A tuple of global events including a cycle
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, node=0, type=0, port=0)])
    t = (glob_ev("E2", event_type=0), glob_ev("E1", event_type=0), cycle_ev())
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    expect_group = (cycle_ev(), glob_ev("E1", event_type=0), glob_ev("E2", event_type=0))
    assert groups == [expect_group]
    # Retrieval mapping order: cycle, E2, E1 => values in (cycle, E2, E1) order in input
    perf_result = {expect_group: (100.0, 1.0, 2.0)}
    assert sched.retrieve_metric_result(perf_result, t) == (2.0, 1.0, 100.0)


def test_global_local_mixing_error():
    # Must not permit global/local mixing in a tuple
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, port=0)])
    t_bad = (glob_ev("E1"), ev("E2", 0, 0))
    with pytest.raises(ValueError):
        CmnScheduler([t_bad], ci)


def test_retrieval_with_global_events():
    # Multiple independent global tuples allowed, order maintained
    ci = make_cmn_info(
        dtc_count=1,
        nodes=[node(dtc=0, xp=0, port=0), node(dtc=0, xp=0, node=4, port=4)],
    )
    t1 = (glob_ev("E1"), glob_ev("E2"))
    t2 = (glob_ev("E3"),)
    sched = CmnScheduler([t1, t2], ci)
    grps = sched.get_optimized_event_groups()
    assert grps[0] == t1
    assert grps[1] == t2
    perf = {grps[0]: (1.2, 2.3), grps[1]: (5.5,)}
    # Retrieval must be exact order
    assert sched.retrieve_metric_result(perf, t1) == (1.2, 2.3)
    assert sched.retrieve_metric_result(perf, t2) == (5.5,)


def test_global_large_tuple_singletons_retrieval():
    """
    A global tuple with 5 distinct events exceeds MAX_EVENTS_PER_NODE and is split into singleton groups.
    Assign distinct perf values per event and ensure retrieval returns them in input order.
    """
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, node=0, type=0, port=0)])
    t = (glob_ev("E1"), glob_ev("E2"), glob_ev("E3"), glob_ev("E4"), glob_ev("E5"))
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()

    # Expect singleton groups for each event
    assert len(groups) == 5
    assert all(len(g) == 1 for g in groups)

    # Build perf_result with unique values per event (10.0, 20.0, ..., 50.0)
    perf = {}
    for g in groups:
        e = g[0]
        val = float(int(e.eventid) * 10)
        perf[g] = (val,)

    out = sched.retrieve_metric_result(perf, t)
    expected = tuple(float(int(e.eventid) * 10) for e in t)
    assert out == expected


def test_global_large_tuple_with_cycle_retrieval():
    """
    Same as above but include a cycle event in the tuple. The cycle is attached to the first created group
    and must be correctly indexed for retrieval.
    """
    ci = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, node=0, type=0, port=0)])
    t = (glob_ev("E1"), glob_ev("E2"), glob_ev("E3"), glob_ev("E4"), glob_ev("E5"), cycle_ev())
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()

    # One group contains (cycle, E1) and the others are singletons for E2..E5
    assert any(len(g) == 2 and g[0].is_cycle() for g in groups)
    assert sum(1 for g in groups if len(g) == 1) == 4

    # Assign values: cycle=999.0, each event gets 10x its numeric id
    perf = {}
    for g in groups:
        if len(g) == 2:
            cyc, e = g
            assert cyc.is_cycle()
            perf[g] = (999.0, float(int(e.eventid) * 10))
        else:
            e = g[0]
            perf[g] = (float(int(e.eventid) * 10),)

    out = sched.retrieve_metric_result(perf, t)
    expected = (10.0, 20.0, 30.0, 40.0, 50.0, 999.0)
    assert out == expected


# ---------------------------------------------------------------------------#
# Watchpoint ingestion/validation tests
# ---------------------------------------------------------------------------#


def test_watchpoint_tuple_legal_single_xp():
    """A tuple with only Watchpoints and the same xp_id is accepted."""
    t = (wp(xp_id=1, port=2, direction="UP"), wp(xp_id=1, port=3, direction="DOWN"))
    sched = CmnScheduler([t], make_cmn_info())
    # Should not raise and should preserve group
    assert sched.get_optimized_event_groups()[0] == t


def test_mixed_tuple_local_allowed() -> None:
    nodes = [node(dtc=0, xp=1, node=10, type=0, port=1), node(dtc=0, xp=1, node=11, type=0, port=1)]
    cmn_info = make_cmn_info(dtc_count=1, nodes=nodes)
    t = (ev("E1", 1, 10), ev("E2", 1, 11), wp(xp_id=1, port=1, direction="UP"))
    sched = CmnScheduler([t], cmn_info)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert set(groups[0]) == set(t)
    values = {ev.key(): float(idx + 1) for idx, ev in enumerate(groups[0])}
    perf_result = {groups[0]: tuple(values[e.key()] for e in groups[0])}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(values[e.key()] for e in t)


def test_mixed_tuple_local_split_by_event_id() -> None:
    nodes = [
        node(dtc=0, xp=10, node=102, type=10, port=1),
        node(dtc=0, xp=10, node=103, type=10, port=1),
    ]
    cmn_info = make_cmn_info(dtc_count=1, nodes=nodes)
    ev15_0 = ev("E15", 10, 102, event_type=10)
    ev15_1 = ev("E15", 10, 103, event_type=10)
    ev16_0 = ev("E16", 10, 102, event_type=10)
    ev16_1 = ev("E16", 10, 103, event_type=10)
    watch = wp(xp_id=10, port=1, direction="DOWN")
    t = (ev15_0, ev15_1, ev16_0, ev16_1, watch)
    sched = CmnScheduler([t], cmn_info)
    groups = sched.get_optimized_event_groups()
    group_sets = {frozenset(e.key() for e in g) for g in groups}
    expected = {
        frozenset({ev15_0.key(), ev15_1.key()}),
        frozenset({ev16_0.key(), ev16_1.key()}),
        frozenset({watch.key()}),
    }
    assert group_sets == expected
    values = {
        ev15_0.key(): 1.0,
        ev15_1.key(): 2.0,
        ev16_0.key(): 3.0,
        ev16_1.key(): 4.0,
        watch.key(): 5.0,
    }
    perf_result = {g: tuple(values[e.key()] for e in g) for g in groups}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(values[e.key()] for e in t)


def test_mixed_tuple_local_global_rejected() -> None:
    cmn_info = make_cmn_info(dtc_count=1, nodes=[node(dtc=0, xp=0, port=0)])
    t = (ev("E1", 0, 0), global_wp("DEV"))
    with pytest.raises(ValueError, match="Cannot mix local and global events"):
        CmnScheduler([t], cmn_info)


def test_watchpoint_tuple_rejects_multiple_xp():
    """Tuple of Watchpoints with different xp_id is error."""
    t = (wp(xp_id=1, port=2), wp(xp_id=2, port=3))
    import pytest

    with pytest.raises(ValueError, match="same xp_id"):
        CmnScheduler([t], make_cmn_info())


def test_global_watchpoint_tuple_requires_single_device() -> None:
    ports = [WatchpointPort(xp_id=0, port=1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"A": ports, "B": ports},
    )
    t = (global_wp("A"), global_wp("B"))
    with pytest.raises(ValueError, match="same device"):
        CmnScheduler([t], ci)


def test_global_watchpoint_missing_mapping_raises() -> None:
    ci = CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=[], watchpoint_ports_by_device={})
    t = (global_wp("DEV"),)
    with pytest.raises(ValueError, match="Unknown watchpoint device"):
        CmnScheduler([t], ci)


def test_global_watchpoint_unschedulable_single_raises() -> None:
    ports = [
        WatchpointPort(xp_id=0, port=1),
        WatchpointPort(xp_id=0, port=2),
        WatchpointPort(xp_id=0, port=3),
    ]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    t = (global_wp("DEV", direction="UP"),)
    with pytest.raises(ValueError, match="cannot fit"):
        CmnScheduler([t], ci)


def test_global_watchpoint_unschedulable_single_raises_per_direction() -> None:
    ports = [
        WatchpointPort(xp_id=0, port=1),
        WatchpointPort(xp_id=0, port=2),
        WatchpointPort(xp_id=0, port=3),
    ]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    t = (global_wp("DEV", direction="DOWN"),)
    with pytest.raises(ValueError, match="cannot fit"):
        CmnScheduler([t], ci)


def test_global_watchpoint_tuple_spills_when_over_capacity() -> None:
    ports = [WatchpointPort(xp_id=0, port=1), WatchpointPort(xp_id=0, port=2)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp0 = global_wp("DEV", direction="UP", value=1)
    wp1 = global_wp("DEV", direction="UP", value=2)
    t = (wp0, wp1)
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 2
    assert {g[0].key() for g in groups} == {wp0.key(), wp1.key()}
    perf_result = {g: (10.0,) if g[0] == wp0 else (20.0,) for g in groups}
    assert sched.retrieve_metric_result(perf_result, t) == (10.0, 20.0)


def test_global_watchpoint_dtc_accounting_allows_many_ports() -> None:
    ports = [WatchpointPort(xp_id=i, port=1) for i in range(MAX_EVENTS_PER_DTC + 1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp_global = global_wp("DEV", direction="UP", value=1)
    sched = CmnScheduler([(wp_global,)], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert groups[0] == (wp_global,)
    perf_result = {groups[0]: (10.0,)}
    assert sched.retrieve_metric_result(perf_result, (wp_global,)) == (10.0,)


def test_global_watchpoint_duplicate_ports_in_mapping_are_ignored() -> None:
    ports = [
        WatchpointPort(xp_id=0, port=0),
        WatchpointPort(xp_id=0, port=0),
        WatchpointPort(xp_id=0, port=0),
        WatchpointPort(xp_id=1, port=0),
        WatchpointPort(xp_id=1, port=0),
    ]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp_global = global_wp("DEV", direction="UP", value=1)

    sched = CmnScheduler([(wp_global,)], ci)
    groups = sched.get_optimized_event_groups()

    assert len(groups) == 1
    assert groups[0] == (wp_global,)
    perf_result = {groups[0]: (10.0,)}
    assert sched.retrieve_metric_result(perf_result, (wp_global,)) == (10.0,)


def test_global_watchpoint_merge_retrieval() -> None:
    ports = [WatchpointPort(xp_id=0, port=1), WatchpointPort(xp_id=1, port=2)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp0 = global_wp("DEV", direction="UP", value=1)
    wp1 = global_wp("DEV", direction="UP", value=2)
    metrics = [(wp0,), (wp1,)]
    sched = CmnScheduler(metrics, ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    group = groups[0]
    assert set(group) == {wp0, wp1}
    values = {wp0.key(): 10.0, wp1.key(): 20.0}
    perf_result = {group: tuple(values[e.key()] for e in group)}
    assert sched.retrieve_metric_result(perf_result, (wp0,)) == (10.0,)
    assert sched.retrieve_metric_result(perf_result, (wp1,)) == (20.0,)


def test_global_watchpoint_paths_retrieval() -> None:
    ports_merge = [WatchpointPort(xp_id=0, port=1), WatchpointPort(xp_id=1, port=2)]
    ports_spill = [WatchpointPort(xp_id=0, port=3), WatchpointPort(xp_id=0, port=4)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"MERGE": ports_merge, "SPILL": ports_spill},
    )
    wp_local = wp(xp_id=2, port=0, direction="UP")
    wp_merge0 = global_wp("MERGE", direction="UP", value=1)
    wp_merge1 = global_wp("MERGE", direction="UP", value=2)
    wp_spill0 = global_wp("SPILL", direction="UP", value=3)
    wp_spill1 = global_wp("SPILL", direction="UP", value=4)
    metrics = [(wp_local,), (wp_merge0,), (wp_merge1,), (wp_spill0, wp_spill1)]

    sched = CmnScheduler(metrics, ci)
    groups = sched.get_optimized_event_groups()
    assert any(set(g) == {wp_merge0, wp_merge1} for g in groups)
    assert any(g == (wp_spill0,) for g in groups)
    assert any(g == (wp_spill1,) for g in groups)
    assert any(wp_local in g for g in groups)

    value_by_key = {
        wp_local.key(): 1.0,
        wp_merge0.key(): 2.0,
        wp_merge1.key(): 3.0,
        wp_spill0.key(): 4.0,
        wp_spill1.key(): 5.0,
    }
    perf_result = {
        g: tuple(value_by_key[e.key()] for e in g)
        for g in groups
    }
    assert sched.retrieve_metric_result(perf_result, (wp_local,)) == (1.0,)
    assert sched.retrieve_metric_result(perf_result, (wp_merge0,)) == (2.0,)
    assert sched.retrieve_metric_result(perf_result, (wp_merge1,)) == (3.0,)
    assert sched.retrieve_metric_result(perf_result, (wp_spill0, wp_spill1)) == (4.0, 5.0)


def test_global_mixed_tuple_splits_when_over_capacity() -> None:
    nodes = [node(dtc=0, xp=0, node=n, type=1, port=0) for n in range(MAX_EVENTS_PER_XP)]
    ports = [WatchpointPort(xp_id=0, port=1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=nodes,
        watchpoint_ports_by_device={"DEV": ports},
    )
    ev_global = glob_ev("E1", event_type=1)
    wp_global = global_wp("DEV", direction="UP", value=1)
    t = (ev_global, wp_global)
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    group_sets = {frozenset(e.key() for e in g) for g in groups}
    assert group_sets == {frozenset({ev_global.key()}), frozenset({wp_global.key()})}
    values = {ev_global.key(): 10.0, wp_global.key(): 20.0}
    perf_result = {g: tuple(values[e.key()] for e in g) for g in groups}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(values[e.key()] for e in t)


def test_global_mixed_single_event_rejects_when_xp_overflow() -> None:
    nodes = [node(dtc=0, xp=0, node=n, type=1, port=0) for n in range(MAX_EVENTS_PER_XP + 1)]
    ports = [WatchpointPort(xp_id=0, port=1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=nodes,
        watchpoint_ports_by_device={"DEV": ports},
    )
    ev_global = glob_ev("E1", event_type=1)
    wp_global = global_wp("DEV", direction="UP", value=1)
    t = (ev_global, wp_global)
    with pytest.raises(ValueError, match="cannot fit"):
        CmnScheduler([t], ci)


def test_global_mixed_tuple_splits_when_dtc_over_capacity() -> None:
    type_base = 10
    n_types = MAX_EVENTS_PER_DTC
    nodes = [node(dtc=0, xp=xp, node=0, type=type_base + xp, port=0) for xp in range(n_types)]
    ports = [WatchpointPort(xp_id=0, port=1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=nodes,
        watchpoint_ports_by_device={"DEV": ports},
    )
    dev_events = [glob_ev(f"E{idx}", event_type=type_base + idx) for idx in range(n_types)]
    wp_global = global_wp("DEV", direction="UP", value=1)
    t = tuple(dev_events + [wp_global])
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == len(t)
    assert {g[0].key() for g in groups} == {e.key() for e in t}
    value_by_key = {e.key(): float(i) for i, e in enumerate(t, start=1)}
    perf_result = {g: (value_by_key[g[0].key()],) for g in groups}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(value_by_key[e.key()] for e in t)


def test_global_mixed_tuple_fits_without_split() -> None:
    nodes = [node(dtc=0, xp=0, node=0, type=2, port=0)]
    ports = [WatchpointPort(xp_id=0, port=1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=nodes,
        watchpoint_ports_by_device={"DEV": ports},
    )
    ev_global = glob_ev("E1", event_type=2)
    wp_global = global_wp("DEV", direction="DOWN", value=9)
    t = (ev_global, wp_global)
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert set(groups[0]) == {ev_global, wp_global}
    values = {ev_global.key(): 1.0, wp_global.key(): 2.0}
    perf_result = {groups[0]: tuple(values[e.key()] for e in groups[0])}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(values[e.key()] for e in t)


def test_global_mixed_dtc_accounting_allows_many_nodes() -> None:
    nodes = [node(dtc=0, xp=i, node=0, type=1, port=0) for i in range(MAX_EVENTS_PER_DTC + 1)]
    ports = [WatchpointPort(xp_id=i, port=1) for i in range(MAX_EVENTS_PER_DTC + 1)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=nodes,
        watchpoint_ports_by_device={"DEV": ports},
    )
    ev_global = glob_ev("E1", event_type=1)
    wp_global = global_wp("DEV", direction="UP", value=1)
    t = (ev_global, wp_global)
    sched = CmnScheduler([t], ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert set(groups[0]) == {ev_global, wp_global}
    values = {ev_global.key(): 1.0, wp_global.key(): 2.0}
    perf_result = {groups[0]: tuple(values[e.key()] for e in groups[0])}
    assert sched.retrieve_metric_result(perf_result, t) == tuple(values[e.key()] for e in t)


def test_global_and_local_watchpoints_mixed_groups() -> None:
    ports = [WatchpointPort(xp_id=0, port=1), WatchpointPort(xp_id=0, port=2)]
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp_g0 = global_wp("DEV", direction="UP", value=1)
    wp_g1 = global_wp("DEV", direction="UP", value=2)
    wp_local = wp(xp_id=1, port=3, direction="DOWN")
    metrics = [(wp_g0,), (wp_g1,), (wp_local,)]
    sched = CmnScheduler(metrics, ci)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 3
    assert any(wp_local in g for g in groups)
    perf_result = {
        g: (1.0,) if wp_g0 in g else (2.0,) if wp_g1 in g else (3.0,)
        for g in groups
    }
    assert sched.retrieve_metric_result(perf_result, (wp_g0,)) == (1.0,)
    assert sched.retrieve_metric_result(perf_result, (wp_g1,)) == (2.0,)
    assert sched.retrieve_metric_result(perf_result, (wp_local,)) == (3.0,)


# WP/DEV group packing and retrieval tests                                    #
# ---------------------------------------------------------------------------#


def test_wp_and_dev_merge_same_xp_when_fit():
    """
    Device events and watchpoints for the same XP can be merged IF the total
    does not exceed the XP hardware cap (4), and per-direction WP constraint (2).
    """
    xp = 0
    dev_tuple = (ev("E1", xp, xp), ev("E2", xp, xp))
    wp_tuple = (wp(xp_id=xp, port=1, direction="UP"), wp(xp_id=xp, port=2, direction="UP"))
    metrics = [dev_tuple, wp_tuple]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # With only 4 events (2 dev + 2 wp), all can fit in one group.
    all_ev = set(dev_tuple + wp_tuple)
    assert any(all_ev == set(g) for g in groups)
    perf_result = {g: tup_val(g) for g in groups}
    for tup in metrics:
        assert sched.retrieve_metric_result(perf_result, tup) == tup_val(tup)


def test_mixed_tuple_groups_optimized_together() -> None:
    nodes = [node(dtc=0, xp=0, node=0, type=0, port=0)]
    cmn_info = make_cmn_info(dtc_count=1, nodes=nodes)
    e1 = ev("E1", 0, 0)
    e2 = ev("E2", 0, 0)
    wp1 = wp(xp_id=0, port=1, direction="UP")
    wp2 = wp(xp_id=0, port=2, direction="DOWN")
    metrics = [(e1, wp1), (e1, wp2), (e1, e2)]
    sched = CmnScheduler(metrics, cmn_info)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert set(groups[0]) == {e1, e2, wp1, wp2}
    values = {e1.key(): 1.0, e2.key(): 2.0, wp1.key(): 3.0, wp2.key(): 4.0}
    perf_result = {groups[0]: tuple(values[e.key()] for e in groups[0])}
    assert sched.retrieve_metric_result(perf_result, (e1, wp1)) == (1.0, 3.0)
    assert sched.retrieve_metric_result(perf_result, (e1, wp2)) == (1.0, 4.0)
    assert sched.retrieve_metric_result(perf_result, (e1, e2)) == (1.0, 2.0)


def test_wp_and_dev_spill_when_too_large():
    """
    Oversize both device and WP sets (same XP): each triggers spilling/splitting per hardware constraints.
    """
    xp = 0
    # 5 device events - violates MAX_EVENTS_PER_NODE (4)
    dev_tuple = tuple(ev(f"E{i}", xp, xp) for i in range(0, MAX_EVENTS_PER_NODE + 1))
    # 3 UP watchpoints - violates per-direction cap (2)
    wp_tuple = tuple(
        wp(xp_id=xp, port=i, direction="UP") for i in range(MAX_WATCHPOINTS_PER_DIRECTION + 2)
    )
    sched = CmnScheduler([dev_tuple, wp_tuple], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Should be all singleton groups (since all must spill)
    assert len(groups) == len(dev_tuple) + len(wp_tuple)
    assert all(len(g) == 1 for g in groups)
    # Ensure all events are present in some group
    got_keys = {e.key() for g in groups for e in g}
    want_keys = {e.key() for e in dev_tuple + wp_tuple}
    assert got_keys == want_keys
    # Retrieval: order matches
    perf_result = {g: tup_val(g) for g in groups}
    assert sched.retrieve_metric_result(perf_result, dev_tuple) == tup_val(dev_tuple)
    assert sched.retrieve_metric_result(perf_result, wp_tuple) == tup_val(wp_tuple)


def test_wp_and_dev_on_different_xps_packed_merging_possible():
    """
    Device and WP tuples for different XPs may be merged if doing so does not violate any constraint.
    """
    dev = (ev("E1", 0, 0), ev("E2", 0, 0))
    wps = (wp(xp_id=1, port=0, direction="UP"), wp(xp_id=1, port=1, direction="UP"))
    sched = CmnScheduler([dev, wps], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # All events present, and groups may be merged or split depending on global (DTC) constraints.
    all_ev = set(dev + wps)
    flatten = {e.key() for g in groups for e in g}
    assert flatten == {e.key() for e in all_ev}
    perf_result = {g: tup_val(g) for g in groups}
    assert sched.retrieve_metric_result(perf_result, dev) == tup_val(dev)
    assert sched.retrieve_metric_result(perf_result, wps) == tup_val(wps)


def test_watchpoint_and_device_exactly_fit_group():
    """
    When device and WP events together fit the XP constraint, all should be merged; if not, correct splitting with all present.
    """
    xp = 4
    dev_tuple = (ev("E0", xp, xp), ev("E1", xp, xp))
    # 4 events total (2 dev, 2 wp): group must not exceed XP limit
    wps = (wp(xp_id=xp, port=1, direction="UP"), wp(xp_id=xp, port=2, direction="DOWN"))
    sched = CmnScheduler([dev_tuple, wps], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    all_ev = set(dev_tuple + wps)
    packed_keys = set()
    for g in groups:
        packed_keys |= set(g)
    # All events must appear in the output, but there may be more than one group for legality.
    assert all_ev == packed_keys
    perf_result = {g: tup_val(g) for g in groups}
    assert sched.retrieve_metric_result(perf_result, dev_tuple) == tup_val(dev_tuple)
    assert sched.retrieve_metric_result(perf_result, wps) == tup_val(wps)


def test_mixed_multi_xp_large_and_small():
    """
    Multi-XP: One XP is oversubscribed (spills), another fits and can merge dev + wp.
    """
    # XP 0 - spill: 6 device (MAX_EVENTS_PER_NODE), 3 WPs (MAX_WATCHPOINTS_PER_DIRECTION) - too many for a single group
    dev0 = tuple(ev(f"E{i}", 0, 0) for i in range(MAX_EVENTS_PER_NODE + 2))
    wp0 = tuple(
        wp(xp_id=0, port=i, direction="DOWN") for i in range(MAX_WATCHPOINTS_PER_DIRECTION + 1)
    )
    # XP 1 - can fit both
    dev1 = (ev("E1", 1, 1),)
    wp1 = (wp(xp_id=1, port=1, direction="UP"),)
    metrics = [dev0, wp0, dev1, wp1]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    keys = {e.key() for g in groups for e in g}
    want = {e.key() for tup in metrics for e in tup}
    assert keys == want
    # XP 0 group(s) must be singleton (spill), XP 1s' events (if total <= XP cap) may merge with each other.
    for g in groups:
        xp_ids = {getattr(e, "xp_id", -1) for e in g}
        if xp_ids == {0}:
            assert len(g) == 1
    perf_result = {g: tup_val(g) for g in groups}
    for tup in metrics:
        assert sched.retrieve_metric_result(perf_result, tup) == tup_val(tup)


# XP/node packing & retrieval tests                                           #
# ---------------------------------------------------------------------------#


def test_xp_pack_overflow_spills():
    """
    5 unique events on one XP and one node ID with no overlap lead to 5 singleton groups.
    """
    events = tuple(ev(f"E{i}", 0, 0) for i in range(5))
    metrics = [events]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Every input tuple yields a singleton group
    assert set(groups) == {(e,) for e in events}
    perf_result = {g: tup_val(g) for g in groups}

    sched.retrieve_metric_result(perf_result, events) == tup_val(events)


def test_multi_xp_packed_in_one_group_when_possible():
    """
    If several XPs are assigned to the same DTC and do not exceed the group limit,
    all events are packed into a single group.
    """
    e0a = ev("E1", 0, 0)
    e0b = ev("E2", 0, 0)
    e1a = ev("E1", 1, 2)
    e1b = ev("E2", 1, 2)
    # Both XPs go to DTC0, and only 4 events in total
    cmn = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
            NodeEntry(dtc=0, xp=1, node=2, node_type=0, port=2),
        ],
    )
    metrics = [(e0a, e0b), (e1a, e1b)]
    sched = CmnScheduler(metrics, cmn)
    groups = sched.get_optimized_event_groups()
    expected_group = (e0a, e0b, e1a, e1b)
    assert expected_group in groups
    perf_result = {g: tup_val(g) for g in groups}
    for tup in metrics:
        assert sched.retrieve_metric_result(perf_result, tup) == tup_val(tup)


def test_tuple_mixing_xp_is_error():
    """
    Any tuple with events from more than one crosspoint is rejected.
    """
    m = (ev("E1", 0, 0), ev("E1", 1, 0))
    with pytest.raises(ValueError, match="xp_id"):
        CmnScheduler([m], make_cmn_info())


def test_cycle_ev_with_xp_or_node_is_error():
    """
    Cycle event must have None for both xp and node, else TypeError.
    """
    with pytest.raises(TypeError):
        Event("", 0, None, 3)
    with pytest.raises(TypeError):
        Event("", None, 0, 3)
    with pytest.raises(TypeError):
        Event("", 0, 0, 3)


def test_xp_and_node_packing_merge_and_retrieval():
    """
    Input tuples each all events with same (xp, node), but covering multiple nodes of same xp.
    Should be packed together in XP-based packs.
    """
    # Node 0 and node 1 for XP 0
    e10_0 = ev("E1", 0, 0)
    e20_0 = ev("E2", 0, 0)
    e10_1 = ev("E1", 0, 1)
    e20_1 = ev("E2", 0, 1)
    metrics = [
        (e10_0, e20_0),
        (e10_1, e20_1),
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    expected_group = (e10_0, e20_0, e10_1, e20_1)
    assert expected_group in groups

    # Value retrieval
    perf_result = {expected_group: tup_val(expected_group)}
    for tup in metrics:
        want = tup_val(tup)
        got = sched.retrieve_metric_result(perf_result, tup)
        assert got == want


def test_xp_and_node_packs_separate_for_different_nodes():
    """
    If tuples per node (same xp), but enough events per tuple that splitting is logical,
    should generate independent groups.
    """
    # Node 0 and node 1, each with 3 events (total 6, fits in one if hardware allows);
    # this will test group formation logic whether splitting happens or not (check for both ways).
    e10_0, e20_0, e30_0 = (ev("E1", 0, 0), ev("E2", 0, 0), ev("E3", 0, 0))
    e10_1, e20_1, e30_1 = (ev("E1", 0, 1), ev("E2", 0, 1), ev("E3", 0, 1))
    metrics = [
        (e10_0, e20_0, e30_0),
        (e10_1, e20_1, e30_1),
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    group1 = (e10_0, e20_0, e30_0)
    group2 = (e10_1, e20_1, e30_1)
    # May be merged or split; check both exist if split, at least one if merged
    assert group1 in groups or group2 in groups
    # Value retrieval for both
    perf_result = {g: tup_val(g) for g in groups}
    for tup in metrics:
        want = tup_val(tup)
        got = sched.retrieve_metric_result(perf_result, tup)
        assert got == want


# Deduplication tests                                                         #
# ---------------------------------------------------------------------------#


def test_canonicalize_metrics_helper() -> None:
    e1 = ev("E1", 0)
    e2 = ev("E2", 0)
    metrics = [(e2, e1), (e1, e2)]
    ordered, key_to_id, input_tuples = CmnScheduler._canonicalize_metrics(metrics)

    expected = tuple(sorted((e1, e2), key=lambda e: e.key()))
    assert ordered == [expected]
    assert key_to_id == {tuple(e.key() for e in expected): 0}
    assert input_tuples == set(metrics)


def test_validate_device_tuple_helper_mixed_global_local() -> None:
    e_global = glob_ev("E1")
    e_local = ev("E1", 0)
    with pytest.raises(ValueError, match="Cannot mix global and local events in a device tuple"):
        CmnScheduler._validate_device_tuple([e_global, e_local], make_cmn_info())


def test_validate_device_tuple_helper_global_ok() -> None:
    # Global tuple must be schedulable based on topology (XP node count at limit is ok).
    cmn_info = make_cmn_info(
        dtc_count=1,
        nodes=[node(dtc=0, xp=0, node=n, type=0, port=n) for n in range(MAX_EVENTS_PER_XP)],
    )
    CmnScheduler._validate_device_tuple([glob_ev("E1"), glob_ev("E2")], cmn_info)


def test_validate_device_tuple_helper_local_ok() -> None:
    # Local tuple requires same xp_id and node_id across events.
    CmnScheduler._validate_device_tuple([ev("E1", 0, 0), ev("E2", 0, 0)], make_cmn_info())


def test_validate_device_tuple_helper_local_mismatched_xp() -> None:
    with pytest.raises(ValueError, match="same xp_id"):
        CmnScheduler._validate_device_tuple([ev("E1", 0, 0), ev("E2", 1, 1)], make_cmn_info())


def test_validate_device_tuple_helper_local_mismatched_node() -> None:
    with pytest.raises(ValueError, match="same node_id"):
        CmnScheduler._validate_device_tuple([ev("E1", 0, 0), ev("E2", 0, 1)], make_cmn_info())


def test_validate_device_tuple_helper_global_topology_overflow() -> None:
    cmn_info = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=n, type=0, port=n)
            for n in range(MAX_EVENTS_PER_XP + 1)
        ],
    )
    with pytest.raises(ValueError, match="global event tuple not allowed"):
        CmnScheduler._validate_device_tuple([glob_ev("E1")], cmn_info)


def test_validate_device_tuple_helper_global_topology_overflow_with_linux_type_alias() -> None:
    cmn_info = make_cmn_info(
        dtc_count=1,
        nodes=[
            node(dtc=0, xp=0, node=n, type=0x000A, port=n)
            for n in range(MAX_EVENTS_PER_XP - 1)
        ] + [
            node(dtc=0, xp=0, node=MAX_EVENTS_PER_XP + n, type=0x000D, port=MAX_EVENTS_PER_XP + n)
            for n in range(2)
        ],
        global_type_aliases=dict(CmnEvent.LINUX_FIX_MAP),
    )
    with pytest.raises(ValueError, match="global event tuple not allowed"):
        CmnScheduler._validate_device_tuple([glob_ev("E1", event_type=0x000A)], cmn_info)


def test_event_sort_key_orders_cycle_device_then_watchpoint() -> None:
    device_ev = ev("E1", 1, 1)
    watch_ev = wp(xp_id=1, port=0, direction="DOWN")
    cycle_key = cycle_ev().key()
    keys = [watch_ev.key(), cycle_key, device_ev.key()]
    ordered = sorted(keys, key=_event_sort_key)
    assert ordered == [cycle_key, device_ev.key(), watch_ev.key()]


def test_partition_local_global_tuples() -> None:
    t_local = _TupleReq(0, (ev("E1", 0),))
    t_global = _TupleReq(1, (glob_ev("E1"),))
    t_cycle = _TupleReq(2, (cycle_ev(),))
    t_mixed = _TupleReq(3, (glob_ev("E1"), global_wp("DEV")))
    local, global_, global_wp_tuples, global_mixed = CmnScheduler._partition_local_global_tuples(
        [t_local, t_global, t_cycle, t_mixed]
    )
    assert local == [t_local, t_cycle]
    assert global_ == [t_global]
    assert global_wp_tuples == []
    assert global_mixed == [t_mixed]


def test_validate_watchpoint_tuple_helper_mismatched_xp() -> None:
    w1 = wp(xp_id=0, port=1, direction="DOWN")
    w2 = wp(xp_id=1, port=2, direction="DOWN")
    with pytest.raises(ValueError, match="same xp_id"):
        CmnScheduler._validate_watchpoint_tuple(
            [w1, w2],
            make_cmn_info(),
        )


def test_reject_tuple_spanning_multiple_nodes():
    """
    Any tuple containing two or more events from distinct (non-cycle) nodes is rejected with ValueError.
    This property should always be enforced strictly; see contract.
    """
    metrics = [
        (ev("E1", 0), ev("E2", 1)),
    ]
    with pytest.raises(ValueError):
        CmnScheduler(metrics, make_cmn_info())


@pytest.mark.parametrize(
    "metrics",
    [
        # Exact duplicate
        [
            (ev("E1", 0), ev("E2", 0)),
            (ev("E1", 0), ev("E2", 0)),
        ],
        # Same events but different order -> still duplicate
        [
            (ev("E1", 0), ev("E2", 0)),
            (ev("E2", 0), ev("E1", 0)),
        ],
    ],
)
def test_duplicate_metric_eliminated(metrics) -> None:
    """
    Duplicate tuples, including those differing only in event order, are eliminated.
    Test both literal and permuted forms.
    """
    sched = CmnScheduler(metrics, make_cmn_info())
    assert len(sched.get_optimized_event_groups()) == 1


def test_duplicate_events_inside_tuple_eliminated() -> None:
    """
    A tuple containing multiple copies of the same event collapses to one event in the optimized group.
    """
    e1 = ev("E1", 0)
    metrics = [
        (e1, e1, e1),
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    assert groups[0] == (e1,)


# ---------------------------------------------------------------------------#
# Cohort-generation tests                                                     #
# ---------------------------------------------------------------------------#
def test_cohort_packing_single_dtc() -> None:
    """
    Four nodes, each with MAX_EVENTS_PER_NODE events, get packed into exactly two cohorts (hardware max: MAX_EVENTS_PER_NODE/node, MAX_EVENTS_PER_DTC/cohort).
    All groups strictly respect cohort size constraints.
    """
    metrics = [
        tuple(ev(f"E{i}", n) for i in range(1, MAX_EVENTS_PER_NODE + 1))  # E1-E4 on node n
        for n in range(MAX_EVENTS_PER_NODE)  # nodes 0..3
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Each group must have exactly MAX_EVENTS_PER_DTC events (PMU max)
    for g in groups:
        assert len(g) == MAX_EVENTS_PER_DTC  # PMU max

    assert flatten(groups) == {e.key() for tup in metrics for e in tup}


def test_overlap_three_tuples_merge_into_one_group() -> None:
    """
    Overlapping tuples on a single node are merged/packed into a minimal group;
    (E1,E2), (E1,E3), (E2,E4) must result in exactly one group with all unique events.
    """
    e1, e2, e3, e4 = (ev("E1", 0), ev("E2", 0), ev("E3", 0), ev("E4", 0))

    metrics = [
        (e1, e2),
        (e1, e3),
        (e2, e4),
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()

    assert len(groups) == 1
    assert flatten(groups) == {e for e in map(lambda e: e.key(), (e1, e2, e3, e4))}


# ---------------------------------------------------------------------------#
# Ordering tests                                                              #
# ---------------------------------------------------------------------------#
def test_event_ordering_within_group_node_then_event() -> None:
    """
    Event order within groups is stable and node-centric: sorted node then sorted event-id.
    Ensures deterministic mapping (and error-freeness in retrieval).
    """
    # Single node ordering
    e1, e2, e3 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [(e1, e3, e2)]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    assert group == (e1, e2, e3)

    # Multiple nodes ordering
    e1_10, e1_0 = (ev("E1", 10), ev("E1", 0))
    metrics2 = [(e1_10,), (e1_0,)]
    sched2 = CmnScheduler(metrics2, make_cmn_info())
    group2 = sched2.get_optimized_event_groups()[0]
    assert group2 == (e1_0, e1_10)


# ---------------------------------------------------------------------------#
# Retrieval tests                                                             #
# ---------------------------------------------------------------------------#
@pytest.mark.parametrize(
    "metrics",
    [
        # Simple non-overlapping events
        [(ev("E1", 0),), (ev("E2", 0),), (ev("E3", 0),)],
        # Overlapping – will be merged
        [
            (ev("E1", 0), ev("E2", 0)),
            (ev("E1", 0), ev("E3", 0)),
            (ev("E2", 0), ev("E4", 0)),
        ],
    ],
)
def test_retrieval_roundtrip(metrics) -> None:
    """
    Build a fake perf result whose values equal the numeric part of the event
    id.  The scheduler should retrieve those values in the order of the original
    metric tuple (even if the internal group is ordered differently).
    """
    sched = CmnScheduler(metrics, make_cmn_info())

    perf_result = build_perf_result(sched.get_optimized_event_groups())
    for metric in metrics:
        expected = tup_val(metric)
        assert sched.retrieve_metric_result(perf_result, metric) == expected


# ---------------------------------------------------------------------------
# Retrieval order-specific tests (explicit user requests)
# ---------------------------------------------------------------------------
def test_retrieval_group_order_and_mapping_case1():
    # Input shuffled (E1@I0:XP0:N0, E3@I0:XP0:N0, E2@I0:XP0:N0), output group should be
    # (E1@I0:XP0:N0, E2@I0:XP0:N0, E3@I0:XP0:N0)
    e1, e2, e3 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [(e1, e3, e2)]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    group = groups[0]
    assert group == (e1, e2, e3)
    # perf output is ordered, input is unordered
    perf_result = build_perf_result([group])
    # When requesting (E1@I0:XP0:N0, E3@I0:XP0:N0, E2@I0:XP0:N0) should yield (1.0, 3.0, 2.0)
    assert sched.retrieve_metric_result(perf_result, (e1, e3, e2)) == (1.0, 3.0, 2.0)


def test_retrieval_group_order_and_mapping_case2():
    # Input: [(E1@I0:XP10:N10,), (E1@I0:XP0:N0,)], optimizer should output group
    # (E1@I0:XP0:N0, E1@I0:XP10:N10)
    e1_10, e1_0 = (ev("E1", 10), ev("E1", 0))
    metrics = [(e1_10,), (e1_0,)]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    assert group == (e1_0, e1_10)
    perf_result = {group: (0, 10)}
    assert sched.retrieve_metric_result(perf_result, (e1_10,)) == (10,)
    assert sched.retrieve_metric_result(perf_result, (e1_0,)) == (0,)


# ---------------------------------------------------------------------------
# Retrieval strict input-tuple test (user spec)
# ---------------------------------------------------------------------------
def test_retrieval_only_for_input_tuples():
    e1, e2, e3 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [
        (e1, e3, e2),
        (e2, e1, e3),
        (e3, e1, e2),
    ]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    assert group == (e1, e2, e3)
    perf_result = build_perf_result([group])
    test_cases = {
        (e1, e3, e2): (1.0, 3.0, 2.0),
        (e2, e1, e3): (2.0, 1.0, 3.0),
        (e3, e1, e2): (3.0, 1.0, 2.0),
    }
    for tup, expect in test_cases.items():
        got = sched.retrieve_metric_result(perf_result, tup)
        assert got == expect
    # Not in input set (sorted tuple) should raise
    with pytest.raises(KeyError):
        sched.retrieve_metric_result(perf_result, (e1, e2, e3))


# ---------------------------------------------------------------------------
# Duplicates in retrieval should yield duplicate values
# ---------------------------------------------------------------------------
def test_retrieval_duplicate_events_in_metric():
    e1, e2 = ev("E1", 0), ev("E2", 0)
    t = (e1, e2, e1)
    metrics = [t]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    # Group contains unique events, but retrieval with duplicates in input should work
    assert group == (e1, e2)
    perf_result = build_perf_result([group])
    # Should retrieve (value of e1, value of e2, value of e1)
    assert sched.retrieve_metric_result(perf_result, t) == tup_val(t)


def test_retrieval_event_across_multiple_groups():
    e1, e2, e3, e4 = (ev("E1", 0), ev("E2", 0), ev("E3", 0), ev("E4", 0))
    e11, e12, e14 = (ev("E11", 0), ev("E12", 0), ev("E14", 0))

    t0 = (e1, e2, e3)
    t1 = (e2, e3, e4)
    t10 = (e11, e12, e3)
    t11 = (e12, e3, e14)

    metrics = [t0, t1, t10, t11]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 2
    g1 = (e1, e2, e3, e4)
    # With numeric sort of event_id, expect: (E3, E11, E12, E14)
    g2 = (e3, e11, e12, e14)

    assert g1 in groups
    assert g2 in groups

    # The actual order of optimized groups may vary; find the two by their exact keys
    perf_result = {g1: (1.0, 2.0, 3.0, 4.0), g2: (13.0, 11.0, 12.0, 14.0)}
    expected = {
        t0: (1.0, 2.0, 3.0),
        t1: (2.0, 3.0, 4.0),
        t10: (11.0, 12.0, 13.0),
        t11: (12.0, 13.0, 14.0),
    }
    for tup, want in expected.items():
        got = sched.retrieve_metric_result(perf_result, tup)
        assert got == want, f"For {tup}, got {got}, want {want}"


def test_retrieval_different_nodes_same_event_ids():
    """
    Same event IDs but on different nodes: must not collide.
    Optimized group should contain all per-node versions.
    Perf value is node_id + event_num (E2@I0:XP0:N0=2, E2@I0:XP10:N10=12, etc).
    """
    e1_0, e2_0, e3_0 = t0 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    e1_10, e2_10, e3_10 = t10 = (ev("E1", 10), ev("E2", 10), ev("E3", 10))
    metrics = [t0, t10]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 1
    group = groups[0]
    assert groups[0] == (e1_0, e2_0, e3_0, e1_10, e2_10, e3_10)

    perf_result = {group: tup_val(group)}
    expect = {
        t0: tup_val(t0),
        t10: tup_val(t10),
    }
    for metric, want in expect.items():
        got = sched.retrieve_metric_result(perf_result, metric)
        assert got == want, f"{metric}: got {got} want {want}"


def test_multi_node_event_packing_and_retrieval():
    """
    Validate correct packing and retrieval for multiple nodes. Test generates:
    - Four tuples, each assigned to a distinct node (0, 10, 20, 30)
    - The scheduler packs them into two groups of 8 events each (constraint: 4 per node per group, 8 per group)
    - Each event's value in perf_result is the event's numeric ID
    - Retrieval uses this mapping for each metric
    """

    e1, e2, e3, e4 = t0 = (ev("E1", 0), ev("E2", 0), ev("E3", 0), ev("E4", 0))
    e11, e12, e13, e14 = t10 = (ev("E11", 10), ev("E12", 10), ev("E13", 10), ev("E14", 10))
    e21, e22, e23, e24 = t20 = (ev("E21", 20), ev("E22", 20), ev("E23", 20), ev("E24", 20))
    e31, e32, e33, e34 = t30 = (ev("E31", 30), ev("E32", 30), ev("E33", 30), ev("E34", 30))

    metrics = [t0, t10, t20, t30]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # We must have exactly 2 groups of 8 events each by design
    assert len(groups) == 2, "Expected 2 groups for 4 nodes of 4 events"
    for grp in groups:
        assert len(grp) == 8, "Each group must have 8 events"

    # Perf result: each event value is the integer value of its event id (numeric part)
    perf_result = {grp: tup_val(grp) for grp in groups}

    # Explicit expected values
    expected = {
        t0: tup_val(t0),
        t10: tup_val(t10),
        t20: tup_val(t20),
        t30: tup_val(t30),
    }
    for tup, want in expected.items():
        got = sched.retrieve_metric_result(perf_result, tup)
        assert got == want, f"{tup}: got {got}, want {want}"


def test_multi_dtc_group_packing_and_retrieval():
    """
    Ensure group packing per DTC works and retrieval is correct.
    DTC 0: nodes 0, 10; DTC 1: nodes 100, 110.
    """
    e1_0, e2_0, e3_0 = t0 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    e1_10, e2_10, e3_10 = t10 = (ev("E1", 10), ev("E2", 10), ev("E3", 10))
    e1_100, e2_100, e3_100 = t100 = (ev("E1", 100), ev("E2", 100), ev("E3", 100))
    e1_110, e2_110, e3_110 = t110 = (ev("E1", 110), ev("E2", 110), ev("E3", 110))
    metrics = [t0, t10, t100, t110]
    # DTC mapping: <100→0, >=100→1
    cmn_info = CmnInfo(
        dtc_count=2,
        dtc_of=lambda nid: 0 if nid < 100 else 1,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
            NodeEntry(dtc=0, xp=10, node=10, node_type=0, port=10),
            NodeEntry(dtc=1, xp=100, node=100, node_type=0, port=100),
            NodeEntry(dtc=1, xp=110, node=110, node_type=0, port=110),
        ],
    )
    sched = CmnScheduler(metrics, cmn_info)
    groups = sched.get_optimized_event_groups()
    assert len(groups) == 2  # 2 DTCs: should be two optimized groups

    # Value: node_id + event_num (numeric part)
    perf_result = {group: tup_val(group) for group in groups}

    expect = {
        t0: tup_val(t0),
        t10: tup_val(t10),
        t100: tup_val(t100),
        t110: tup_val(t110),
    }

    for metric, want in expect.items():
        got = sched.retrieve_metric_result(perf_result, metric)
        assert got == want, f"{metric}: got {got} want {want}"


def test_perf_result_missing_group_key():
    """Should raise KeyError if expected group key is missing from perf_result."""
    e1, e2, e3 = t0 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [t0]
    sched = CmnScheduler(metrics, make_cmn_info())
    # Give group key missing one event
    perf_result = {(e1, e2): (1, 2)}
    with pytest.raises(KeyError):
        sched.retrieve_metric_result(perf_result, metrics[0])


def test_perf_result_wrong_length():
    """Should raise IndexError if value list in perf_result is the wrong length."""
    metrics = [(ev("E1", 0), ev("E2", 0), ev("E3", 0))]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    perf_result = {group: (1, 2)}  # should have 3 values
    with pytest.raises(IndexError):
        sched.retrieve_metric_result(perf_result, metrics[0])


def test_perf_result_extra_group():
    """Extra keys in perf_result should raise an error; group key set must be exact."""
    metrics = [(ev("E1", 0), ev("E2", 0), ev("E3", 0))]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    perf_result = {
        group: (1, 2, 3),
        (ev("E1", 10), ev("E2", 10), ev("E3", 10)): (11, 12, 13),
    }
    with pytest.raises(KeyError, match="perf_result keys do not match"):
        sched.retrieve_metric_result(perf_result, metrics[0])


def test_perf_result_wrong_event_order():
    """Perf group key is in wrong order: this is treated as a different group and should fail."""
    e1, e2, e3 = t0 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [t0]
    sched = CmnScheduler(metrics, make_cmn_info())
    # perf_result has correct events, wrong tuple order (shouldn't match)
    wrong_group = (e2, e1, e3)
    perf_result = {wrong_group: (2, 1, 3)}
    with pytest.raises(KeyError):
        sched.retrieve_metric_result(perf_result, metrics[0])


def test_cycle_event_always_first_in_optimized_group():
    """Cycle event must appear first in the group, retrieval maps to user order."""
    e1, e2, cyc = t0 = (ev("E1", 0), ev("E2", 0), cycle_ev())
    metrics = [t0]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    assert group == (cyc, e1, e2)
    perf_result = {group: (1000.0, 1.0, 2.0)}
    # Retrieval: should match requested tuple order
    values = sched.retrieve_metric_result(perf_result, t0)
    assert values == (1.0, 2.0, 1000.0)


def test_retriever_propagates_none_in_perf():
    """
    If perf_result maps an event to None, the retriever must return it in the right place.
    """
    e1, e2, e3 = (ev("E1", 0), ev("E2", 0), ev("E3", 0))
    metrics = [(e2, e1, e3)]
    sched = CmnScheduler(metrics, make_cmn_info())
    group = sched.get_optimized_event_groups()[0]
    # event keys of group will be (E1@I0, E2@I0, E3@I0)
    perf_result = {group: (1.0, None, 3.0)}
    # Retrieval for order (E2, E1, E3) → (None, 1.0, 3.0)
    assert sched.retrieve_metric_result(perf_result, metrics[0]) == (None, 1.0, 3.0)


def test_group_with_and_without_cycle_event():
    """
    Group with and without cycle event can be packed, and each input retrieves correct values.
    """
    e1, e2, e3, e4 = (ev("E1", 0), ev("E2", 0), ev("E3", 0), ev("E4", 0))
    cyc = cycle_ev()

    t0 = (e1, e2, cyc)
    t1 = (e3, e4)

    metrics = [t0, t1]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Two distinct groups: one with cycle, one without
    assert any(cyc in g for g in groups)
    # Build a perf_result with correct event ordering for each group
    perf_result = {g: tup_val(g) for g in groups}
    expected = {
        t0: tup_val(t0),
        t1: tup_val(t1),
    }
    for metric, want in expected.items():
        got = sched.retrieve_metric_result(perf_result, metric)
        assert got == want, f"{metric}: got {got}, want {want}"


def test_event_node_id_must_be_int():
    ev("E1", 0, 0)
    cycle_ev()
    with pytest.raises(TypeError, match="nodeid must be int"):
        CmnEvent(
            name="bad",
            title="",
            description="",
            cmn_index=0,
            type=0,
            eventid=1,
            occupid=None,
            nodeid="not-an-int",
            xp_id=0,
        )


def test_event_xp_id_must_be_int_or_none():
    ev("E1", 0, 0)
    cycle_ev()
    with pytest.raises(TypeError, match="xp_id must be int"):
        CmnEvent(
            name="bad",
            title="",
            description="",
            cmn_index=0,
            type=0,
            eventid=1,
            occupid=None,
            nodeid=0,
            xp_id="not-an-int",
        )


def test_empty_tuple_returns_empty_result():
    """
    Empty input tuple yields no optimized group, but is still retrievable as () when perf_result is empty.
    """
    metrics = [tuple()]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert groups == []
    assert sched.retrieve_metric_result({}, tuple()) == ()


def test_tuple_with_only_cycle():
    """
    A tuple containing only the cycle event is allowed, retrieval works as expected.
    """
    metrics = [(cycle_ev(),)]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert groups == [(cycle_ev(),)]
    perf_result = {groups[0]: (1234.0,)}
    assert sched.retrieve_metric_result(perf_result, (cycle_ev(),)) == (1234.0,)


def test_mixed_empty_and_nonempty_tuples():
    """
    An empty and a non-empty tuple: only the non-empty is optimized.
    Retrieval for empty tuple returns (), nonempty returns value.
    """
    e1 = ev("E1", 0)
    metrics = [tuple(), (e1,)]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    assert () not in groups
    group1 = groups[0]
    perf_result = {group1: (2.0,)}
    assert sched.retrieve_metric_result(perf_result, tuple()) == ()
    assert sched.retrieve_metric_result(perf_result, (e1,)) == (2.0,)


def test_large_stress_retrieval_across_dtcs_and_nodes():
    """
    Randomized scale/stress test:
    - 4 DTCs, 10 nodes/DTC. Each node: 1-4 random event ids.
    Validates that all packing constraints are respected at scale, and that retrieval correctness/invariants hold.
    """
    random.seed(42)
    num_dtcs = 4
    nodes_per_dtc = 10
    nodes = [d * 100 + n * 10 for d in range(num_dtcs) for n in range(nodes_per_dtc)]
    metrics = []
    cmn_nodes = []
    for cmn_node in nodes:
        n_events = random.randint(1, MAX_EVENTS_PER_NODE)
        event_ids = random.sample(range(1, 10), n_events)
        metrics.append(tuple(ev(f"E{id}", cmn_node) for id in event_ids))
        cmn_nodes.append(
            NodeEntry(
                dtc=cmn_node // 100,
                xp=cmn_node,
                node=cmn_node,
                node_type=0,
                port=cmn_node,
            )
        )

    sched = CmnScheduler(metrics, CmnInfo(num_dtcs, lambda nid: nid // 100, nodes=cmn_nodes))
    groups = sched.get_optimized_event_groups()
    for group in groups:
        assert len(group) <= MAX_EVENTS_PER_DTC

    perf_result = build_perf_result(groups)

    for metric in metrics:
        expected = tup_val(metric)
        got = sched.retrieve_metric_result(perf_result, metric)
        assert got == expected, f"{metric}: got {got}, want {expected}"


# ---------------------------------------------------------------------------#
# Oversize-tuple singleton & cycle-only spill packs test
# ---------------------------------------------------------------------------#
def test_two_large_tuples_all_singletons_and_cycle() -> None:
    """
    Two oversized tuples on the same node must produce only singleton groups
    for each event, plus a standalone cycle group.
    """
    # First tuple: E1-E6 and cycle
    t1 = (ev("E1", 0), ev("E2", 0), ev("E3", 0), ev("E4", 0), ev("E5", 0), ev("E6", 0), cycle_ev())
    # Second tuple: overlapping E4-E6 and new E7-E9
    t2 = (ev("E4", 0), ev("E5", 0), ev("E6", 0), ev("E7", 0), ev("E8", 0), ev("E9", 0))
    sched = CmnScheduler([t1, t2], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Expect one group for cycle and one per event E1..E9@I0
    expected_keys = {cycle_ev().key()} | {f"0:{i}@I0:XP0:N0" for i in range(1, 10)}
    actual_keys = {e.key() for grp in groups for e in grp}
    assert actual_keys == expected_keys
    assert len(groups) == len(set(groups))
    # All groups must be single-event groups
    assert all(len(grp) == 1 for grp in groups)
    # Validate retrieval
    perf = build_perf_result(groups)
    assert sched.retrieve_metric_result(perf, t1) == tup_val(t1)
    assert sched.retrieve_metric_result(perf, t2) == tup_val(t2)


def test_large_local_watchpoint_tuples_with_cycle_share_single_cycle_group_across_xps() -> None:
    wp40 = (
        wp(xp_id=0x40, port=1, direction="UP", value=11),
        wp(xp_id=0x40, port=1, direction="UP", value=12),
        wp(xp_id=0x40, port=1, direction="UP", value=13),
    )
    wp100 = (
        wp(xp_id=0x100, port=1, direction="UP", value=21),
        wp(xp_id=0x100, port=1, direction="UP", value=22),
        wp(xp_id=0x100, port=1, direction="UP", value=23),
    )
    metrics = [
        (cycle_ev(),),
        wp40,
        wp100,
        (cycle_ev(),) + wp40,
        (cycle_ev(),) + wp100,
    ]

    sched = CmnScheduler(metrics, make_cmn_info(dtc_count=1))
    groups = list(sched.get_optimized_event_groups())

    assert len(groups) == 7
    assert len(groups) == len(set(groups))
    assert groups.count((cycle_ev(),)) == 1
    for watchpoint in wp40 + wp100:
        assert groups.count((watchpoint,)) == 1

    perf_result = build_perf_result(groups)
    for metric in metrics:
        assert sched.retrieve_metric_result(perf_result, metric) == tup_val(metric)


def test_large_local_watchpoint_tuples_with_cycle_share_single_cycle_group_across_dtcs() -> None:
    wp0 = (
        wp(xp_id=0, port=1, direction="UP", value=31),
        wp(xp_id=0, port=1, direction="UP", value=32),
        wp(xp_id=0, port=1, direction="UP", value=33),
    )
    wp1 = (
        wp(xp_id=1, port=1, direction="UP", value=41),
        wp(xp_id=1, port=1, direction="UP", value=42),
        wp(xp_id=1, port=1, direction="UP", value=43),
    )
    metrics = [
        wp0,
        wp1,
        (cycle_ev(),) + wp0,
        (cycle_ev(),) + wp1,
    ]

    sched = CmnScheduler(metrics, make_cmn_info(dtc_count=2))
    groups = list(sched.get_optimized_event_groups())

    assert len(groups) == 7
    assert len(groups) == len(set(groups))
    assert groups.count((cycle_ev(),)) == 1
    for watchpoint in wp0 + wp1:
        assert groups.count((watchpoint,)) == 1

    perf_result = build_perf_result(groups)
    for metric in metrics:
        assert sched.retrieve_metric_result(perf_result, metric) == tup_val(metric)


def test_large_local_watchpoint_tuples_reuse_shared_singletons() -> None:
    wp40 = (
        wp(xp_id=0x40, port=1, direction="UP", value=1),
        wp(xp_id=0x40, port=1, direction="UP", value=2),
        wp(xp_id=0x40, port=1, direction="UP", value=3),
    )
    wp40_2 = (
        wp(xp_id=0x40, port=1, direction="UP", value=1),
        wp(xp_id=0x40, port=1, direction="UP", value=2),
        wp(xp_id=0x40, port=1, direction="UP", value=42),
    )

    sched = CmnScheduler([wp40, wp40_2], make_cmn_info(dtc_count=1))
    groups = list(sched.get_optimized_event_groups())

    expected_singletons = {
        (wp40[0],),
        (wp40[1],),
        (wp40[2],),
        (wp40_2[2],),
    }
    assert set(groups) == expected_singletons
    assert len(groups) == len(set(groups)) == 4
    assert all(len(group) == 1 for group in groups)

    perf_result = build_perf_result(groups)
    assert sched.retrieve_metric_result(perf_result, wp40) == tup_val(wp40)
    assert sched.retrieve_metric_result(perf_result, wp40_2) == tup_val(wp40_2)


# ---------------------------------------------------------------------------#
# Oversize-tuple scattering and retrieval tests
# ---------------------------------------------------------------------------#
def test_oversize_tuple_scatter_and_retrieval() -> None:
    """
    A tuple with >MAX_EVENTS_PER_NODE events must be scattered across multiple packs/groups,
    but retrieval should reconstruct per-user ordering correctly.
    """
    # small tuples and one large tuple of 5 events
    e1, e2, e3, e4, e5, e6, e7, e8, e9 = (ev(f"E{i}", 0) for i in range(1, 10))
    t1 = (e1, e2, e3)
    t2 = (e7, e8, e9)
    t3 = (e3, e4, e5, e6, e7)
    metrics = [t1, t2, t3]
    sched = CmnScheduler(metrics, make_cmn_info())
    groups = sched.get_optimized_event_groups()
    expected_groups = {
        t1,
        t2,
        (e4,),
        (e5,),
        (e6,),
    }
    assert len(groups) == len(expected_groups)
    for g in expected_groups:
        assert g in groups

    # Perf result for all groups, using event_value
    perf_result = build_perf_result(groups)
    # t1 and t2 must retrieve correct values
    assert sched.retrieve_metric_result(perf_result, t1) == tup_val(t1)
    assert sched.retrieve_metric_result(perf_result, t2) == tup_val(t2)
    # Retrieval from t3 order
    assert sched.retrieve_metric_result(perf_result, t3) == tup_val(t3)


@pytest.mark.parametrize("count", [5, 8, 12, 20])
def test_random_oversize_tuple(count) -> None:
    """
    Random tuple of length >MAX_EVENTS_PER_NODE on one node is split into packs,
    and retrieval reconstructs user-defined order.
    """
    node = 42
    evs = tuple(ev(i, node, node) for i in range(count))
    sched = CmnScheduler([evs], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # flatten keys == all events
    assert flatten(groups) == {e.key() for e in evs}
    # retrieval values match user ordering
    perf = build_perf_result(groups)
    res = sched.retrieve_metric_result(perf, evs)
    want = tup_val(evs)
    assert res == want


# ---------------------------------------------------------------------------#
# Event type specific tests                                                   #
# ---------------------------------------------------------------------------#
def test_tuple_mixed_event_types_error():
    """Tuple mixing different event_type values (non-cycle) must raise ValueError."""
    e1 = ev("E1", 0, event_type=0)
    e2 = ev("E2", 0, event_type=1)
    with pytest.raises(ValueError, match="event_type"):
        CmnScheduler([(e1, e2)], make_cmn_info())


def test_event_type_sort_order():
    """Events are ordered by ascending event_type within the same xp/node."""
    e_low = ev("E1", 0, event_type=0)
    e_high = ev("E2", 0, event_type=2)
    # Separate tuples so the scheduler can merge them into a single cohort/group
    sched = CmnScheduler([(e_low,), (e_high,)], make_cmn_info())
    groups = sched.get_optimized_event_groups()
    # Expect a single group with e_low first due to lower event_type
    assert len(groups) == 1
    assert groups[0] == (e_low, e_high)
