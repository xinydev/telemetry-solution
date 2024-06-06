# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

import itertools
import sys

import pytest
from topdown_tool.__main__ import DEFAULT_ALL_STAGES

from topdown_tool.event_collection import CPU_PMU_COUNTERS, CollectBy, GroupScheduleError, schedule_for_events
from topdown_tool.metric_data import MetricData


TEST_CPUS = ["neoverse-n1", "neoverse-v1", "neoverse-n2", "neoverse-v2"]
# Collecting all metrics in these groups requires more events than can be counted simultaneously
MULTIPLEX_GROUPS = ["Topdown_L1", "Operation_Mix", "Miss_Ratio", "MPKI"]


@pytest.fixture(name="metric_data")
def metric_data_fixture():
    return MetricData("neoverse-n1")


def test_collect_by_none_simple(metric_data):
    """Simple case, one run"""
    metrics = metric_data.metrics_for_group("CycleAccounting")
    schedule = schedule_for_events(metrics, CollectBy.NONE, CPU_PMU_COUNTERS)
    assert len(schedule) == 1


def test_collect_by_none_one_run(metric_data):
    """Larger number of metrics, with unbounded events => one run"""
    metrics = metric_data.metrics_descended_from("frontend_stalled_cycles")
    schedule = schedule_for_events(metrics, CollectBy.NONE, sys.maxsize)
    assert len(schedule) == 1


def test_collect_by_none_multiplex(metric_data):
    """Larger number of metrics with max_events restriction"""
    metrics = metric_data.metrics_descended_from("frontend_stalled_cycles")
    schedule = schedule_for_events(metrics, CollectBy.NONE, CPU_PMU_COUNTERS)
    # More than one run
    assert len(schedule) > 1

    # All scheduling groups have a single event
    for run in schedule:
        assert all(len(groups) == 1 for groups in run)

    # No duplicate events
    flat_events = [event for schedule_item in schedule for group in schedule_item for event in group]
    assert len(flat_events) == len(set(flat_events))


@pytest.mark.parametrize("collect_by", CollectBy)
@pytest.mark.parametrize("cpu", TEST_CPUS)
def test_no_multiplex_all_events(cpu, collect_by):
    """Schedule all events without multiplexing, with different collect-by options."""
    metric_data = MetricData(cpu)
    metrics = metric_data.all_metrics(DEFAULT_ALL_STAGES)

    try:
        schedule = schedule_for_events(metrics, collect_by, CPU_PMU_COUNTERS)
        for event_groups in schedule:
            unique_events = set(e.event for e in itertools.chain(*event_groups))
            assert len(unique_events) <= CPU_PMU_COUNTERS
    except GroupScheduleError as e:
        # We know that some metric groups can't be scheduled together
        assert collect_by == CollectBy.GROUP
        assert e.group.name in MULTIPLEX_GROUPS


@pytest.mark.parametrize("cpu", TEST_CPUS)
def test_no_multiplex_groups(cpu):
    """Ensure groups (other than the known "multiplex groups") can be scheduled without multiplexing."""
    metric_data = MetricData(cpu)

    for group_name in (g for g in metric_data.groups if g not in MULTIPLEX_GROUPS):
        metrics = metric_data.metrics_for_group(group_name)

        schedule = schedule_for_events(metrics, CollectBy.GROUP, CPU_PMU_COUNTERS)
        for event_groups in schedule:
            unique_events = set(e.event for e in itertools.chain(*event_groups))
            assert len(unique_events) <= CPU_PMU_COUNTERS
            assert len(schedule) == 1


@pytest.mark.parametrize("cpu", TEST_CPUS)
def test_multiplex_groups(cpu):
    """Ensure all multiplex groups raise an exception when trying to collect by group."""
    metric_data = MetricData(cpu)

    for group_name in (g for g in MULTIPLEX_GROUPS if g in metric_data.groups):
        metrics = metric_data.metrics_for_group(group_name)

        with pytest.raises(GroupScheduleError) as e_info:
            schedule_for_events(metrics, CollectBy.GROUP, CPU_PMU_COUNTERS)

        assert e_info.value.group.name == group_name
        assert len(e_info.value.events) > CPU_PMU_COUNTERS
        assert e_info.value.available_events == CPU_PMU_COUNTERS
