# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import csv

import pytest

from topdown_tool.cpu_probe.cpu_csv_renderer import CpuCsvRenderer
from topdown_tool.cpu_probe.cpu_telemetry_database import GroupView
from topdown_tool.perf import Cpu, Uncore
from topdown_tool.cpu_probe.common import CpuAggregate
from topdown_tool.common import range_encode

from tests.cpu_probe.helpers import get_fixture_path, compare_reference


# --- Use the canonical fixture provided via conftest.py ---
@pytest.fixture
def db(test_telemetry_db):
    return test_telemetry_db


@pytest.fixture
def cpu_csv_renderer():
    return CpuCsvRenderer()


# --- Helper Function for Reading CSV ---
def read_csv(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


# --- Helper Function for Reading CSV as Text ---
def read_file_str(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# --- Tests use the existing db fixture as authored ---


def test_write_csv_without_aggregate(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Per-core only: create computed_metrics for one core and verify CSV output.
    """
    cpu0 = Cpu(0)
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {cpu0: {1.0: {group: {metric: 100}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    out_path = out_dir / "testcpu_core_0_metrics.csv"

    actual_csv = read_file_str(out_path)
    reference_path = get_fixture_path("cpu_csv_renderer", "write_csv_without_aggregate.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_write_csv_with_core_and_aggregate(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Both per-core and aggregate results: verify file names and contents using reference output.
    """
    cpu0 = Cpu(0)
    cpu1 = Cpu(1)
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {
        cpu0: {2.0: {group: {metric: 50}}},
        CpuAggregate(cpus=(cpu0, cpu1)): {2.0: {group: {metric: 75}}},
    }
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    # Check core file
    path_core = out_dir / "testcpu_core_0_metrics.csv"
    actual_core = read_file_str(path_core)
    reference_core = get_fixture_path(
        "cpu_csv_renderer", "write_csv_with_core_and_aggregate_core_0.csv"
    )
    compare_reference(actual_core, reference_core, regen_reference_mode)

    # Check aggregate file (ids 0,1)
    agg_filename = f"testcpu_core_aggregate_({range_encode([0, 1])})_metrics.csv"
    path_agg = out_dir / agg_filename
    actual_agg = read_file_str(path_agg)
    reference_agg = get_fixture_path(
        "cpu_csv_renderer", "write_csv_with_core_and_aggregate_aggregate_(0-1).csv"
    )
    compare_reference(actual_agg, reference_agg, regen_reference_mode)


def test_write_csv_with_intervals(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Multiple timestamps for the same core: verify CSV reference.
    """
    cpu0 = Cpu(0)
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {
        cpu0: {
            1.0: {group: {metric: 10}},
            2.0: {group: {metric: 20}},
            None: {group: {metric: 30}},
        }
    }
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "write_csv_with_intervals.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_deep_topdown_tree_levels(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Test output with group1, two metrics, and verify the tree levels via reference.
    """
    cpu0 = Cpu(0)
    group = db.groups["stage1_left_group"]
    metric1 = db.metrics["stage1_left_lv1_metric"]
    metric2 = db.metrics["stage1_left_lv2_left_metric"]
    computed_metrics = {cpu0: {3.0: {group: {metric1: 10, metric2: 20}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "deep_topdown_tree_levels.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_metrics_in_multiple_groups(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Metric present in both group1 and group2. Output must match reference.
    """
    cpu0 = Cpu(0)
    group1 = db.groups["topdown_root_group"]
    group2 = db.groups["stage2_group1"]
    metric2 = db.metrics["shared_metric1"]
    computed_metrics = {cpu0: {4.0: {group1: {metric2: 99}, group2: {metric2: 99}}}}
    capture_groups = [group1, group2]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "metrics_in_multiple_groups.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_groups_outside_stage1_and_stage2(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Group not part of any stage: verify reference CSV (should have blank stage/level).
    """
    cpu0 = Cpu(0)
    group = db.groups["freestanding_group"]
    metric = db.metrics["freestanding_metric1"]
    computed_metrics = {cpu0: {5.0: {group: {metric: 55}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "groups_outside_stage1_and_stage2.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_missing_values(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Value is None; must produce blank field in CSV.
    """
    cpu0 = Cpu(0)
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {cpu0: {6.0: {group: {metric: None}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "missing_values.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_groupview_handling(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Use GroupView for group1 (extra row): output must match reference.
    """
    group = db.groups["topdown_root_group"]
    view = GroupView.from_group(group, list(group.metrics))
    cpu0 = Cpu(0)
    metric = db.metrics["root_metric1"]
    computed_metrics = {cpu0: {8.0: {group: {metric: 88}}}}
    capture_groups = [group, view]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / "testcpu_core_0_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer", "groupview_handling.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_write_csv_pid_tracking(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Per-core only: create computed_metrics for one core and verify CSV output.
    """
    uncore = Uncore()
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {uncore: {1.0: {group: {metric: 100}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    out_path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(out_path)
    reference_path = get_fixture_path("cpu_csv_renderer_pid_tracking", "write_csv.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_write_csv_with_intervals_pid_tracking(
    tmp_path, db, cpu_csv_renderer, regen_reference_mode
):
    """
    Multiple timestamps for the same core: verify CSV reference.
    """
    uncore = Uncore()
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {
        uncore: {
            1.0: {group: {metric: 10}},
            2.0: {group: {metric: 20}},
            None: {group: {metric: 30}},
        }
    }
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path(
        "cpu_csv_renderer_pid_tracking", "write_csv_with_intervals.csv"
    )
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_deep_topdown_tree_levels_pid_tracking(
    tmp_path, db, cpu_csv_renderer, regen_reference_mode
):
    """
    Test output with group1, two metrics, and verify the tree levels via reference.
    """
    uncore = Uncore()
    group = db.groups["stage1_left_group"]
    metric1 = db.metrics["stage1_left_lv1_metric"]
    metric2 = db.metrics["stage1_left_lv2_left_metric"]
    computed_metrics = {uncore: {3.0: {group: {metric1: 10, metric2: 20}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path(
        "cpu_csv_renderer_pid_tracking", "deep_topdown_tree_levels.csv"
    )
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_metrics_in_multiple_groups_pid_tracking(
    tmp_path, db, cpu_csv_renderer, regen_reference_mode
):
    """
    Metric present in both group1 and group2. Output must match reference.
    """
    uncore = Uncore()
    group1 = db.groups["topdown_root_group"]
    group2 = db.groups["stage2_group1"]
    metric2 = db.metrics["shared_metric1"]
    computed_metrics = {uncore: {4.0: {group1: {metric2: 99}, group2: {metric2: 99}}}}
    capture_groups = [group1, group2]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path(
        "cpu_csv_renderer_pid_tracking", "metrics_in_multiple_groups.csv"
    )
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_groups_outside_stage1_and_stage2_pid_tracking(
    tmp_path, db, cpu_csv_renderer, regen_reference_mode
):
    """
    Group not part of any stage: verify reference CSV (should have blank stage/level).
    """
    uncore = Uncore()
    group = db.groups["freestanding_group"]
    metric = db.metrics["freestanding_metric1"]
    computed_metrics = {uncore: {5.0: {group: {metric: 55}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path(
        "cpu_csv_renderer_pid_tracking", "groups_outside_stage1_and_stage2.csv"
    )
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_missing_values_pid_tracking(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Value is None; must produce blank field in CSV.
    """
    uncore = Uncore()
    group = db.groups["topdown_root_group"]
    metric = db.metrics["root_metric1"]
    computed_metrics = {uncore: {6.0: {group: {metric: None}}}}
    capture_groups = [group]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer_pid_tracking", "missing_values.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)


def test_groupview_handling_pid_tracking(tmp_path, db, cpu_csv_renderer, regen_reference_mode):
    """
    Use GroupView for group1 (extra row): output must match reference.
    """
    group = db.groups["topdown_root_group"]
    view = GroupView.from_group(group, list(group.metrics))
    uncore = Uncore()
    metric = db.metrics["root_metric1"]
    computed_metrics = {uncore: {8.0: {group: {metric: 88}}}}
    capture_groups = [group, view]
    out_dir = tmp_path / "csv_out"
    out_dir.mkdir()
    cpu_csv_renderer.render_metric_groups(computed_metrics, capture_groups, db, str(out_dir))

    path = out_dir / f"{db.product_name.lower()}_metrics.csv"
    actual_csv = read_file_str(path)
    reference_path = get_fixture_path("cpu_csv_renderer_pid_tracking", "groupview_handling.csv")
    compare_reference(actual_csv, reference_path, regen_reference_mode)
