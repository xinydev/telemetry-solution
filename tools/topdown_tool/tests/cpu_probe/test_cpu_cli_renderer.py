# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import pytest
from io import StringIO
from rich.console import Console
import itertools

from topdown_tool.cpu_probe.cpu_cli_renderer import CpuCliRenderer
from tests.cpu_probe.helpers import get_fixture_path, compare_reference

from topdown_tool.cpu_probe.common import CpuAggregate
from topdown_tool.perf.perf import Cpu


# ------------------------
# Pytest Fixtures
# ------------------------

# test_telemetry_db is provided by conftest.py


@pytest.fixture
def test_console():
    """Fixture: Provide a deterministic Rich console and capture buffer."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=False,
        width=120,
        color_system=None,
    )
    return console, buf


@pytest.fixture
def cli_renderer(test_telemetry_db, test_console):
    console, buf = test_console
    return CpuCliRenderer(console, test_telemetry_db)


@pytest.fixture
def output_buf(test_console):
    return test_console[1]


# ------------------------
# Master Fixture for render_metric_groups_stages
# ------------------------


@pytest.fixture
def render_metric_groups_stages_fixture(test_telemetry_db):
    """
    Master fixture for render_metric_groups_stages:
    - Includes all groups (stage1, stage2, general), all their metrics
    - Aggregate and 4 CPUs (0, 2, 3, 4)
    - Each value is uniquely identifiable for its (loc, group, metric)
    Returns (computed, all_capture_groups)
    """
    db = test_telemetry_db

    # Groups and metrics (aliased for concise referencing)
    topdown_root_group = db.groups["topdown_root_group"]
    stage1_left_group = db.groups["stage1_left_group"]
    stage1_right_group = db.groups["stage1_right_group"]
    stage2_group1 = db.groups["stage2_group1"]
    stage2_group2 = db.groups["stage2_group2"]
    freestanding_group = db.groups["freestanding_group"]
    # "STANDALONE_METRICS" is added on the fly in list_metrics; we omit it here

    # Metrics
    # Root group
    root_metric1 = db.metrics["root_metric1"]
    root_metric2 = db.metrics["root_metric2"]
    shared_metric1 = db.metrics["shared_metric1"]
    # Stage 1 left
    stage1_left_lv1_metric = db.metrics["stage1_left_lv1_metric"]
    stage1_left_lv2_left_metric = db.metrics["stage1_left_lv2_left_metric"]
    stage1_left_lv2_right_metric = db.metrics["stage1_left_lv2_right_metric"]
    # Stage 1 right
    stage1_right_lv1_metric = db.metrics["stage1_right_lv1_metric"]
    # Stage 2 group 1/2
    stage_2_group1_metric1 = db.metrics["stage_2_group1_metric1"]
    stage_2_group1_metric2 = db.metrics["stage_2_group1_metric2"]
    stage_2_group2_metric1 = db.metrics["stage_2_group2_metric1"]
    shared_metric2 = db.metrics["shared_metric2"]
    # Freestanding
    freestanding_metric1 = db.metrics["freestanding_metric1"]
    freestanding_metric2 = db.metrics["freestanding_metric2"]

    # Prepare CpuAggregate and Cpu locations
    agg = CpuAggregate((Cpu(0), Cpu(2), Cpu(3), Cpu(4)))
    cpu0 = Cpu(0)
    cpu2 = Cpu(2)
    cpu3 = Cpu(3)
    cpu4 = Cpu(4)
    locations = [
        (agg, "agg", 100),
        (cpu0, "cpu0", 200),
        (cpu2, "cpu2", 300),
        (cpu3, "cpu3", 400),
        (cpu4, "cpu4", 500),
    ]

    # Helper for explicit value
    def v(base, n):
        return float(base + n)

    computed = {}

    for loc, loc_name, base in locations:
        computed[loc] = {
            None: {
                topdown_root_group: {
                    root_metric1: v(base, 1),
                    root_metric2: v(base, 2),
                    shared_metric1: v(base, 3),
                },
                stage1_left_group: {
                    stage1_left_lv1_metric: v(base, 11),
                    stage1_left_lv2_left_metric: v(base, 12),
                    stage1_left_lv2_right_metric: v(base, 13),
                },
                stage1_right_group: {
                    stage1_right_lv1_metric: v(base, 21),
                },
                stage2_group1: {
                    stage_2_group1_metric1: v(base, 31),
                    stage_2_group1_metric2: v(base, 32),
                    shared_metric1: v(base, 33),
                },
                stage2_group2: {
                    stage_2_group2_metric1: v(base, 41),
                    shared_metric2: v(base, 42),
                },
                freestanding_group: {
                    freestanding_metric1: v(base, 51),
                    freestanding_metric2: v(base, 52),
                    shared_metric2: v(base, 53),
                },
            }
        }

    all_capture_groups = [
        topdown_root_group,
        stage1_left_group,
        stage1_right_group,
        stage2_group1,
        stage2_group2,
        freestanding_group,
    ]

    return computed, all_capture_groups


# --- Filtering helper for render_metric_groups_stages_fixture ---


def filter_fixture_by_stages(computed, capture_groups, db, stages):
    """
    Use db.topdown.stage_1_groups, stage_2_groups, and all db.groups.values()
    to filter by groups themselves, not names.
    """
    groups_to_include = set()
    if 1 in stages:
        groups_to_include.update(db.topdown.stage_1_groups)
    if 2 in stages:
        groups_to_include.update(db.topdown.stage_2_groups)
    if 0 in stages:
        # Add groups not part of either stage 1 or 2
        groups_to_include.update(
            set(db.groups.values())
            - set(db.topdown.stage_1_groups)
            - set(db.topdown.stage_2_groups)
        )

    filtered_capture_groups = [g for g in capture_groups if g in groups_to_include]

    filtered_computed = {}
    for loc, timedict in computed.items():
        filtered_groups = {}
        for timed, groups in timedict.items():
            filtered_groups[timed] = {g: ms for g, ms in groups.items() if g in groups_to_include}
        filtered_computed[loc] = filtered_groups

    # Assert all requested groups are present
    for g in groups_to_include:
        assert g in filtered_capture_groups, f"Missing group {getattr(g, 'name', str(g))}"

    return filtered_computed, filtered_capture_groups


# --- Dedicated fixtures for the three cases ---


@pytest.fixture
def render_metric_groups_stages_stage_1(render_metric_groups_stages_fixture, test_telemetry_db):
    computed, capture_groups = render_metric_groups_stages_fixture
    filtered_computed, filtered_capture_groups = filter_fixture_by_stages(
        computed, capture_groups, test_telemetry_db, [1]
    )
    return filtered_computed, filtered_capture_groups


@pytest.fixture
def render_metric_groups_stages_stage_1_2(render_metric_groups_stages_fixture, test_telemetry_db):
    computed, capture_groups = render_metric_groups_stages_fixture
    filtered_computed, filtered_capture_groups = filter_fixture_by_stages(
        computed, capture_groups, test_telemetry_db, [1, 2]
    )
    return filtered_computed, filtered_capture_groups


@pytest.fixture
def render_metric_groups_stages_all(render_metric_groups_stages_fixture, test_telemetry_db):
    # 0 means "general"/other in this context (see helper)
    computed, capture_groups = render_metric_groups_stages_fixture
    filtered_computed, filtered_capture_groups = filter_fixture_by_stages(
        computed, capture_groups, test_telemetry_db, [1, 2, 0]
    )
    return filtered_computed, filtered_capture_groups


# ------------------------
# Test Cases for CLI Renderer
# ------------------------


@pytest.mark.parametrize(
    "fixture_name,stages_label",
    [
        ("render_metric_groups_stages_stage_1", "1"),
        ("render_metric_groups_stages_stage_1_2", "1,2"),
        ("render_metric_groups_stages_all", "1,2,0"),
    ],
)
@pytest.mark.parametrize("desc", [False, True])
def test_render_metric_groups_stages_reference(
    fixture_name, stages_label, desc, cli_renderer, output_buf, regen_reference_mode, request
):
    # Get (computed, capture_groups)
    computed, capture_groups = request.getfixturevalue(fixture_name)

    renderer = cli_renderer
    renderer.render_metric_groups_stages(computed, capture_groups, include_descriptions=desc)
    output = output_buf.getvalue()

    reference_path = get_fixture_path(
        "cpu_cli_renderer", "render_metric_groups_stages", f"stage_{stages_label}_desc_{desc}.txt"
    )
    compare_reference(output, reference_path, regen_reference_mode)


# --- list_events ---


@pytest.mark.parametrize("desc", [False, True])
def test_list_events_with_desc(desc, cli_renderer, output_buf, regen_reference_mode):
    """Test output of list_events(include_description=desc)."""
    cli_renderer.list_events(include_description=desc)
    output = output_buf.getvalue()
    reference_path = get_fixture_path("cpu_cli_renderer", "list_events", f"desc_{desc}.txt")
    compare_reference(output, reference_path, regen_reference_mode)


# --- list_metrics --- (permutations of description/sample events)


@pytest.mark.parametrize("desc,sample", list(itertools.product([False, True], [False, True])))
def test_list_metrics_permutations(desc, sample, cli_renderer, output_buf, regen_reference_mode):
    cli_renderer.list_metrics(include_description=desc, include_sample_events=sample)
    output = output_buf.getvalue()
    reference_path = get_fixture_path(
        "cpu_cli_renderer", "list_metrics", f"desc_{desc}_sample_events_{sample}.txt"
    )
    compare_reference(output, reference_path, regen_reference_mode)


# --- list_groups ---


@pytest.mark.parametrize(
    "desc,stages", list(itertools.product([False, True], [[], [1], [2], [1, 2]]))
)
def test_list_groups_permutations(desc, stages, cli_renderer, output_buf, regen_reference_mode):
    cli_renderer.list_groups(include_description=desc, include_stages=stages)
    output = output_buf.getvalue()
    if stages == [1, 2]:
        stages_str = "all"
    elif not stages:
        stages_str = "combined"
    else:
        stages_str = "_".join(map(str, stages))
    reference_path = get_fixture_path(
        "cpu_cli_renderer", "list_groups", f"desc_{desc}_stages_{stages_str}.txt"
    )
    compare_reference(output, reference_path, regen_reference_mode)


# --- render_metrics_tree ---


@pytest.mark.parametrize("desc", [False, True])
@pytest.mark.parametrize(
    "root_node",
    [
        None,
        "root_metric1",
        "root_metric2",
        "shared_metric1",
        "stage1_left_lv1_metric",
        "stage1_left_lv2_left_metric",
        "stage1_left_lv2_right_metric",
        "stage1_right_lv1_metric",
    ],
)
def test_render_metric_groups_tree_reference(
    render_metric_groups_stages_fixture,
    cli_renderer,
    output_buf,
    regen_reference_mode,
    desc,
    root_node,
):
    computed, _capture_groups = render_metric_groups_stages_fixture

    cli_renderer.render_metrics_tree(
        computed,
        include_descriptions=desc,
        root_node=root_node,
    )
    output = output_buf.getvalue()
    rnode = root_node if root_node else "none"
    ref_name = f"root_{rnode}_desc_{desc}.txt"
    reference_path = get_fixture_path("cpu_cli_renderer", "render_metrics_tree", ref_name)
    compare_reference(output, reference_path, regen_reference_mode)
