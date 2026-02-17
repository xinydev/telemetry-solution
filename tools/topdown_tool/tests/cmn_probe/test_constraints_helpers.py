# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from topdown_tool.cmn_probe.common import Event as CmnEvent
from topdown_tool.cmn_probe.scheduler import (
    violates_global_constraints,
    violates_local_constraints,
    MAX_EVENTS_PER_NODE,
    MAX_EVENTS_PER_DTC,
    MAX_WATCHPOINTS_PER_DIRECTION,
    CmnInfo,
    NodeEntry,
)
from .helpers import (
    ev,
    glob_ev,
    wp,
)


def build_events_by_type(event_type_to_ids):
    # event_type_to_ids: {int: [str, ...]}
    result = {}
    for t, ids in event_type_to_ids.items():
        result[t] = set(f"{t}:{eid}" for eid in ids)
    return result


def dummy_cmninfo(extra_nodes=None):
    # DTC 0/1, each has one XP, each XP has two nodes, each node type 0 or 1 (unless extra_nodes are passed for custom scenarios)
    nodes = [
        NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
        NodeEntry(dtc=0, xp=0, node=1, node_type=1, port=1),
        NodeEntry(dtc=1, xp=1, node=2, node_type=0, port=2),
        NodeEntry(dtc=1, xp=1, node=3, node_type=1, port=3),
    ]
    if extra_nodes:
        nodes.extend(extra_nodes)
    return CmnInfo(dtc_count=2, dtc_of=lambda xp: xp, nodes=nodes)


def test_global_node_constraint_boundary():
    ci = dummy_cmninfo()
    # At limit: legal
    events = [glob_ev(f"E{i}") for i in range(MAX_EVENTS_PER_NODE)]
    assert not violates_global_constraints(events, ci)
    # Over limit: illegal
    events = [glob_ev(f"E{i}") for i in range(MAX_EVENTS_PER_NODE + 1)]
    assert violates_global_constraints(events, ci)


def test_global_xp_constraint_boundary():
    # At limit: 2 nodes of type 0, 2 events: 2*2 = 4 (legal; MAX_EVENTS_PER_XP)
    extra_nodes = [NodeEntry(dtc=0, xp=0, node=4, node_type=0, port=4)]  # XP0: nodes 0, 4 of type 0
    ci = dummy_cmninfo(extra_nodes)
    events = [glob_ev(f"E{i}") for i in range(2)]
    assert not violates_global_constraints(events, ci)
    # Over limit: 2 nodes of type 0, 3 events: 2*3 = 6 (>4, illegal)
    events = [glob_ev(f"E{i}") for i in range(3)]
    assert violates_global_constraints(events, ci)


def test_global_xp_constraint_uses_linux_aliased_node_types():
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0x000A, port=0),
            NodeEntry(dtc=0, xp=0, node=1, node_type=0x000D, port=1),
        ],
        global_type_aliases=dict(CmnEvent.LINUX_FIX_MAP),
    )
    events = [glob_ev(f"E{i}", event_type=0x000A) for i in range(3)]
    assert violates_global_constraints(events, ci)


def test_global_dtc_constraint_boundary():
    # Use only event_types >= 10 to avoid cycle lookup (event_type=3)
    type_base = 10
    n_types = MAX_EVENTS_PER_DTC
    # Map each node/type to its own XP to never hit the XP constraint before the DTC constraint
    nodes = [NodeEntry(dtc=0, xp=xp, node=0, node_type=type_base + xp, port=0) for xp in range(n_types)]
    ci = CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=nodes)
    # At the DTC limit: legal
    events = [glob_ev("E1", event_type=type_base + xp) for xp in range(n_types)]
    assert not violates_global_constraints(events, ci)
    # Over the DTC limit: add one more node/type/xp
    nodes_over = [
        NodeEntry(dtc=0, xp=xp, node=0, node_type=type_base + xp, port=0)
        for xp in range(n_types + 1)
    ]
    ci_over = CmnInfo(dtc_count=1, dtc_of=lambda xp: 0, nodes=nodes_over)
    events_over = [glob_ev("E1", event_type=type_base + xp) for xp in range(n_types + 1)]
    assert violates_global_constraints(events_over, ci_over)


def test_global_no_violation():
    ci = dummy_cmninfo()
    events = [glob_ev("E1", event_type=0), glob_ev("E2", event_type=1)]
    assert not violates_global_constraints(events, ci)


# -------- local constraints --------


def test_local_node_constraint_boundary():
    ci = dummy_cmninfo()
    # At node limit: legal
    events = [ev(f"E{i}", 0) for i in range(MAX_EVENTS_PER_NODE)]
    assert not violates_local_constraints(events, ci)
    # Over node limit: illegal
    events = [ev(f"E{i}", 0) for i in range(MAX_EVENTS_PER_NODE + 1)]
    assert violates_local_constraints(events, ci)


def test_local_xp_constraint_boundary():
    # Test XP constraint: combine events for two different nodes on XP0 so sum triggers violation over MAX_EVENTS_PER_XP
    extra_nodes = [NodeEntry(dtc=0, xp=0, node=5, node_type=1, port=5)]  # XP0: nodes 1 and 5 of type 1
    ci = dummy_cmninfo(extra_nodes)
    # At the limit: 2 events for node 1, 2 for node 5 => total 4 on XP0 (legal)
    events = [
        ev("E0", 0, 1, event_type=1),  # node 1, XP0
        ev("E1", 0, 1, event_type=1),  # node 1, XP0
        ev("E2", 0, 5, event_type=1),  # node 5, XP0
        ev("E3", 0, 5, event_type=1),  # node 5, XP0
    ]
    assert not violates_local_constraints(events, ci)
    # Over the edge: add one more event targeting XP0 (node 1), now sum = 5
    events.append(ev("E4", 0, 1, event_type=1))
    assert violates_local_constraints(events, ci)


def test_local_dtc_constraint_boundary():
    dtc_count = 1
    n_events = MAX_EVENTS_PER_DTC
    # Each event on a distinct XP under the same DTC (so that XP constraint is not first limiting)
    nodes = [NodeEntry(dtc=0, xp=xp, node=xp, node_type=0, port=xp) for xp in range(n_events)]
    ci = CmnInfo(dtc_count=dtc_count, dtc_of=lambda xp: 0, nodes=nodes)
    # At DTC limit: legal
    events = [ev(f"E{i}", xp, xp, event_type=0) for i, xp in enumerate(range(n_events))]
    assert not violates_local_constraints(events, ci)
    # Over DTC limit: one extra XP/node/event
    nodes_over = [
        NodeEntry(dtc=0, xp=xp, node=xp, node_type=0, port=xp)
        for xp in range(n_events + 1)
    ]
    ci_over = CmnInfo(dtc_count=dtc_count, dtc_of=lambda xp: 0, nodes=nodes_over)
    events_over = [ev(f"E{i}", xp, xp, event_type=0) for i, xp in enumerate(range(n_events + 1))]
    assert violates_local_constraints(events_over, ci_over)


def test_local_no_violation():
    ci = dummy_cmninfo()
    events = [ev("E1", 0), ev("E2", 1, event_type=1)]
    assert not violates_local_constraints(events, ci)


def test_watchpoint_xp_constraints():
    # At max 4 per XP: legal
    events = [wp(xp_id=0, port=i, direction="UP") for i in range(MAX_WATCHPOINTS_PER_DIRECTION)]
    events += [
        wp(xp_id=0, port=10 + i, direction="DOWN") for i in range(MAX_WATCHPOINTS_PER_DIRECTION)
    ]
    ci = dummy_cmninfo()
    assert not violates_local_constraints(events, ci)
    # Over max per XP: illegal (5 total)
    events5 = events + [wp(xp_id=0, port=20, direction="UP")]
    assert violates_local_constraints(events5, ci)


def test_watchpoint_per_direction_constraints():
    # 2 UP, 2 DOWN on XP0: legal
    events = [
        wp(xp_id=0, port=0, direction="UP"),
        wp(xp_id=0, port=1, direction="UP"),
        wp(xp_id=0, port=2, direction="DOWN"),
        wp(xp_id=0, port=3, direction="DOWN"),
    ]
    ci = dummy_cmninfo()
    assert not violates_local_constraints(events, ci)
    # 3 UP, 1 DOWN: illegal
    events_bad = [
        wp(xp_id=0, port=0, direction="UP"),
        wp(xp_id=0, port=1, direction="UP"),
        wp(xp_id=0, port=2, direction="UP"),
        wp(xp_id=0, port=3, direction="DOWN"),
    ]
    assert violates_local_constraints(events_bad, ci)
    # 1 UP, 3 DOWN: illegal
    events_bad2 = [
        wp(xp_id=0, port=0, direction="UP"),
        wp(xp_id=0, port=1, direction="DOWN"),
        wp(xp_id=0, port=2, direction="DOWN"),
        wp(xp_id=0, port=3, direction="DOWN"),
    ]
    assert violates_local_constraints(events_bad2, ci)


def test_watchpoint_dtc_sum_mixed():
    # Mix device and watchpoint: total slot count = MAX_EVENTS_PER_DTC should be legal
    ci = dummy_cmninfo()
    num_wp = MAX_EVENTS_PER_DTC // 2
    num_dev = MAX_EVENTS_PER_DTC - num_wp
    # Place all events (device and WP) on a single DTC (dtc_of=lambda xp: 0)
    # Use unique xp_id for each event but dtc_of forces all to the same dtc
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[NodeEntry(dtc=0, xp=i, node=i, node_type=0, port=i) for i in range(num_dev)],
    )
    # Each device event: unique xp and node
    events = [ev(f"E{i}", i, i) for i in range(num_dev)]
    # Each WP: unique xp_id as well, same dtc as device events
    events += [wp(xp_id=i, port=10, direction="DOWN") for i in range(num_wp)]
    assert not violates_local_constraints(events, ci)
    # One extra WP: triggers DTC overflow on DTC 0
    events2 = events + [wp(xp_id=num_wp + 1, port=10, direction="UP")]
    assert violates_local_constraints(events2, ci)
