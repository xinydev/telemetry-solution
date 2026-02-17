# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from topdown_tool.cmn_probe.common import Event
from topdown_tool.cmn_probe.scheduler import (
    _GGroup,
    _is_small_global_tuple,
    _TupleReq,
    CmnInfo,
    NodeEntry,
    cycle_event,
)


def glob_event(event_type: int, event_id: int, *, cmn_index: int = 0) -> Event:
    return Event(
        name=f"E{event_id}",
        title="",
        description="",
        cmn_index=cmn_index,
        type=event_type,
        eventid=event_id,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )


def test_ggroupx_basic_helpers_and_freeze():
    # Start empty
    grp = _GGroup()
    assert grp.k_total() == 0
    assert grp.size() == 0
    # Add two real Event objects via add, different types
    ev0 = glob_event(0, 1)
    ev1 = glob_event(1, 2)
    grp.add({ev0.type: {ev0.key()}})
    grp.add({ev1.type: {ev1.key()}})
    assert grp.k_t(0) == 1
    assert grp.k_t(1) == 1
    assert grp.k_total() == 2
    # Add cycle with method
    grp.add({}, cycle_key=cycle_event(cmn_index=0).key())
    frozen = grp.freeze()
    # cycle is first
    assert frozen[0] == cycle_event(cmn_index=0)
    # remaining events, sorted order by canonical key
    keys = [e.key() for e in frozen]
    assert keys[0].startswith("cycle@I0")
    assert set(keys[1:]) == {ev0.key(), ev1.key()}
    # Remove cycle and freeze again
    grp.cycle_key = None
    frozen2 = grp.freeze()
    keys2 = [e.key() for e in frozen2]
    assert not any(k.startswith("cycle@") for k in keys2) and set(keys2) == {ev0.key(), ev1.key()}


def test_ggroupx_can_accept_two_type_dtc_capacity():
    # 4 events of type 0 and 4 of type 1 can fit; a 5th event of a new type fails DTC constraint.
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
            NodeEntry(dtc=0, xp=0, node=1, node_type=1, port=1),
        ],
    )
    grp = _GGroup()
    for i in range(4):
        grp.add({0: {glob_event(0, i).key()}})
    for i in range(4):
        grp.add({1: {glob_event(1, i).key()}})
    # Should be at max DTC-wide count
    assert grp.can_accept({2: {glob_event(2, 0).key()}}, ci) is False


def test_ggroupx_can_accept_xp_event_overflow():
    # Try packing 5 events of the same type with XP hosting one node of type 0 — should block at XP slot constraint
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0)],
    )
    grp = _GGroup()
    for i in range(4):
        grp.add({0: {glob_event(0, i).key()}})
    assert grp.can_accept({0: {glob_event(0, 4).key()}}, ci) is False


def test_ggroupx_can_accept_per_xp_constraint_multiple_nodes():
    # 2 nodes of type 0 on XP0: so only 2 events allowed. Adding 3rd overfills
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
            NodeEntry(dtc=0, xp=0, node=1, node_type=0, port=1),
        ],
    )
    grp = _GGroup()
    # 2 event types is max slot (2 × 2 = 4)
    for i in range(2):
        grp.add({0: {glob_event(0, i).key()}})
    assert grp.can_accept({0: {glob_event(0, 2).key()}}, ci) is False


def test_ggroupx_multi_dtc_cross_type():
    # 2 DTCs: DTC 0 has node types 0, 1; DTC 1 has type 2.
    # Fill up type 0, 1, 2 to max (4 per DTC), adding more should fail.
    nodes = [
        NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),  # DTC 0, XP 0, node 0, type 0
        NodeEntry(dtc=0, xp=1, node=1, node_type=1, port=1),  # DTC 0, XP 1, node 1, type 1
        NodeEntry(dtc=1, xp=2, node=2, node_type=2, port=2),  # DTC 1, XP 2, node 2, type 2
    ]
    ci = CmnInfo(dtc_count=2, dtc_of=lambda xp: 0 if xp in [0, 1] else 1, nodes=nodes)
    grp = _GGroup()
    # Fill up 4 events for each type (should fit for each DTC)
    for i in range(4):
        assert grp.can_accept({0: {glob_event(0, i).key()}}, ci)
        grp.add({0: {glob_event(0, i).key()}})
    for i in range(4):
        assert grp.can_accept({1: {glob_event(1, i).key()}}, ci)
        grp.add({1: {glob_event(1, i).key()}})
    for i in range(4):
        assert grp.can_accept({2: {glob_event(2, i).key()}}, ci)
        grp.add({2: {glob_event(2, i).key()}})
    # Adding a 5th event for any type should now fail due to per-DTC/group constraints.
    assert not grp.can_accept({0: {glob_event(0, 4).key()}}, ci)
    assert not grp.can_accept({1: {glob_event(1, 4).key()}}, ci)
    assert not grp.can_accept({2: {glob_event(2, 4).key()}}, ci)


def test_is_small_global_tuple_fits_and_fails():
    # Arrange a tuple with ≤ slots per DTC/type/node: should fit.
    ci = CmnInfo(
        dtc_count=1,
        dtc_of=lambda xp: 0,
        nodes=[
            NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0),
            NodeEntry(dtc=0, xp=0, node=1, node_type=1, port=1),
        ],
    )
    events = (
        glob_event(0, 1),
        glob_event(0, 2),
        glob_event(1, 3),
    )
    tup = _TupleReq(1, events)
    assert _is_small_global_tuple(tup, ci)
    # Tuple with 5 events of same type overfills DTC constraint
    events2 = tuple(glob_event(0, i) for i in range(9))
    tup2 = _TupleReq(2, events2)
    assert not _is_small_global_tuple(tup2, ci)
    # Tuple with cycle event and ≤ constraints
    events3 = (
        glob_event(0, 1),
        glob_event(1, 3),
        cycle_event(cmn_index=0),
    )
    tup3 = _TupleReq(3, events3)
    assert _is_small_global_tuple(tup3, ci)
