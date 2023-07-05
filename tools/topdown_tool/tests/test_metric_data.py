# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

import json
import os

from topdown_tool.metric_data import METRICS_DIR, CombinedMetricInstance, MetricData, combine_instances


def test_add_data():
    """Test that additional data doesn't break parsing"""
    with open(os.path.join(METRICS_DIR, "neoverse-n1.json"), encoding="utf-8") as f:
        json_data = json.load(f)

    assert "events" in json_data
    json_data["events"]["CPU_CYCLES"]["new-field"] = "test"
    data = MetricData(json_data)

    assert "metrics" in json_data
    json_data["metrics"]["frontend_stalled_cycles"]["new-field"] = "test"
    data = MetricData(json_data)

    assert "groups" in json_data
    assert "metrics" in json_data["groups"]
    json_data["groups"]["metrics"]["Cycle_Accounting"]["new-field"] = "test"
    data = MetricData(json_data)

    assert data


def test_group():
    metric_data = MetricData("neoverse-n1")
    metric_instances = metric_data.metrics_for_group("Cycle_Accounting")
    assert [instance.metric.name for instance in metric_instances] == [
        "frontend_stalled_cycles",
        "backend_stalled_cycles"
    ]
    assert all(instance.group.name == "Cycle_Accounting" for instance in metric_instances)


def test_level():
    metric_data = MetricData("neoverse-n1")
    l1 = metric_data.metrics_up_to_level(1)
    assert all(instance.level == 1 for instance in l1)
    assert set(instance.metric.name for instance in l1) == {
        "frontend_stalled_cycles",
        "backend_stalled_cycles",
    }

    l2 = metric_data.metrics_up_to_level(2)
    l2_metrics = [instance.metric for instance in l2]
    assert 1 in (instance.level for instance in l2)  # We have some L1 metrics
    assert 2 in (instance.level for instance in l2)  # We have some L2 metrics
    assert all(
        l1_instance.metric in l2_metrics for l1_instance in l1
    )  # All L1 metrics are also present when collecting up to L2
    assert "store_percentage" in [
        instance.metric.name for instance in l2
    ]  # Check for a specific metric


def test_descendants():
    metric_data = MetricData("neoverse-n1")
    metrics = metric_data.metrics_descended_from("frontend_stalled_cycles")

    assert sorted(instance.metric.name for instance in metrics) == sorted(
        [
            "frontend_stalled_cycles",
            "branch_mpki",
            "branch_misprediction_ratio",
            "itlb_mpki",
            "itlb_walk_ratio",
            "l1i_tlb_mpki",
            "l1i_tlb_miss_ratio",
            "l2_tlb_mpki",
            "l2_tlb_miss_ratio",
            "l1i_cache_mpki",
            "l1i_cache_miss_ratio",
            "l2_cache_mpki",
            "l2_cache_miss_ratio",
            "ll_cache_read_mpki",
            "ll_cache_read_miss_ratio",
            "ll_cache_read_hit_ratio"
        ]
    )
    assert set(instance.group.name for instance in metrics if instance.group) == set(
        ["Cycle_Accounting", "Branch_Effectiveness", "ITLB_Effectiveness", "L1I_Cache_Effectiveness", "LL_Cache_Effectiveness", "L2_Cache_Effectiveness"]
    )


def test_uncategorised():
    metric_data = MetricData("neoverse-n1")
    metrics = metric_data.uncategorised_metrics()

    assert sorted(m.metric.title for m in metrics) == sorted([
        "Branch MPKI",
        "ITLB MPKI",
        "L1 Instruction TLB MPKI",
        "DTLB MPKI",
        "L1 Data TLB MPKI",
        "L2 Unified TLB MPKI",
        "L1I Cache MPKI",
        "L1D Cache MPKI",
        "L2 Cache MPKI",
        "LL Cache Read MPKI",
        "Branch Misprediction Ratio",
        "ITLB Walk Ratio",
        "DTLB Walk Ratio",
        "L1 Instruction TLB Miss Ratio",
        "L1 Data TLB Miss Ratio",
        "L2 Unified TLB Miss Ratio",
        "L1I Cache Miss Ratio",
        "L1D Cache Miss Ratio",
        "L2 Cache Miss Ratio",
        "LL Cache Read Miss Ratio",
        "Instructions Per Cycle"
    ])


def test_case_insensitive():
    metric_data = MetricData("neoverse-n1")
    assert (metric_data.metrics_for_group("Cycle_Accounting")
            == metric_data.metrics_for_group("cycle_accounting")
            == metric_data.metrics_for_group("CYCLE_ACCOUNTING")
            == metric_data.metrics_for_group("CycleAccounting")
            == metric_data.metrics_for_group("cycleaccounting")
            == metric_data.metrics_for_group("Cycle-Accounting"))

    assert (metric_data.metrics_descended_from("frontend_stalled_cycles")
            == metric_data.metrics_descended_from("FRONTEND_STALLED_CYCLES")
            == metric_data.metrics_descended_from("frontend-stalled-cycles")
            == metric_data.metrics_descended_from("frontendstalledcycles")
            == metric_data.metrics_descended_from("FrOnT-EnDsTaLleD_CyClEs"))


def test_combine_instances():
    assert combine_instances([]) == []

    metric_data = MetricData("neoverse-n1")
    instances = metric_data.metrics_for_group("CycleAccounting")
    length = len(instances)
    assert instances[0].metric.name == "frontend_stalled_cycles"

    # Add duplicate
    instances.append(instances[0])

    combined_instances = combine_instances(instances)
    assert length == len(combined_instances)
    for instance in combined_instances:
        assert isinstance(instance, CombinedMetricInstance)
