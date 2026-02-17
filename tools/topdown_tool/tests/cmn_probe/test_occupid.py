# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

import pytest
from topdown_tool.cmn_probe.common import Event, Watchpoint
from topdown_tool.cmn_probe.scheduler import (
    event_from_key,
    CmnScheduler,
    NodeEntry,
    WatchpointPort,
    MAX_EVENTS_PER_XP,
)
from .helpers import ev, glob_ev, make_cmn_info, build_perf_result, tup_val, wp


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


def test_key_and_from_key_global_with_and_without_occupid():
    # No occupid
    e_no_occ = Event(
        name="E42",
        title="",
        description="",
        cmn_index=0,
        type=2,
        eventid=42,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )
    key_no = e_no_occ.key()
    assert key_no == "2:42@I0"
    e_rnd_no = event_from_key(key_no)
    assert e_no_occ == e_rnd_no

    # With occupid
    e_occ = Event(
        name="E42",
        title="",
        description="",
        cmn_index=0,
        type=2,
        eventid=42,
        occupid=15,
        nodeid=None,
        xp_id=None,
    )
    key_occ = e_occ.key()
    assert key_occ == "2:42:15@I0"
    e_rnd = event_from_key(key_occ)
    assert e_occ == e_rnd
    assert e_rnd.occupid == 15


def test_key_and_from_key_local_with_and_without_occupid():
    # No occupid
    e_no_occ = Event(
        name="E7",
        title="",
        description="",
        cmn_index=0,
        type=1,
        eventid=7,
        occupid=None,
        nodeid=10,
        xp_id=4,
    )
    key_no = e_no_occ.key()
    assert key_no == "1:7@I0:XP4:N10"
    e_rnd_no = event_from_key(key_no)
    assert e_no_occ == e_rnd_no

    # With occupid
    e_occ = Event(
        name="E7",
        title="",
        description="",
        cmn_index=0,
        type=1,
        eventid=7,
        occupid=7,
        nodeid=10,
        xp_id=4,
    )
    key_occ = e_occ.key()
    assert key_occ == "1:7:7@I0:XP4:N10"
    e_rnd = event_from_key(key_occ)
    assert e_occ == e_rnd
    assert e_rnd.occupid == 7


def test_scheduler_dedup_within_tuple_with_occupid():
    # Duplicate the same event (with occupid) inside a tuple: group dedups; retrieval yields correct values
    cmn = make_cmn_info(dtc_count=1, nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0)])
    e = ev("E2", 0, 0, event_type=0, occupid=55)
    tup = (e, e, e)
    sched = CmnScheduler([tup], cmn)
    groups = sched.get_optimized_event_groups()
    assert any(g.count(e) == 1 for g in groups)
    perf = {g: (9.0,) for g in groups}
    out = sched.retrieve_metric_result(perf, tup)
    assert out == (9.0, 9.0, 9.0)


def test_from_key_malformed_inputs():
    # Catch input errors: wrong or missing occupid values, etc.
    with pytest.raises(ValueError):
        event_from_key("foo:1@I0")  # Non-integer event_type
    with pytest.raises(ValueError):
        event_from_key("2:1:notanint@I0")  # non-integer occupid
    with pytest.raises(ValueError):
        event_from_key("1:1:12@I0:XP4NN10")  # malformed XP/N


def test_mixed_occupid_allowed_different_nodes():
    """
    Different nodes (or XP) may legitimately use different occupid values.
    This must succeed and the scheduler must keep events separate but valid.
    """
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[
            NodeEntry(dtc=0, xp=1, node=1, node_type=0, port=1),
            NodeEntry(dtc=0, xp=2, node=2, node_type=0, port=2),
        ],
    )

    e_node1 = ev("E1", 1, 1, occupid=None)
    e_node2 = ev("E1", 2, 2, occupid=77)

    sched = CmnScheduler([(e_node1,), (e_node2,)], cmn)
    groups = sched.get_optimized_event_groups()

    # Ensure the two events end up in (possibly distinct) groups and occupid is preserved
    keys = {e_node1.key(), e_node2.key()}
    assert all(any(ev.key() == k for ev in grp) for k in keys for grp in groups)

    # Round-trip retrieval
    perf = build_perf_result(groups)
    assert sched.retrieve_metric_result(perf, (e_node1,)) == tup_val((e_node1,))
    assert sched.retrieve_metric_result(perf, (e_node2,)) == tup_val((e_node2,))


def test_local_events_not_merged_if_occupid_differs():
    """
    Two local events on the same node with different occupid values must end up
    in distinct optimized groups.
    """
    cmn = make_cmn_info(dtc_count=1, nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0)])

    e_no_occ = ev("E1", 0, 0, occupid=None)
    e_occ_50 = ev("E2", 0, 0, occupid=50)

    sched = CmnScheduler([(e_no_occ,), (e_occ_50,)], cmn)
    groups = sched.get_optimized_event_groups()

    # Neither group should contain both events
    assert all(not ({e_no_occ.key(), e_occ_50.key()} <= {ev.key() for ev in g}) for g in groups)


def test_global_events_not_merged_if_occupid_differs():
    """
    Two global events of the same event_type but different occupid values
    must not appear in the same optimized group.
    """
    cmn = make_cmn_info(dtc_count=1)

    g1 = glob_ev("E1", event_type=1)  # occupid = None/0
    g2 = glob_ev("E2", event_type=1)  # occupid = None/0
    g3 = glob_ev("E3", event_type=1)  # occupid = None/0
    g5 = glob_ev("E5", event_type=1, occupid=75)  # occupid = 75

    sched = CmnScheduler([(g1, g2, g3), (g5,)], cmn)
    groups = sched.get_optimized_event_groups()

    # Every group that contains g5 must contain ONLY g5 for that event_type
    for grp in groups:
        keys = {ev.key() for ev in grp}
        if g5.key() in keys:
            assert keys == {g5.key()}


def test_local_tuple_with_mixed_occupid_splits_and_retrieves():
    cmn = make_cmn_info(dtc_count=1, nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)])
    e_no_occ = ev("E1", 0, 0, event_type=1, occupid=None)
    e_occ_50 = ev("E2", 0, 0, event_type=1, occupid=50)
    metric = (e_no_occ, e_occ_50)

    sched = CmnScheduler([metric], cmn)
    groups = sched.get_optimized_event_groups()

    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({e_no_occ.key()}),
        frozenset({e_occ_50.key()}),
    }

    values = {e_no_occ.key(): 10.0, e_occ_50.key(): 20.0}
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}
    assert sched.retrieve_metric_result(perf_result, metric) == (10.0, 20.0)


def test_global_tuple_with_mixed_occupid_splits_and_retrieves():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=2, port=0)],
    )
    g_no_occ = glob_ev("E1", event_type=2, occupid=None)
    g_occ_75 = glob_ev("E2", event_type=2, occupid=75)
    metric = (g_no_occ, g_occ_75)

    sched = CmnScheduler([metric], cmn)
    groups = sched.get_optimized_event_groups()

    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({g_no_occ.key()}),
        frozenset({g_occ_75.key()}),
    }

    values = {g_no_occ.key(): 30.0, g_occ_75.key(): 40.0}
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}
    assert sched.retrieve_metric_result(perf_result, metric) == (30.0, 40.0)


def test_local_mixed_tuple_with_mixed_occupid_splits_and_retrieves():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=1, node=10, node_type=5, port=1)],
    )
    e_no_occ = ev("E1", 1, 10, event_type=5, occupid=None)
    e_occ_9 = ev("E1", 1, 10, event_type=5, occupid=9)
    watch = wp(xp_id=1, port=1, direction="UP", value=7)
    metric = (e_no_occ, e_occ_9, watch)

    sched = CmnScheduler([metric], cmn)
    groups = sched.get_optimized_event_groups()

    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({e_no_occ.key()}),
        frozenset({e_occ_9.key()}),
        frozenset({watch.key()}),
    }

    values = {e_no_occ.key(): 1.0, e_occ_9.key(): 2.0, watch.key(): 3.0}
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}
    assert sched.retrieve_metric_result(perf_result, metric) == (1.0, 2.0, 3.0)


def test_global_mixed_tuple_with_mixed_occupid_splits_and_retrieves():
    ports = [WatchpointPort(xp_id=0, port=1)]
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=4, port=0)],
        watchpoint_ports_by_device={"DEV": ports},
    )
    g_no_occ = glob_ev("E1", event_type=4, occupid=None)
    g_occ_11 = glob_ev("E2", event_type=4, occupid=11)
    watch = global_wp("DEV", direction="DOWN", value=5)
    metric = (g_no_occ, g_occ_11, watch)

    sched = CmnScheduler([metric], cmn)
    groups = sched.get_optimized_event_groups()

    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({g_no_occ.key()}),
        frozenset({g_occ_11.key()}),
        frozenset({watch.key()}),
    }

    values = {g_no_occ.key(): 4.0, g_occ_11.key(): 5.0, watch.key(): 6.0}
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}
    assert sched.retrieve_metric_result(perf_result, metric) == (4.0, 5.0, 6.0)


def test_duplicate_identical_metrics_with_mixed_occupid_retrieve_consistently():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=6, port=0)],
    )
    metric_a = (
        ev("E1", 0, 0, event_type=6, occupid=None),
        ev("E2", 0, 0, event_type=6, occupid=50),
    )
    metric_b = (
        ev("E1", 0, 0, event_type=6, occupid=None),
        ev("E2", 0, 0, event_type=6, occupid=50),
    )

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()

    assert len(groups) == 2
    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({metric_a[0].key()}),
        frozenset({metric_a[1].key()}),
    }

    values = {
        metric_a[0].key(): 101.0,
        metric_a[1].key(): 202.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == (101.0, 202.0)
    assert sched.retrieve_metric_result(perf_result, metric_b) == (101.0, 202.0)


def test_duplicate_identical_global_metrics_with_mixed_occupid_retrieve_consistently():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=7, port=0)],
    )
    metric_a = (
        glob_ev("E1", event_type=7, occupid=None),
        glob_ev("E2", event_type=7, occupid=50),
    )
    metric_b = (
        glob_ev("E1", event_type=7, occupid=None),
        glob_ev("E2", event_type=7, occupid=50),
    )

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()

    assert len(groups) == 2
    assert {frozenset(ev.key() for ev in group) for group in groups} == {
        frozenset({metric_a[0].key()}),
        frozenset({metric_a[1].key()}),
    }

    values = {
        metric_a[0].key(): 303.0,
        metric_a[1].key(): 404.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == (303.0, 404.0)
    assert sched.retrieve_metric_result(perf_result, metric_b) == (303.0, 404.0)


def test_duplicate_global_metrics_with_mixed_occupid_reuse_shared_split_groups():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=8, port=0)],
    )
    cycle = Event(
        name="SYS_CMN_CYCLES",
        title="",
        description="",
        cmn_index=0,
        type=3,
        eventid=None,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )
    miss_occ_1 = Event(
        name="PMU_HNS_CACHE_MISS",
        title="",
        description="",
        cmn_index=0,
        type=8,
        eventid=1,
        occupid=1,
        nodeid=None,
        xp_id=None,
    )
    miss_occ_2 = Event(
        name="PMU_HNS_CACHE_MISS",
        title="",
        description="",
        cmn_index=0,
        type=8,
        eventid=1,
        occupid=2,
        nodeid=None,
        xp_id=None,
    )
    access = Event(
        name="PMU_HNS_SLCSF_CACHE_ACCESS",
        title="",
        description="",
        cmn_index=0,
        type=8,
        eventid=2,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )

    metric_a = (cycle, miss_occ_1, access)
    metric_b = (cycle, miss_occ_2, access)

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()
    noncycle_group_sets = [
        frozenset(ev.key() for ev in group if not ev.is_cycle()) for group in groups
    ]

    assert noncycle_group_sets.count(frozenset({miss_occ_1.key()})) == 1
    assert noncycle_group_sets.count(frozenset({miss_occ_2.key()})) == 1
    assert noncycle_group_sets.count(frozenset({access.key()})) == 1
    assert len(groups) == 3

    values = {
        cycle.key(): 1000.0,
        miss_occ_1.key(): 10.0,
        miss_occ_2.key(): 30.0,
        access.key(): 20.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == (1000.0, 10.0, 20.0)
    assert sched.retrieve_metric_result(perf_result, metric_b) == (1000.0, 30.0, 20.0)


def test_duplicate_global_mixed_metrics_reuse_shared_split_groups():
    ports = [WatchpointPort(xp_id=0, port=1)]
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=n, node_type=9, port=0) for n in range(MAX_EVENTS_PER_XP)],
        watchpoint_ports_by_device={"DEV": ports},
    )
    miss_occ_1 = glob_ev("E1", event_type=9, occupid=1)
    miss_occ_2 = glob_ev("E1", event_type=9, occupid=2)
    shared_wp = global_wp("DEV", direction="UP", value=5)
    metric_a = (miss_occ_1, shared_wp)
    metric_b = (miss_occ_2, shared_wp)

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()
    group_sets = [frozenset(ev.key() for ev in group) for group in groups]

    assert group_sets.count(frozenset({miss_occ_1.key()})) == 1
    assert group_sets.count(frozenset({miss_occ_2.key()})) == 1
    assert group_sets.count(frozenset({shared_wp.key()})) == 1
    assert len(groups) == 3

    values = {
        miss_occ_1.key(): 11.0,
        miss_occ_2.key(): 22.0,
        shared_wp.key(): 33.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == (11.0, 33.0)
    assert sched.retrieve_metric_result(perf_result, metric_b) == (22.0, 33.0)


def test_duplicate_global_watchpoint_metrics_reuse_shared_split_groups():
    ports = [WatchpointPort(xp_id=0, port=1)]
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[],
        watchpoint_ports_by_device={"DEV": ports},
    )
    wp1 = global_wp("DEV", direction="UP", value=1)
    wp2 = global_wp("DEV", direction="UP", value=2)
    wp3 = global_wp("DEV", direction="UP", value=3)
    wp4 = global_wp("DEV", direction="UP", value=4)
    wp5 = global_wp("DEV", direction="UP", value=5)
    metric_a = (wp1, wp2, wp3)
    metric_b = (wp3, wp4, wp5)

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()
    group_sets = [frozenset(ev.key() for ev in group) for group in groups]

    for wp_ev in (wp1, wp2, wp3, wp4, wp5):
        assert group_sets.count(frozenset({wp_ev.key()})) == 1
    assert len(groups) == 5

    values = {
        wp1.key(): 1.0,
        wp2.key(): 2.0,
        wp3.key(): 3.0,
        wp4.key(): 4.0,
        wp5.key(): 5.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == (1.0, 2.0, 3.0)
    assert sched.retrieve_metric_result(perf_result, metric_b) == (3.0, 4.0, 5.0)


def test_duplicate_local_large_metrics_reuse_shared_split_groups():
    cmn = make_cmn_info(
        dtc_count=1,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=10, port=0)],
    )
    shared = tuple(ev(f"E{i}", 0, 0, event_type=10) for i in range(1, 5))
    e5 = ev("E5", 0, 0, event_type=10)
    e6 = ev("E6", 0, 0, event_type=10)
    metric_a = shared + (e5,)
    metric_b = shared + (e6,)

    sched = CmnScheduler([metric_a, metric_b], cmn)
    groups = sched.get_optimized_event_groups()
    group_sets = [frozenset(ev.key() for ev in group) for group in groups]

    for local_ev in shared + (e5, e6):
        assert group_sets.count(frozenset({local_ev.key()})) == 1
    assert len(groups) == 6

    values = {local_ev.key(): float(idx) for idx, local_ev in enumerate(shared + (e5, e6), start=1)}
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert sched.retrieve_metric_result(perf_result, metric_a) == tuple(values[event.key()] for event in metric_a)
    assert sched.retrieve_metric_result(perf_result, metric_b) == tuple(values[event.key()] for event in metric_b)
