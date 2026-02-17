# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

"""
Tests for MultiCmnScheduler orchestration/proxy integration.
============================================================

Covers:
- API equivalency with CmnScheduler (flat event group output, tuple-keyed perf_result)
- Multi-CMN routing and optimization partitioning
- Duplicate event and value retrieval in input/output
- perf_name and original methods preserved in optimized groups
- Error cases: cross-CMN tuple, missing perf_result group, tuple not registered
"""

import pytest
from topdown_tool.cmn_probe.common import (
    Event as RealDeviceEvent,
    Watchpoint as RealWatchpointEvent,
)
from topdown_tool.cmn_probe.multi_cmn_scheduler import MultiCmnScheduler
from topdown_tool.cmn_probe.scheduler import CmnInfo, NodeEntry


def minimal_real_dev(
    name, cmn_index, type=0, eventid=None, occupid=None, nodeid=None, xp_id=None
):
    return RealDeviceEvent(
        name=name,
        title="t",
        description="d",
        cmn_index=cmn_index,
        type=type,
        eventid=eventid,
        occupid=occupid,
        nodeid=nodeid,
        xp_id=xp_id,
    )


def test_real_event_xp_id_affects_equality() -> None:
    ev0 = RealDeviceEvent(
        name="a",
        title="t",
        description="d",
        cmn_index=0,
        type=0,
        eventid=1,
        occupid=None,
        nodeid=1,
        xp_id=2,
    )
    ev1 = RealDeviceEvent(
        name="a",
        title="t",
        description="d",
        cmn_index=0,
        type=0,
        eventid=1,
        occupid=None,
        nodeid=1,
        xp_id=3,
    )
    assert ev0 != ev1


def minimal_real_wp(
    name,
    cmn_index,
    mesh_flit_dir,
    xp_id,
    port,
    wp_chn_sel=0,
    wp_grp=0,
    wp_mask=1,
    wp_val=42,
    device=None,
):
    return RealWatchpointEvent(
        name=name,
        title="t",
        description="d",
        cmn_index=cmn_index,
        mesh_flit_dir=mesh_flit_dir,
        wp_chn_sel=wp_chn_sel,
        wp_grp=wp_grp,
        wp_mask=wp_mask,
        wp_val=wp_val,
        xp_id=xp_id,
        port=port,
        device=device,
    )


def make_cmn(index, dtc_count, nodes):
    # nodes: list of (dtc, xp, node, node_type, port)
    return index, CmnInfo(dtc_count=dtc_count, dtc_of=lambda n: n % dtc_count, nodes=nodes)


def build_perf_result(groups):
    """Assigns a dummy count value as the result for each group (for round-trip validation)."""
    result = {}
    for group in groups:
        vals = tuple(i for i, _ in enumerate(group))
        result[group] = vals
    return result


def test_flat_equivalence_to_cmn_scheduler_simple():
    # Single CMN, tuple of two events on SAME node -> same xp_id and same node_id!
    nodes = [NodeEntry(dtc=0, xp=1, node=1, node_type=1, port=1)]  # Only node 1 is defined
    cmn_idx, ci = make_cmn(0, 1, nodes)
    e1 = minimal_real_dev("a", cmn_index=0, type=5, eventid=4, nodeid=1, xp_id=1)
    e2 = minimal_real_dev("b", cmn_index=0, type=5, eventid=5, nodeid=1, xp_id=1)
    e_tuple = (e1, e2)
    ms = MultiCmnScheduler([e_tuple], {cmn_idx: ci})
    groups = ms.get_optimized_event_groups()
    assert len(groups) == 1
    assert groups[0][0] == e1
    assert groups[0][1] == e2
    assert groups[0][0].perf_name().startswith("arm_cmn_0/")
    perf_result = build_perf_result(groups)
    result = ms.retrieve_metric_result(perf_result, e_tuple)
    assert result == (0, 1)


def test_multiple_cmn_buckets_and_routing():
    # Two CMNs, should return union of optimized groups, retrieval restricted to relevant group keys
    nodes0 = [NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)]
    nodes1 = [NodeEntry(dtc=0, xp=1, node=1, node_type=1, port=1)]
    cmn0, ci0 = make_cmn(0, 1, nodes0)
    cmn1, ci1 = make_cmn(1, 1, nodes1)
    e0 = minimal_real_dev("x", 0, eventid=10, nodeid=0, xp_id=0)
    e1 = minimal_real_dev("y", 1, eventid=11, nodeid=1, xp_id=1)
    t0, t1 = (e0,), (e1,)
    ms = MultiCmnScheduler([t0, t1], {cmn0: ci0, cmn1: ci1})
    groups = ms.get_optimized_event_groups()
    group0 = [g for g in groups if g[0] == e0][0]
    group1 = [g for g in groups if g[0] == e1][0]
    perf_result0 = {group0: (0,)}
    perf_result1 = {group1: (0,)}
    assert ms.retrieve_metric_result(perf_result0, t0) == (0,)
    assert ms.retrieve_metric_result(perf_result1, t1) == (0,)


def test_duplicate_events_preserved_in_input_and_output():
    # The group should be deduped (1 event), but retrieval of (ev, ev, ev) yields 3 results.
    node = NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)
    cmn, ci = make_cmn(0, 1, [node])
    ev = minimal_real_dev("dup", 0, eventid=1, nodeid=0, xp_id=0)
    t_dup = (ev, ev, ev)
    ms = MultiCmnScheduler([t_dup], {cmn: ci})
    groups = ms.get_optimized_event_groups()
    assert any(g.count(ev) == 1 for g in groups)
    perf_result = build_perf_result(groups)
    result = ms.retrieve_metric_result(perf_result, t_dup)
    assert result == (0, 0, 0)


def test_duplicate_identical_metrics_with_mixed_occupid_retrieve_consistently():
    node = NodeEntry(dtc=0, xp=0, node=0, node_type=6, port=0)
    cmn, ci = make_cmn(0, 1, [node])
    metric_a = (
        minimal_real_dev("a0", 0, type=6, eventid=1, occupid=None, nodeid=0, xp_id=0),
        minimal_real_dev("a1", 0, type=6, eventid=2, occupid=50, nodeid=0, xp_id=0),
    )
    metric_b = (
        minimal_real_dev("b0", 0, type=6, eventid=1, occupid=None, nodeid=0, xp_id=0),
        minimal_real_dev("b1", 0, type=6, eventid=2, occupid=50, nodeid=0, xp_id=0),
    )

    ms = MultiCmnScheduler([metric_a, metric_b], {cmn: ci})
    groups = ms.get_optimized_event_groups()

    assert len(groups) == 2
    values = {
        metric_a[0].key(): 101.0,
        metric_a[1].key(): 202.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert ms.retrieve_metric_result(perf_result, metric_a) == (101.0, 202.0)
    assert ms.retrieve_metric_result(perf_result, metric_b) == (101.0, 202.0)


def test_duplicate_identical_global_metrics_with_mixed_occupid_retrieve_consistently():
    node = NodeEntry(dtc=0, xp=0, node=0, node_type=7, port=0)
    cmn, ci = make_cmn(0, 1, [node])
    metric_a = (
        minimal_real_dev("a0", 0, type=7, eventid=1, occupid=None, nodeid=None, xp_id=None),
        minimal_real_dev("a1", 0, type=7, eventid=2, occupid=50, nodeid=None, xp_id=None),
    )
    metric_b = (
        minimal_real_dev("b0", 0, type=7, eventid=1, occupid=None, nodeid=None, xp_id=None),
        minimal_real_dev("b1", 0, type=7, eventid=2, occupid=50, nodeid=None, xp_id=None),
    )

    ms = MultiCmnScheduler([metric_a, metric_b], {cmn: ci})
    groups = ms.get_optimized_event_groups()

    assert len(groups) == 2
    values = {
        metric_a[0].key(): 303.0,
        metric_a[1].key(): 404.0,
    }
    perf_result = {group: tuple(values[event.key()] for event in group) for group in groups}

    assert ms.retrieve_metric_result(perf_result, metric_a) == (303.0, 404.0)
    assert ms.retrieve_metric_result(perf_result, metric_b) == (303.0, 404.0)


def test_watchpoint_event_routed_and_perf_name():
    node = NodeEntry(dtc=0, xp=7, node=7, node_type=2, port=7)
    cmn, ci = make_cmn(0, 1, [node])
    wp = minimal_real_wp("w", 0, mesh_flit_dir=2, xp_id=7, port=42, wp_chn_sel=2)
    t = (wp,)
    ms = MultiCmnScheduler([t], {cmn: ci})
    groups = ms.get_optimized_event_groups()
    assert len(groups) == 1 and groups[0] == (wp,)
    assert "watchpoint_down" in wp.perf_name()
    perf_result = build_perf_result(groups)
    assert ms.retrieve_metric_result(perf_result, t) == (0,)


def test_cross_cmn_tuple_raises_valueerror():
    node0 = NodeEntry(dtc=0, xp=1, node=5, node_type=1, port=5)
    node1 = NodeEntry(dtc=0, xp=7, node=13, node_type=1, port=13)
    cmn0, ci0 = make_cmn(0, 1, [node0])
    cmn1, ci1 = make_cmn(1, 1, [node1])
    e0 = minimal_real_dev("e0", 0, eventid=2, nodeid=5, xp_id=1)
    e1 = minimal_real_dev("e1", 1, eventid=3, nodeid=13, xp_id=7)
    with pytest.raises(ValueError):
        MultiCmnScheduler([(e0, e1)], {0: ci0, 1: ci1})


def test_missing_perf_result_group_key_raises_keyerror():
    node = NodeEntry(dtc=0, xp=3, node=3, node_type=1, port=3)
    cmn, ci = make_cmn(0, 1, [node])
    e = minimal_real_dev("a", 0, eventid=3, nodeid=3, xp_id=3)
    ms = MultiCmnScheduler([(e,)], {cmn: ci})
    perf_result = {}  # simulate missing results
    with pytest.raises(KeyError):
        ms.retrieve_metric_result(perf_result, (e,))


def test_metric_tuple_not_registered_raises_keyerror():
    node = NodeEntry(dtc=0, xp=2, node=2, node_type=1, port=2)
    cmn, ci = make_cmn(0, 1, [node])
    e = minimal_real_dev("g", 0, eventid=4, nodeid=2, xp_id=2)
    ms = MultiCmnScheduler([(e,)], {cmn: ci})
    fake_e = minimal_real_dev("g", 0, eventid=4, nodeid=4, xp_id=2)
    # Should raise as this is a distinct object, not among input tuples
    with pytest.raises(KeyError):
        ms.retrieve_metric_result({(e,): (0,)}, (fake_e,))


def test_combined_device_and_watchpoint_multiple_groups():
    nodes0 = [NodeEntry(dtc=0, xp=1, node=1, node_type=1, port=1)]
    nodes1 = [NodeEntry(dtc=0, xp=2, node=2, node_type=1, port=2)]
    cmn0, ci0 = make_cmn(0, 1, nodes0)
    cmn1, ci1 = make_cmn(1, 1, nodes1)
    dev = minimal_real_dev("a", 0, eventid=3, nodeid=1, xp_id=1)
    wp = minimal_real_wp("w", 1, mesh_flit_dir=0, xp_id=2, port=8)
    t_dev = (dev,)
    t_wp = (wp,)
    ms = MultiCmnScheduler([t_dev, t_wp], {cmn0: ci0, cmn1: ci1})
    groups = ms.get_optimized_event_groups()
    group0 = [
        g for g in groups if any(isinstance(x, RealDeviceEvent) and x.cmn_index == 0 for x in g)
    ][0]
    group1 = [
        g for g in groups if any(isinstance(x, RealWatchpointEvent) and x.cmn_index == 1 for x in g)
    ][0]
    pr0 = {group0: (0,)}
    pr1 = {group1: (0,)}
    assert ms.retrieve_metric_result(pr0, t_dev) == (0,)
    assert ms.retrieve_metric_result(pr1, t_wp) == (0,)


def test_large_duplicate_stress():
    # N unique events by varying eventid, so each group is deduped, but retrieval is checked.
    N = 50
    node = [NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)]
    cmn, ci = make_cmn(0, 1, node)
    events = tuple(minimal_real_dev("bulk", 0, eventid=i, nodeid=0, xp_id=0) for i in range(N))
    ms = MultiCmnScheduler([events], {cmn: ci})
    groups = ms.get_optimized_event_groups()
    found = set()
    for g in groups:
        for event in g:
            found.add(event.eventid)
    assert len(found) == N
    # Build perf_result mapping each event to its eventid for precise output
    perf_result = {}
    for group in groups:
        vals = tuple(event.eventid for event in group)
        perf_result[group] = vals
    out = ms.retrieve_metric_result(perf_result, events)
    assert out == tuple(ev.eventid for ev in events)


def test_event_occupid_none_equals_zero():
    e_none = minimal_real_dev("a", 0, type=5, eventid=7, nodeid=1, occupid=None, xp_id=1)
    e_zero = minimal_real_dev("a", 0, type=5, eventid=7, nodeid=1, occupid=0, xp_id=1)
    assert e_none == e_zero
    assert hash(e_none) == hash(e_zero)
    assert not (e_none < e_zero)
    assert not (e_zero < e_none)


def test_event_metadata_ignored_in_equality():
    ev = minimal_real_dev("a", cmn_index=0, type=5, eventid=7, nodeid=1, xp_id=1)
    alt = RealDeviceEvent(
        name="b",
        title="other",
        description="diff",
        cmn_index=0,
        type=5,
        eventid=7,
        occupid=None,
        nodeid=1,
        xp_id=1,
    )
    assert ev == alt
    assert hash(ev) == hash(alt)
    other_cmn = minimal_real_dev("a", cmn_index=1, type=5, eventid=7, nodeid=1, xp_id=1)
    assert ev != other_cmn


def test_watchpoint_metadata_ignored_in_equality():
    wp = minimal_real_wp("w", 0, mesh_flit_dir=2, xp_id=7, port=42, wp_chn_sel=2)
    alt = RealWatchpointEvent(
        name="other",
        title="other",
        description="diff",
        cmn_index=0,
        mesh_flit_dir=2,
        wp_chn_sel=2,
        wp_grp=0,
        wp_mask=1,
        wp_val=42,
        xp_id=7,
        port=42,
        device=None,
    )
    assert wp == alt
    assert hash(wp) == hash(alt)
    other_cmn = minimal_real_wp("w", 1, mesh_flit_dir=2, xp_id=7, port=42, wp_chn_sel=2)
    assert wp != other_cmn


def test_watchpoint_normalization_and_validation():
    wp = minimal_real_wp("w", 0, mesh_flit_dir=2, xp_id=7, port=42, wp_mask=-1, wp_val=-2)
    assert wp.wp_mask == 2**64 - 1
    assert wp.wp_val == 2**64 - 2
    with pytest.raises(ValueError):
        minimal_real_wp("bad", 0, mesh_flit_dir=1, xp_id=7, port=42)


def test_retrieval_distinguishes_cmn_instances():
    """
    When two CMN fabrics host identical logical events (same xp/node/eventid) we must
    still retrieve the value associated with the correct fabric.  This test ensures
    that `MultiCmnScheduler.retrieve_metric_result` filters `perf_result` by
    `cmn_index`, avoiding cross-fabric collisions.
    """
    node_desc = NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)
    cmn0_idx, ci0 = make_cmn(0, 1, [node_desc])
    cmn1_idx, ci1 = make_cmn(1, 1, [node_desc])

    ev0 = minimal_real_dev("dev", cmn_index=0, eventid=1, nodeid=0, xp_id=0)
    ev1 = minimal_real_dev("dev", cmn_index=1, eventid=1, nodeid=0, xp_id=0)

    t0, t1 = (ev0,), (ev1,)

    ms = MultiCmnScheduler([t0, t1], {cmn0_idx: ci0, cmn1_idx: ci1})
    groups = ms.get_optimized_event_groups()

    # Identify which optimized group belongs to which CMN
    group0 = next(g for g in groups if g[0].cmn_index == 0)
    group1 = next(g for g in groups if g[0].cmn_index == 1)

    # Provide distinct values for each CMN's event
    perf_result = {
        group0: (100,),
        group1: (200,),
    }

    assert ms.retrieve_metric_result(perf_result, t0) == (100,)
    assert ms.retrieve_metric_result(perf_result, t1) == (200,)


def test_prepared_perf_result_fast_path():
    """
    Verify that prepare_perf_result converts the raw mapping once and that
    retrieve_metric_result_prepared returns the correct values for each CMN.
    """
    nodes0 = [NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)]
    nodes1 = [NodeEntry(dtc=0, xp=0, node=0, node_type=1, port=0)]
    idx0, ci0 = make_cmn(0, 1, nodes0)
    idx1, ci1 = make_cmn(1, 1, nodes1)

    ev0 = minimal_real_dev("d0", cmn_index=0, eventid=7, nodeid=0, xp_id=0)
    ev1 = minimal_real_dev("d1", cmn_index=1, eventid=7, nodeid=0, xp_id=0)

    t0, t1 = (ev0,), (ev1,)

    ms = MultiCmnScheduler([t0, t1], {idx0: ci0, idx1: ci1})
    groups = ms.get_optimized_event_groups()
    g0 = next(g for g in groups if g[0] == ev0)
    g1 = next(g for g in groups if g[0] == ev1)

    raw_perf = {g0: (123,), g1: (456,)}
    prepared = ms.prepare_perf_result(raw_perf)

    assert ms.retrieve_metric_result_prepared(prepared, t0) == (123,)
    assert ms.retrieve_metric_result_prepared(prepared, t1) == (456,)
