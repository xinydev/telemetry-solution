# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from topdown_tool.cmn_probe.cmn_database import CmnDatabase
from topdown_tool.cmn_probe.common import CmnLocation, Event, Watchpoint, XpLocation


def _make_event(name: str, cmn_index: int = 0, eventid: int = 1) -> Event:
    return Event(
        name=name,
        title=name,
        description="desc",
        cmn_index=cmn_index,
        type=0,
        eventid=eventid,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )


def _make_watchpoint(
    name: str, cmn_index: int = 0, xp_id: int = 1, port: int = 2
) -> Watchpoint:
    return Watchpoint(
        name=name,
        title=name,
        description="desc",
        cmn_index=cmn_index,
        mesh_flit_dir=0,
        wp_chn_sel=1,
        wp_grp=2,
        wp_mask=0,
        wp_val=0,
        xp_id=xp_id,
        port=port,
        device=None,
    )


def test_parse_hex_int_signed() -> None:
    assert CmnDatabase.parse_hex_int("0x7fffffffffffffff") == 2**63 - 1
    assert CmnDatabase.parse_hex_int("0x8000000000000000") == -(2**63)
    assert CmnDatabase.parse_hex_int("0xffffffffffffffff") == -1


def test_merge_events_extends_destinations() -> None:
    loc_global = CmnLocation(0)
    loc_xp = XpLocation(0, 1)
    event1 = _make_event("E1")
    event2 = _make_event("E2")
    event3 = _make_event("E3")

    dest = {loc_global: [event1]}
    source = {loc_global: [event2], loc_xp: [event3]}

    CmnDatabase.merge_events(dest, source)

    assert dest[loc_global] == [event1, event2]
    assert dest[loc_xp] == [event3]


def test_old_flatten_events_flattens_groups() -> None:
    loc = CmnLocation(0)
    event1 = _make_event("E1")
    event2 = _make_event("E2")
    event3 = _make_event("E3")
    wp1 = _make_watchpoint("WP1")
    wp2 = _make_watchpoint("WP2", xp_id=2, port=3)
    wp3 = _make_watchpoint("WP3", xp_id=3, port=4)

    metrics = {
        "m1": {
            loc: [
                event1,
                [event2, event3],
                wp1,
                [wp2, wp3],
            ]
        }
    }

    flattened = CmnDatabase.old_flatten_events(metrics)

    assert flattened == ((event1, event2, event3, wp1, wp2, wp3),)


def test_regroup_events_restores_indices_and_groups_watchpoints() -> None:
    loc = CmnLocation(0)
    event1 = _make_event("E1")
    event2 = _make_event("E2")
    event3 = _make_event("E3")
    event4 = _make_event("E4")
    wp1 = _make_watchpoint("WP1", xp_id=5, port=1)
    wp2 = _make_watchpoint("WP2", xp_id=5, port=1)

    metrics = {"m1": {loc: [event1, [event2, event3], [wp1, wp2], event4]}}

    regrouped, restore = CmnDatabase.regroup_events(metrics)

    assert regrouped["m1"][loc][0] == [event1, event2, event3, event4]
    assert regrouped["m1"][loc][1] == [wp1, wp2]
    assert restore["m1"][loc] == [0, 1, 2, 5, 3, 4]


def test_eliminate_duplicated_events_deduplicates() -> None:
    loc = CmnLocation(0)
    event1 = _make_event("E1")
    event2 = _make_event("E2")
    event3 = _make_event("E3")

    metrics = {
        "m1": {loc: [event1, event2]},
        "m2": {loc: [event2, [event3]]},
    }

    unique = set(CmnDatabase.eliminate_duplicated_events(metrics))

    assert unique == {event1, event2, event3}
