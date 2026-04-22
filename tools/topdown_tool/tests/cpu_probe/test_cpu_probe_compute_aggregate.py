# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from typing import Tuple

from topdown_tool.cpu_probe.common import CpuAggregate
from topdown_tool.cpu_probe.cpu_probe import CpuProbe, EventRecords, EventTimedResults
from topdown_tool.perf import Cpu, Uncore
from topdown_tool.cpu_probe.cpu_telemetry_database import Event


def create_event(name: str, code: int = 0) -> Event:
    # Create a real Event instance; title and description are set to dummy values.
    return Event(name=name, title=name, description="dummy", code=code, modifiers=None)


# Helper type for event group
EventGroup = Tuple[Event, ...]


# Test case: No CPU record in the input.
def test_no_cpu_record() -> None:
    # Input records: only a non-CPU key
    event_records: EventRecords = {Uncore(): {}}  # No data provided
    ev = create_event("event1")
    recorded_groups: Tuple[Event, ...] = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    # Expect no CPU keys found, aggregated CpuAggregate should have empty cpus.
    assert isinstance(aggregate, CpuAggregate)
    assert aggregate.cpus == ()
    # There should be no aggregated data because no Cpu key was processed.
    assert agg_records == {}


# Test case: CPU record with no time information.
def test_cpu_record_no_time() -> None:
    ev = create_event("event1")
    # Build event_records with key Cpu(0) and a single sample at time None.
    event_records: EventRecords = {Cpu(0): {None: {(ev,): (10.0,)}}}
    recorded_groups = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    # Expect CpuAggregate to include Cpu(0)
    assert isinstance(aggregate, CpuAggregate)
    assert aggregate.cpus == (Cpu(0),)
    # For time key None, the aggregated results should match the input.
    expected: EventTimedResults = {None: {(ev,): (10.0,)}}
    assert agg_records == expected


# Test case: CPU record with time information.
def test_cpu_record_with_time() -> None:
    ev = create_event("event1")
    event_records: EventRecords = {Cpu(0): {1.0: {(ev,): (3.0,)}}}
    recorded_groups = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    assert aggregate.cpus == (Cpu(0),)
    expected: EventTimedResults = {1.0: {(ev,): (3.0,)}}
    assert agg_records == expected


# Test case: Multiple CPU records with aggregation (summing values).
def test_multiple_cpu_records_sum() -> None:
    ev = create_event("event1")
    event_records: EventRecords = {
        Cpu(0): {1.0: {(ev,): (2.0,)}},
        Cpu(1): {1.0: {(ev,): (3.0,)}},
    }
    recorded_groups = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    # Expect both CPU keys to be detected.
    assert set(aggregate.cpus) == {Cpu(0), Cpu(1)}
    expected: EventTimedResults = {1.0: {(ev,): (5.0,)}}
    assert agg_records == expected


# Test case: Aggregation with a None value in the measurements.
def test_none_value_aggregation() -> None:
    ev = create_event("event1")
    event_records: EventRecords = {Cpu(0): {None: {(ev,): (None,)}}}
    recorded_groups = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    # When any measurement is None, the aggregated value should be None.
    expected: EventTimedResults = {None: {(ev,): (None,)}}
    assert agg_records == expected


# Test case: Multiple time keys for a single CPU.
def test_multiple_time_keys() -> None:
    ev = create_event("event1")
    event_records: EventRecords = {Cpu(0): {1.0: {(ev,): (2.0,)}, 2.0: {(ev,): (3.0,)}}}
    recorded_groups = (ev,)

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    expected: EventTimedResults = {1.0: {(ev,): (2.0,)}, 2.0: {(ev,): (3.0,)}}
    assert agg_records == expected


# Test case: Multiple events in a group (tuple length > 1) and additive aggregation.
def test_multiple_events_in_group() -> None:
    ev1 = create_event("event1", code=1)
    ev2 = create_event("event2", code=2)
    group: Tuple[Event, Event] = (ev1, ev2)

    # Two CPU records at same time with corresponding tuples.
    event_records: EventRecords = {
        Cpu(0): {1.0: {group: (1.0, 2.0)}},
        Cpu(1): {1.0: {group: (3.0, 4.0)}},
    }
    recorded_groups = group

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])

    # Expect summed results elementwise.
    expected: EventTimedResults = {1.0: {group: (4.0, 6.0)}}
    assert agg_records == expected


def test_multiple_event_groups_multiple_timed_values() -> None:
    # Create two events and two groups (each group is a tuple of one event)
    ev1 = create_event("event1")
    ev2 = create_event("event2")
    group1: Tuple[Event, ...] = (ev1,)
    group2: Tuple[Event, ...] = (ev2,)
    recorded_groups = [group1, group2]

    # Build event_records with two CPU keys having two time keys each.
    event_records: EventRecords = {
        Cpu(0): {
            1.0: {group1: (1.0,), group2: (2.0,)},
            2.0: {group1: (3.0,), group2: (4.0,)},
        },
        Cpu(1): {
            1.0: {group1: (10.0,), group2: (20.0,)},
            2.0: {group1: (30.0,), group2: (40.0,)},
        },
    }

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, recorded_groups)

    # Assert the aggregate value (CpuAggregate) correctly contains both CPUs.
    assert aggregate.cpus == tuple(sorted((Cpu(0), Cpu(1))))

    # Expected aggregation per time key:
    expected: EventTimedResults = {
        1.0: {
            group1: (11.0,),
            group2: (22.0,),
        },
        2.0: {
            group1: (33.0,),
            group2: (44.0,),
        },
    }
    assert agg_records == expected


def test_timed_value_none_aggregation() -> None:
    # Test: One CPU record has a None value, so the aggregated value is None.
    ev = create_event("event1")
    event_records: EventRecords = {
        Cpu(0): {1.0: {(ev,): (None,)}},
        Cpu(1): {1.0: {(ev,): (10.0,)}},
    }
    recorded_groups = (ev,)
    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, [recorded_groups])
    # Since one measurement is None, the sum should be None.
    expected: EventTimedResults = {1.0: {(ev,): (None,)}}
    assert agg_records == expected


def test_missing_event_group_in_eventresults() -> None:
    # Two groups, but one group is missing from one CPU's EventResults at a given time
    ev1 = create_event("event1")
    ev2 = create_event("event2")
    group1: Tuple[Event, ...] = (ev1,)
    group2: Tuple[Event, ...] = (ev2,)
    recorded_groups = [group1, group2]

    event_records: EventRecords = {
        Cpu(0): {
            1.0: {group1: (1.0,)},  # group2 missing here
        },
        Cpu(1): {
            1.0: {group1: (2.0,), group2: (3.0,)},
        },
    }

    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, recorded_groups)

    # For group2 at time 1.0, since it's missing in Cpu(0), the aggregate should be (None,)
    expected: EventTimedResults = {
        1.0: {
            group1: (3.0,),  # 1.0 + 2.0
            group2: (None,),  # missing in Cpu(0)
        }
    }
    assert agg_records == expected
