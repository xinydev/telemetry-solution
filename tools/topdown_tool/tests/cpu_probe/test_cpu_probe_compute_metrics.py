# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from typing import Dict, Tuple, Optional

from topdown_tool.cpu_probe.cpu_probe import CpuProbe
from topdown_tool.perf.event_scheduler import EventScheduler, CollectBy
from topdown_tool.cpu_probe.cpu_telemetry_database import Event, Metric, Group
from topdown_tool.perf import Cpu


def test_compute_metrics_none_mode_all_defined() -> None:
    # Setup fixture for CollectBy.NONE
    cpu = Cpu(0)
    time_key = None

    # Create events and metric with formula "A+B"
    eA = Event(name="A", title="A", description="", code=1)
    eB = Event(name="B", title="B", description="", code=2)
    m1 = Metric(
        db=None,
        name="M1",
        title="M1",
        description="",
        units="",
        formula="A+B",
        events=(eA, eB),
        sample_events=(eA, eB),
    )
    g = Group(name="G", title="G", description="", metrics=(m1,))

    # In NONE mode, perf_result keys are individual events.
    perf_result: Dict[Tuple[Event, ...], Tuple[Optional[float], ...]] = {
        (eA,): (1.0,),
        (eB,): (2.0,),
    }
    records = {cpu: {time_key: perf_result}}
    scheduler = EventScheduler([g.metric_event_tuples()], CollectBy.NONE, max_events=6)
    computed = CpuProbe._compute_metrics([g], records, scheduler)
    # Expected: 1+2 = 3
    assert computed[cpu][time_key][g][m1] == 3


def test_compute_metrics_none_mode_with_missing_value():
    # Setup fixture for CollectBy.NONE with missing value in one event
    cpu = Cpu(1)
    time_key = 0.0
    eA = Event(name="A", title="A", description="", code=1)
    eB = Event(name="B", title="B", description="", code=2)
    m1 = Metric(
        db=None,
        name="M1",
        title="M1",
        description="",
        units="",
        formula="A+B",
        events=(eA, eB),
        sample_events=(eA, eB),
    )
    g = Group(name="G", title="G", description="", metrics=(m1,))
    # Missing value for B.
    perf_result = {(eA,): (1.0,), (eB,): (None,)}
    records = {cpu: {time_key: perf_result}}
    scheduler = EventScheduler([g.metric_event_tuples()], CollectBy.NONE, max_events=6)
    computed = CpuProbe._compute_metrics([g], records, scheduler)
    # Expected: None because one value is missing.
    assert computed[cpu][time_key][g][m1] is None


def test_compute_metrics_metric_mode_all_defined():
    # Setup fixture for CollectBy.METRIC
    cpu = Cpu(0)
    time_key = 1.0
    eA = Event(name="A", title="A", description="", code=1)
    eB = Event(name="B", title="B", description="", code=2)
    # Using formula "A*B"
    m1 = Metric(
        db=None,
        name="M1",
        title="M1",
        description="",
        units="",
        formula="A*B",
        events=(eA, eB),
        sample_events=(eA, eB),
    )
    g = Group(name="G", title="G", description="", metrics=(m1,))
    # In METRIC mode, perf_result key is m1.events
    perf_result = {m1.events: (3.0, 4.0)}  # "A*B" becomes "3*4" => 12
    records = {cpu: {time_key: perf_result}}
    scheduler = EventScheduler([g.metric_event_tuples()], CollectBy.METRIC, max_events=6)
    computed = CpuProbe._compute_metrics([g], records, scheduler)
    assert computed[cpu][time_key][g][m1] == 12


def test_compute_metrics_group_mode_all_defined() -> None:
    # Setup fixture for CollectBy.GROUP
    cpu = Cpu(0)
    time_key = 2.0
    eA = Event(name="A", title="A", description="", code=1)
    eB = Event(name="B", title="B", description="", code=2)
    eC = Event(name="C", title="C", description="", code=3)
    m1 = Metric(
        db=None,
        name="M1",
        title="M1",
        description="",
        units="",
        formula="A+B",
        events=(eA, eB),
        sample_events=(eA, eB),
    )
    m2 = Metric(
        db=None,
        name="M2",
        title="M2",
        description="",
        units="",
        formula="B+C",
        events=(eB, eC),
        sample_events=(eB, eC),
    )
    # Group g contains both metrics; group.events is union, sorted. Assume order: (A, B, C)
    g = Group(name="G", title="G", description="", metrics=(m1, m2))
    # In GROUP mode, perf_result key is based on group.events.
    # For g; supply values for (A, B, C) e.g. (2.0, 3.0, 4.0)
    perf_result = {g.events: (2.0, 3.0, 4.0)}
    records = {cpu: {time_key: perf_result}}
    scheduler = EventScheduler([g.metric_event_tuples()], CollectBy.GROUP, max_events=6)
    computed = CpuProbe._compute_metrics([g], records, scheduler)
    # For m1: events (A,B) -> indices 0 and 1 => 2+3 = 5
    # For m2: events (B,C) -> indices 1 and 2 => 3+4 = 7
    assert computed[cpu][time_key][g][m1] == 5.0
    assert computed[cpu][time_key][g][m2] == 7.0


def test_compute_metrics_metric_multiple_cpus_time():
    # Fixture for CollectBy.METRIC with multiple CPUs and multiple time keys, using two groups
    # Create two dummy CPUs
    cpu1 = Cpu(0)
    cpu2 = Cpu(1)
    # Define two time keys
    time1 = 0.0
    time2 = 1.0

    # Create events for Metric 1 and Metric 2
    eA = Event(name="A", title="A", description="", code=1)
    eB = Event(name="B", title="B", description="", code=2)
    eC = Event(name="C", title="C", description="", code=3)
    eD = Event(name="D", title="D", description="", code=4)

    # Metric1: formula "A+B"
    m1 = Metric(
        db=None,
        name="M1",
        title="M1",
        description="",
        units="",
        formula="A+B",
        events=(eA, eB),
        sample_events=(eA, eB),
    )
    # Metric2: formula "C*D"
    m2 = Metric(
        db=None,
        name="M2",
        title="M2",
        description="",
        units="",
        formula="C*D",
        events=(eC, eD),
        sample_events=(eC, eD),
    )

    # Define groups: g1 contains Metric1; g2 contains Metric2
    g1 = Group(name="G1", title="G1", description="", metrics=(m1,))
    g2 = Group(name="G2", title="G2", description="", metrics=(m2,))
    groups = [g1, g2]

    # Build perf_result dictionaries for each CPU and time key in METRIC mode.
    # In METRIC mode, the key is the events tuple for each metric.
    # For cpu1:
    perf_result_cpu1_t1 = {
        (eA, eB, eC, eD): (1.0, 2.0, 3.0, 4.0),  # 1+2 = 3 and 3*4 = 12
    }
    perf_result_cpu1_t2 = {
        (eA, eB, eC, eD): (2.0, 3.0, 4.0, 5.0),  # 2+3 = 5 and 4*5 = 20
    }
    # For cpu2:
    perf_result_cpu2_t1 = {
        (eA, eB, eC, eD): (0.5, 1.5, 2.0, 3.0),  # 0.5+1.5 = 2 and 2*3 = 6
    }
    perf_result_cpu2_t2 = {
        (eA, eB, eC, eD): (1.0, 1.0, 3.0, 2.0),  # 1+1 = 2 and 3*2 = 6
    }

    records = {
        cpu1: {time1: perf_result_cpu1_t1, time2: perf_result_cpu1_t2},
        cpu2: {time1: perf_result_cpu2_t1, time2: perf_result_cpu2_t2},
    }

    # Create scheduler using CollectBy.METRIC with both groups
    scheduler = EventScheduler(
        [g.metric_event_tuples() for g in groups], CollectBy.METRIC, max_events=6
    )

    computed = CpuProbe._compute_metrics(groups, records, scheduler)

    # Validate computed metrics for each CPU and time key
    # For cpu1, time1:
    assert computed[cpu1][time1][g1][m1] == 3.0  # 1+2
    assert computed[cpu1][time1][g2][m2] == 12.0  # 3*4
    # For cpu1, time2:
    assert computed[cpu1][time2][g1][m1] == 5.0  # 2+3
    assert computed[cpu1][time2][g2][m2] == 20.0  # 4*5
    # For cpu2, time1:
    assert computed[cpu2][time1][g1][m1] == 2.0  # 0.5+1.5
    assert computed[cpu2][time1][g2][m2] == 6.0  # 2*3
    # For cpu2, time2:
    assert computed[cpu2][time2][g1][m1] == 2.0  # 1+1
    assert computed[cpu2][time2][g2][m2] == 6.0  # 3*2
