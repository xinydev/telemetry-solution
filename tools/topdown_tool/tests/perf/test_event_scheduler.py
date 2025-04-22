# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import pytest
from topdown_tool.cpu_probe.common import CollectBy
from topdown_tool.perf.event_scheduler import (
    EventGroupIterator,
    EventScheduler,
    GroupScheduleError,
)
from topdown_tool.perf.perf import PerfEvent


@dataclass(frozen=True, order=True)
class DummyEvent(PerfEvent):
    name: str

    def perf_name(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return self.name


DummyPerfEventGroup = Tuple[DummyEvent, ...]


def make_event(name):
    return DummyEvent(name)


@pytest.fixture
def superset_test_data():
    # Create events.
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    e4 = make_event("E4")
    # Metrics: m2 is a superset of m1, m4 is a superset of m3.
    m1 = (e1, e2)
    m2 = (e1, e2, e3)  # superset of m1
    m3 = (e4,)
    m4 = (e3, e4)  # superset of m3
    # Groups: g2 is a superset of g1.
    g1 = (m1, m3)
    g2 = (m1, m2, m3, m4)
    return {
        "events": (e1, e2, e3, e4),
        "metrics": (m1, m2, m3, m4),
        "groups": (g1, g2),
        "e1": e1,
        "e2": e2,
        "e3": e3,
        "e4": e4,
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "m4": m4,
        "g1": g1,
        "g2": g2,
    }


def _group_events(group: Sequence[DummyPerfEventGroup]) -> DummyPerfEventGroup:
    """Return a sorted tuple of unique events from the group's metrics."""
    unique_events = set([event for metric_events in group for event in metric_events])
    sorted_events = sorted(unique_events)
    return tuple(sorted_events)


def test_metric_event_superset_optimization(superset_test_data) -> None:
    f = superset_test_data
    groups = f["groups"]
    scheduler: EventScheduler[DummyEvent] = EventScheduler(groups, CollectBy.METRIC, max_events=3)

    expected_event_groups: List[DummyPerfEventGroup] = [
        f["m2"],  # m2
        f["m4"],  # m4
    ]
    actual_event_groups = [set(grp) for grp in scheduler.optimized_event_groups]
    expected_sets = [set(grp) for grp in expected_event_groups]

    assert len(actual_event_groups) == len(expected_sets)
    for expected in expected_sets:
        assert expected in actual_event_groups


def test_group_metric_superset_optimization(superset_test_data) -> None:
    f = superset_test_data
    groups: Tuple[Tuple[DummyPerfEventGroup]] = f["groups"]
    scheduler: EventScheduler[DummyEvent] = EventScheduler(groups, CollectBy.GROUP, max_events=6)

    expected_event_groups: List[DummyPerfEventGroup] = [
        _group_events(f["g2"]),  # g2: m1 + m2 + m3 + m4
    ]
    actual_event_groups = [set(grp) for grp in scheduler.optimized_event_groups]
    expected_sets = [set(grp) for grp in expected_event_groups]

    assert len(actual_event_groups) == len(expected_sets)
    for expected in expected_sets:
        assert expected in actual_event_groups


def test_none_collectby_event_groups(superset_test_data):
    f = superset_test_data
    groups = f["groups"]
    scheduler = EventScheduler(groups, CollectBy.NONE, max_events=6)

    expected_event_groups = [(e,) for e in f["events"]]
    actual_event_groups = [grp for grp in scheduler.optimized_event_groups]
    expected_sets = [set(grp) for grp in expected_event_groups]

    assert len(actual_event_groups) == len(expected_sets)
    for expected in expected_sets:
        assert any(set(grp) == expected for grp in actual_event_groups)


def test_retrieve_metric_result_superset(superset_test_data):
    f = superset_test_data
    groups = f["groups"]
    scheduler = EventScheduler(groups, CollectBy.METRIC, max_events=3)
    # Only m2 and m4 should be present in optimized_event_groups
    perf_result = {
        f["m2"]: [10.0, 20.0, 30.0],
        f["m4"]: [40.0, 50.0],
    }
    res_m1 = scheduler.retrieve_metric_result(perf_result, f["m1"])
    res_m2 = scheduler.retrieve_metric_result(perf_result, f["m2"])
    res_m3 = scheduler.retrieve_metric_result(perf_result, f["m3"])
    res_m4 = scheduler.retrieve_metric_result(perf_result, f["m4"])
    assert res_m1 == (10.0, 20.0)
    assert res_m2 == (10.0, 20.0, 30.0)
    assert res_m3 == (50.0,)
    assert res_m4 == (40.0, 50.0)


def test_retrieve_group_result_superset(superset_test_data):
    f = superset_test_data
    groups = f["groups"]
    scheduler = EventScheduler(groups, CollectBy.GROUP, max_events=6)
    perf_result = {tuple(f["events"]): [1.0, 2.0, 3.0, 4.0]}  # g1 + g2
    res_g1 = scheduler.retrieve_group_result(perf_result, f["g1"])
    res_g2 = scheduler.retrieve_group_result(perf_result, f["g2"])
    assert res_g1 == (1.0, 2.0, 4.0)
    assert res_g2 == (1.0, 2.0, 3.0, 4.0)


def test_retrieve_event_result_none(superset_test_data):
    f = superset_test_data
    groups = f["groups"]
    scheduler = EventScheduler(groups, CollectBy.NONE, max_events=6)
    # Each event should be its own group in optimized_event_groups
    perf_result = {
        (f["e1"],): (11.0),
        (f["e2"],): (22.0),
        (f["e3"],): (33.0),
        (f["e4"],): (44.0),
    }
    assert scheduler.retrieve_event_result(perf_result, f["e1"]) == (11.0)
    assert scheduler.retrieve_event_result(perf_result, f["e2"]) == (22.0)
    assert scheduler.retrieve_event_result(perf_result, f["e3"]) == (33.0)
    assert scheduler.retrieve_event_result(perf_result, f["e4"]) == (44.0)


def test_group_schedule_error():
    # 7 events, more than max_events=6
    m = tuple([make_event(f"E{i}") for i in range(7)])
    g = (m,)
    with pytest.raises(GroupScheduleError):
        EventScheduler([g], CollectBy.GROUP, max_events=6)


def test_no_split_behavior():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    event_groups = [(e1, e2), (e3,)]
    it = EventGroupIterator(event_groups, max_events=2, split=False)
    assert iter(it) is it
    assert it.has_next()
    assert it.remaining_chunks() == 1
    chunk = next(it)
    assert chunk == event_groups
    assert not it.has_next()
    assert it.remaining_chunks() == 0
    with pytest.raises(StopIteration):
        next(it)


def test_split_behavior():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    e4 = make_event("E4")
    event_groups = [
        (e1, e2),  # 2 events
        (e3, e4),  # 2 events
        (e1,),  # 1 event
    ]
    it = EventGroupIterator(event_groups, max_events=3, split=True)
    assert iter(it) is it
    assert it.has_next()
    assert it.remaining_chunks() == 2
    chunk1 = next(it)
    assert chunk1 == [(e1, e2)]
    assert it.has_next()
    assert it.remaining_chunks() == 1
    chunk2 = next(it)
    assert chunk2 == [(e3, e4), (e1,)]
    assert not it.has_next()
    assert it.remaining_chunks() == 0
    with pytest.raises(StopIteration):
        next(it)


def test_group_larger_than_max_events():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    # This group has 3 events, but max_events=2
    event_groups = [
        (e1, e2, e3),
    ]
    with pytest.raises(ValueError):
        EventGroupIterator(event_groups, max_events=2, split=True)


def test_greedy_merge_optimization():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    # Create metrics with distinct event subsets
    m1 = (e1, e2)
    m2 = (e2, e3)
    m3 = (e1, e3)
    # Create separate real Group instances for each metric
    g1 = (m1,)
    g2 = (m2,)
    g3 = (m3,)
    scheduler = EventScheduler([g1, g2, g3], CollectBy.GROUP, max_events=3)
    optimized = scheduler.optimized_event_groups
    # Expect a single merged group with union of e1, e2, e3 (sorted by code)
    expected = {e1, e2, e3}
    assert len(optimized) == 1
    assert set(optimized[0]) == expected


def test_greedy_merge_metric_optimization():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    # Create metrics with overlapping events
    m1 = (e1, e2)
    m2 = (e2, e3)
    m3 = (e1, e3)
    # Create one Group containing all metrics
    g = (m1, m2, m3)
    scheduler = EventScheduler([g], CollectBy.METRIC, max_events=3)
    optimized = scheduler.optimized_event_groups
    expected = {e1, e2, e3}
    assert len(optimized) == 1
    assert set(optimized[0]) == expected


def test_no_merge_edge_case():
    # Edge case: Groups that cannot be merged because union exceeds max_events
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    e4 = make_event("E4")
    # Create metrics that are disjoint so their union would be 4 events
    m1 = (e1, e2)
    m2 = (e3, e4)
    g1 = (m1,)
    g2 = (m2,)
    scheduler = EventScheduler([g1, g2], CollectBy.GROUP, max_events=3)
    optimized = scheduler.optimized_event_groups
    # No merge should occur as merging them would exceed max_events (4 > 3)
    assert len(optimized) == 2
    groups_sets = [set(g) for g in optimized]
    assert {e1, e2} in groups_sets
    assert {e3, e4} in groups_sets


def test_retrieve_event_results_none():
    e1 = make_event("A")
    e2 = make_event("B")
    e3 = make_event("C")
    e4 = make_event("D")
    # Metric1 uses e1,e2; Metric2 uses e3,e4; Metric3 uses e2,e3
    m1 = (e1, e2)
    m2 = (e3, e4)
    m3 = (e2, e3)
    # Group1 contains m1 and m3; Group2 contains m2.
    g1 = (m1, m3)
    g2 = (m2,)
    # Perf result: keys are individual events.
    perf_result = {
        (e1,): (10.0,),
        (e2,): (20.0,),
        (e3,): (30.0,),
        (e4,): (40.0,),
    }
    # Using both groups in a single scheduler.
    scheduler = EventScheduler([g1, g2], CollectBy.NONE, max_events=6)
    res_m1 = scheduler.retrieve_event_results(perf_result, g1, m1)
    res_m3 = scheduler.retrieve_event_results(perf_result, g1, m3)
    # m1.events is sorted (A, B) -> (10.0, 20.0)
    assert res_m1 == (10.0, 20.0)
    # m3.events is sorted (B, C) -> (20.0, 30.0)
    assert res_m3 == (20.0, 30.0)


def test_retrieve_event_results_metric():
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    e4 = make_event("E4")
    # Metric1 uses e1,e2; Metric2 uses e3,e4; Metric3 uses e2,e3.
    m1 = (e1, e2)
    m2 = (e3, e4)
    m3 = (e2, e3)
    g = (m1, m2, m3)
    # In METRIC mode, perf_result keys are based on metric.events.
    perf_result = {
        (e1, e2, e3, e4): (10.0, 20.0, 30.0, 40.0),
    }
    scheduler = EventScheduler([g], CollectBy.METRIC, max_events=6)
    res_m1 = scheduler.retrieve_event_results(perf_result, g, m1)
    res_m2 = scheduler.retrieve_event_results(perf_result, g, m2)
    res_m3 = scheduler.retrieve_event_results(perf_result, g, m3)
    assert res_m1 == (10.0, 20.0)
    assert res_m2 == (30.0, 40.0)
    assert res_m3 == (20.0, 30.0)


def test_retrieve_event_results_group() -> None:
    e1 = make_event("E1")
    e2 = make_event("E2")
    e3 = make_event("E3")
    e4 = make_event("E4")
    m1 = (e1, e2)
    m2 = (e3,)
    m3 = (e2, e4)
    g1 = (m1, m2)
    g2 = (m3,)
    perf_result: Dict[DummyPerfEventGroup, Tuple[Optional[float], ...]] = {
        _group_events(g1): (
            101.0,
            102.0,
            103.0,
        ),
        _group_events(g2): (202.0, 204.0),
    }

    scheduler = EventScheduler[DummyEvent]([g1, g2], CollectBy.GROUP, max_events=3)
    res_m1 = scheduler.retrieve_event_results(perf_result, g1, m1)
    res_m2 = scheduler.retrieve_event_results(perf_result, g1, m2)
    res_m3 = scheduler.retrieve_event_results(perf_result, g2, m3)
    assert res_m1 == (101.0, 102.0)
    assert res_m2 == (103.0,)
    assert res_m3 == (202.0, 204.0)


def test_retrieve_event_results_none_with_missing_value():
    e1 = make_event("M")
    e2 = make_event("N")
    e3 = make_event("O")
    # Metric1 uses e1,e2; Metric2 uses e2,e3.
    m1 = (e1, e2)
    m2 = (e2, e3)
    g = (m1, m2)
    # In NONE mode, keys are individual events.
    perf_result = {
        (e1,): (15.0,),
        (e2,): (None,),  # missing value for e2
        (e3,): (35.0,),
    }
    scheduler = EventScheduler([g], CollectBy.NONE, max_events=6)
    res_m1 = scheduler.retrieve_event_results(perf_result, g, m1)
    res_m2 = scheduler.retrieve_event_results(perf_result, g, m2)
    # For m1, sorted (e1,e2) -> (15.0, None)
    assert res_m1 == (15.0, None)
    # For m2, sorted (e2,e3) -> (None, 35.0)
    assert res_m2 == (None, 35.0)


def test_retrieve_event_results_invalid_key():
    eA = make_event("A")
    eB = make_event("B")
    m_metric = (eA, eB)
    g = (m_metric,)
    perf_result = {}  # missing key for m_metric.events
    scheduler = EventScheduler([g], CollectBy.METRIC, max_events=6)
    with pytest.raises(KeyError):
        scheduler.retrieve_event_results(perf_result, g, m_metric)
