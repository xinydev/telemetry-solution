# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import sys

from event_collection import CollectBy, schedule_for_events
from metric_data import MetricData

metric_data = MetricData("neoverse-n1")


def test_collect_by_none_simple():
    """Simple case, one run"""
    metrics = metric_data.metrics_for_group("CycleAccounting")
    schedule = schedule_for_events(metrics, CollectBy.NONE, 6)
    assert len(schedule) == 1


def test_collect_by_none_one_run():
    """Larger number of metrics, with unbounded events => one run"""
    metrics = metric_data.metrics_descended_from("frontend_stalled_cycles")
    schedule = schedule_for_events(metrics, CollectBy.NONE, sys.maxsize)
    assert len(schedule) == 1


def test_collect_by_none_multiplex():
    """Larger number of metrics with max_events restriction"""
    metrics = metric_data.metrics_descended_from("frontend_stalled_cycles")
    schedule = schedule_for_events(metrics, CollectBy.NONE, 6)
    # More than one run
    assert len(schedule) > 1

    # All scheduling groups have a single event
    for run in schedule:
        assert all(len(groups) == 1 for groups in run)

    # No duplicate events
    flat_events = [event for schedule_item in schedule for group in schedule_item for event in group]
    assert len(flat_events) == len(set(flat_events))
