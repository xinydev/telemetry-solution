# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import pytest
from typing import Any, Dict

from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification
from topdown_tool.cpu_probe.cpu_telemetry_database import TelemetryDatabase, GroupView


# Minimal complex fixture for a valid TelemetrySpecification
@pytest.fixture
def valid_spec() -> TelemetrySpecification:
    """
    Build a valid TelemetrySpecification instance containing events, metrics,
    groups and a non-trivial top-down methodology tree.
    """
    spec_dict: Dict[str, Any] = {
        "document": {"info": "test document"},
        "product_configuration": {
            "product_name": "Test CPU",
            "part_num": "0x1A2B",
            "implementer": "0x1C3D",
            "major_revision": 1,
            "minor_revision": 0,
            "num_slots": 4,
            "num_bus_slots": 2,
            "architecture": "x86",
            "pmu_architecture": "PMU-v1",
        },
        "events": {
            "evt1": {
                "code": "0x1",
                "title": "Event One",
                "description": "First test event",
                "common": True,
                "architectural": False,
                "impdef": False,
                "accesses": ("read",),
            },
            "evt2": {
                "code": "0x2",
                "title": "Event Two",
                "description": "Second test event",
                "common": False,
                "architectural": True,
                "impdef": False,
                "accesses": ("write",),
            },
        },
        "metrics": {
            "metric1": {
                "title": "Metric One",
                "formula": "evt1 + evt2",
                "description": "Test metric using two events.",
                "units": "units",
                "events": ("evt1", "evt2"),
                "sample_events": ("evt1",),
            },
        },
        "groups": {
            "function": {},
            "metrics": {
                "group1": {
                    "title": "Group One",
                    "description": "Test metric group",
                    "metrics": ("metric1",),
                },
            },
        },
        "methodologies": {
            "topdown_methodology": {
                "title": "Topdown Test Methodology",
                "description": "A non-trivial methodology for testing.",
                "metric_grouping": {
                    "stage_1": ("group1",),
                    "stage_2": (),
                },
                "decision_tree": {
                    "root_nodes": ("metric1",),
                    "metrics": [
                        {
                            "name": "metric1",
                            "group": "group1",
                            "next_items": (),
                            "sample_events": ("evt1",),
                        },
                    ],
                },
            },
        },
    }
    # Create TelemetrySpecification instance using model_validate
    return TelemetrySpecification.model_validate(spec_dict)


def test_events_population(valid_spec):
    """
    Test that TelemetryDatabase correctly populates the events from a valid specification.
    """
    db = TelemetryDatabase(valid_spec)
    assert "evt1" in db.events
    assert "evt2" in db.events
    assert db.events["evt1"].title == "Event One"


def test_metrics_population(valid_spec):
    """
    Verify that TelemetryDatabase parses metrics correctly and
    TelemetryDatabase.find_metric returns the expected metric.
    """
    db = TelemetryDatabase(valid_spec)
    metric = db.find_metric("metric1")
    assert metric is not None
    assert metric.title == "Metric One"
    # Test normalized lookup
    assert db.find_metric("METRIC_1") is not None


def test_groups_population(valid_spec):
    """
    Ensure that TelemetryDatabase correctly loads metric groups and that
    get_close_group_match returns a close match when queried.
    """
    db = TelemetryDatabase(valid_spec)
    # Direct lookup
    group = db.groups.get("group1")
    assert group is not None
    # Test normalization and similarity
    close_match = db.get_close_group_match("GroupOne")
    assert close_match == "group1"


def test_topdown_methodology_tree(valid_spec):
    """
    Check that the top-down methodology tree is built correctly and that
    find_node returns the correct decision node.
    """
    db = TelemetryDatabase(valid_spec)
    node = db.topdown.find_node("metric1")
    assert node is not None
    # Validate that the node's group is consistent with the group's definition.
    assert node.group.name == "group1"
    # Test that root_metrics contains the expected metric.
    assert any(m.name == "metric1" for m in db.topdown.root_metrics)


def test_invalid_group_lookup(valid_spec):
    """
    Test that get_groups raises an exception when an invalid group name is provided.
    """
    db = TelemetryDatabase(valid_spec)
    with pytest.raises(Exception) as excinfo:
        db.get_groups(["nonexistent_group"])
    assert "is not a valid group" in str(excinfo.value)


def test_get_close_metric_match_edge(valid_spec):
    """
    Validate that get_close_metric_match returns a close match for mistyped metric names.
    """
    db = TelemetryDatabase(valid_spec)
    close = db.get_close_metric_match("metricone")
    assert close == "metric1"


def test_find_group_case_insensitive(valid_spec):
    """
    Ensure that find_group works case insensitively.
    """
    db = TelemetryDatabase(valid_spec)
    group = db.find_group("GROUP1")
    assert group is not None
    assert group.name == "group1"


def test_find_metric_case_insensitive(valid_spec):
    """
    Ensure that find_metric works case insensitively.
    """
    db = TelemetryDatabase(valid_spec)
    metric = db.find_metric("METRIC1")
    assert metric is not None
    assert metric.name == "metric1"


def test_get_groups_empty(valid_spec):
    """
    Verify that get_groups returns an empty list when provided with an empty list.
    """
    db = TelemetryDatabase(valid_spec)
    groups = db.get_groups([])
    assert groups == ()


def test_topdown_methodology_get_stage(valid_spec):
    """
    Validate that get_stage_for_group returns the correct stage for a group.
    """
    db = TelemetryDatabase(valid_spec)
    # 'group1' is assigned to stage 1 per the specification.
    stage = db.topdown.get_stage_for_group("group1")
    assert stage == 1


def test_topdown_methodology_get_all_parents(valid_spec):
    """
    Test that get_all_parents returns an empty tuple for a node with no parents.
    """
    db = TelemetryDatabase(valid_spec)
    node = db.topdown.find_node("metric1")
    parents = db.topdown.get_all_parents(node)
    assert parents == ()


def test_topdown_node_children(valid_spec):
    """
    Confirm that a TopdownMethodology.Node with no next_items has an empty children list.
    """
    db = TelemetryDatabase(valid_spec)
    node = db.topdown.find_node("metric1")
    assert node is not None
    assert node.children == ()


def test_topdown_node_metric_property(valid_spec):
    """
    Ensure that the Node.metric property returns the correct Metric.
    """
    db = TelemetryDatabase(valid_spec)
    node = db.topdown.find_node("metric1")
    assert node is not None
    m = node.metric
    assert m.name == "metric1"


def test_metric_cached_properties(valid_spec):
    """
    Validate Metric cached properties (stage and groups).
    """
    db = TelemetryDatabase(valid_spec)
    m = db.find_metric("metric1")
    assert m is not None
    # stage property should be calculated and cached (expected stage 1)
    stage = m.stage
    assert stage == 1
    groups = m.groups
    assert isinstance(groups, tuple)
    assert any(g.name == "group1" for g in groups)


def test_group_cached_properties(valid_spec):
    """
    Validate Group cached property for events.
    """
    db = TelemetryDatabase(valid_spec)
    g = db.groups.get("group1")
    assert g is not None
    events = g.events
    event_names = {e.name for e in events}
    # Group 'group1' metric has events evt1 and evt2.
    assert event_names == {"evt1", "evt2"}


def test_group_view(valid_spec):
    """
    Test creation of a GroupView and validate its properties.
    """
    db = TelemetryDatabase(valid_spec)
    g = db.groups.get("group1")
    assert g is not None
    # Create a view containing only metric1.
    view = GroupView.from_group(g, [db.metrics["metric1"]])
    assert view.metrics == (db.metrics["metric1"],)
    events = view.events
    event_names = {e.name for e in events}
    assert event_names == {"evt1", "evt2"}


def test_get_close_metric_match_no_match(valid_spec):
    """
    Test that get_close_metric_match returns None when no close match is found.
    """
    db = TelemetryDatabase(valid_spec)
    close = db.get_close_metric_match("nonexistent_metric")
    assert close is None


def test_topdown_find_node_invalid(valid_spec):
    """
    Test that find_node returns None for a non-existent node.
    """
    db = TelemetryDatabase(valid_spec)
    node = db.topdown.find_node("nonexistent_node")
    assert node is None


def test_event_construction(valid_spec):
    """
    Validate that the Event instance is correctly constructed via TelemetryDatabase,
    and that perf_name returns the expected performance-compatible name.
    """
    db = TelemetryDatabase(valid_spec)
    evt = db.events["evt1"]
    assert evt.name == "evt1"
    assert evt.title == "Event One"
    # int('0x1', 16) equals 1 so expected perf name is "r1" (in hex lower-case)
    assert evt.perf_name() == f"r{int('0x1', 16):x}"


def test_metric_ordering(valid_spec):
    """
    Validate that Metric construction orders both events and sample_events.
    """
    db = TelemetryDatabase(valid_spec)
    metric = db.metrics["metric1"]
    event_names = [e.name for e in metric.events]
    sample_event_names = [e.name for e in metric.sample_events]
    # Expected order is alphabetically sorted.
    assert event_names == sorted(event_names)
    assert sample_event_names == sorted(sample_event_names)


def test_group_construction(valid_spec):
    """
    Validate that the Group instance is correctly constructed via TelemetryDatabase.
    """
    db = TelemetryDatabase(valid_spec)
    group = db.groups["group1"]
    assert group.name == "group1"
    assert group.title == "Group One"
    # Check that Group.events are sorted
    event_names = [e.name for e in group.events]
    assert event_names == sorted(event_names)


def test_group_view_original(valid_spec):
    """
    Validate that the GroupView exposes the 'original' property correctly.
    """
    db = TelemetryDatabase(valid_spec)
    group = db.groups["group1"]
    view = GroupView.from_group(group, [db.metrics["metric1"]])
    assert view.original == group


def test_product_name(valid_spec):
    """
    Validate that TelemetryDatabase.product_name is correctly populated.
    """
    db = TelemetryDatabase(valid_spec)
    assert db.product_name == "Test CPU"


def test_find_group_nonexistent(valid_spec):
    """
    Validate that find_group returns None when the group does not exist.
    """
    db = TelemetryDatabase(valid_spec)
    group = db.find_group("nonexistent")
    assert group is None


def test_find_metric_nonexistent(valid_spec):
    """
    Validate that find_metric returns None when the metric does not exist.
    """
    db = TelemetryDatabase(valid_spec)
    metric = db.find_metric("nonexistent")
    assert metric is None


def test_get_close_group_match_no_match(valid_spec):
    """
    Validate that get_close_group_match returns None when no match is found.
    """
    db = TelemetryDatabase(valid_spec)
    match = db.get_close_group_match("zzzz")
    assert match is None


def test_get_groups_mixed(valid_spec):
    """
    Validate that get_groups raises an Exception when provided a mix of valid and invalid group names.
    """
    db = TelemetryDatabase(valid_spec)
    with pytest.raises(Exception) as excinfo:
        db.get_groups(["group1", "invalid"])
    assert "is not a valid group" in str(excinfo.value)


@pytest.fixture
def complex_spec() -> TelemetrySpecification:
    """
    Build a complex TelemetrySpecification containing extra events, metrics, groups,
    and a decision tree with a multi-parent relationship.
    In this spec, metric2 is the common child of both metric1 and metric3.
    Additionally, multiple events ("evt_standalone1", "evt_standalone2", "evt_standalone3")
    are added as standalone events not referenced by any metric.
    """
    spec_dict: Dict[str, Any] = {
        "document": {"info": "complex test document"},
        "product_configuration": {
            "product_name": "Complex Test CPU",
            "part_num": "0x1A2B",
            "implementer": "0x1C3D",
            "major_revision": 1,
            "minor_revision": 0,
            "num_slots": 8,
            "num_bus_slots": 4,
            "architecture": "x86",
            "pmu_architecture": "PMU-v2",
        },
        "events": {
            "evt1": {
                "code": "0x1",
                "title": "Event One",
                "description": "First test event",
                "common": True,
                "architectural": False,
                "impdef": False,
                "accesses": ("read",),
            },
            "evt2": {
                "code": "0x2",
                "title": "Event Two",
                "description": "Second test event",
                "common": False,
                "architectural": True,
                "impdef": False,
                "accesses": ("write",),
            },
            "evt3": {
                "code": "0x3",
                "title": "Event Three",
                "description": "Third test event",
                "common": True,
                "architectural": True,
                "impdef": False,
                "accesses": ("read",),
            },
            "evt_standalone1": {
                "code": "0xA",
                "title": "Standalone Event 1",
                "description": "First standalone event",
                "common": False,
                "architectural": False,
                "impdef": False,
                "accesses": ("read",),
            },
            "evt_standalone2": {
                "code": "0xB",
                "title": "Standalone Event 2",
                "description": "Second standalone event",
                "common": False,
                "architectural": False,
                "impdef": False,
                "accesses": ("write",),
            },
            "evt_standalone3": {
                "code": "0xC",
                "title": "Standalone Event 3",
                "description": "Third standalone event",
                "common": False,
                "architectural": False,
                "impdef": False,
                "accesses": ("read",),
            },
        },
        "metrics": {
            "metric1": {
                "title": "Metric One",
                "formula": "evt1",
                "description": "First metric",
                "units": "u",
                "events": ("evt1",),
                "sample_events": ("evt1",),
            },
            "metric2": {
                "title": "Metric Two",
                "formula": "evt2",
                "description": "Second metric",
                "units": "u",
                "events": ("evt2",),
                "sample_events": ("evt2",),
            },
            "metric3": {
                "title": "Metric Three",
                "formula": "evt3",
                "description": "Third metric",
                "units": "u",
                "events": ("evt3",),
                "sample_events": ("evt3",),
            },
        },
        "groups": {
            "function": {},
            "metrics": {
                "group1": {
                    "title": "Group One",
                    "description": "Group for metric1 and metric2",
                    "metrics": ("metric1", "metric2"),
                },
                "group2": {
                    "title": "Group Two",
                    "description": "Group for metric3 and metric2",
                    "metrics": ("metric3", "metric2"),
                },
            },
        },
        "methodologies": {
            "topdown_methodology": {
                "title": "Complex Topdown Methodology",
                "description": "A non-trivial methodology for complex testing with multi-parent node.",
                "metric_grouping": {
                    "stage_1": ("group1",),
                    "stage_2": ("group2",),
                },
                "decision_tree": {
                    "root_nodes": ("metric1", "metric3"),
                    "metrics": [
                        {
                            "name": "metric1",
                            "group": "group1",
                            "next_items": ("metric2",),
                            "sample_events": ("evt1",),
                        },
                        {
                            "name": "metric3",
                            "group": "group2",
                            "next_items": ("metric2",),
                            "sample_events": ("evt3",),
                        },
                        {
                            "name": "metric2",
                            "group": "group1",
                            "next_items": (),
                            "sample_events": ("evt2",),
                        },
                    ],
                },
            },
        },
    }
    return TelemetrySpecification.model_validate(spec_dict)


def test_topdown_methodology_parents(complex_spec):
    """
    Test that get_all_parents returns the correct parent node.
    In this complex spec, metric1 is the parent of metric2.
    """
    db = TelemetryDatabase(complex_spec)
    node2 = db.topdown.find_node("metric2")
    parents = db.topdown.get_all_parents(node2)
    assert len(parents) == 2
    assert parents[0].name == "metric1"
    assert parents[1].name == "metric3"


def test_get_all_events_groups_method(complex_spec):
    """
    Validate that get_all_events_groups appends a standalone events group.
    In this updated complex spec, there are three standalone events, so the standalone group
    should create multiple metrics based on the max_events parameter.
    """
    db = TelemetryDatabase(complex_spec)
    groups = db.get_all_events_groups(max_events=1)
    standalone_groups = [g for g in groups if g.name == "STANDALONE_EVENTS_GROUP"]
    assert len(standalone_groups) == 1
    # With max_events=1 and 3 standalone events, expect 3 metrics
    assert len(standalone_groups[0].metrics) == 3
    # Check that each metric's events contains one of the standalone events
    standalone_event_names = {
        e.name for metric in standalone_groups[0].metrics for e in metric.events
    }
    assert standalone_event_names == {
        "evt_standalone1",
        "evt_standalone2",
        "evt_standalone3",
    }


def test_topdown_node_children_extended(complex_spec):
    """
    Validate that a TopdownMethodology.Node with next_items returns the proper children.
    In this complex spec, metric1 has metric2 as its child.
    """
    db = TelemetryDatabase(complex_spec)
    node1 = db.topdown.find_node("metric1")
    assert node1 is not None
    children = node1.children
    # Expect at least one child with name "metric2"
    assert any(child.name == "metric2" for child in children)


def test_get_all_events_groups_max_events(complex_spec):
    """
    Validate that get_all_events_groups correctly honors the max_events parameter.
    In this updated complex spec, with max_events=2, the standalone events group should
    group the 3 standalone events into 2 metrics: one with 2 events and one with 1 event.
    """
    db = TelemetryDatabase(complex_spec)
    groups = db.get_all_events_groups(max_events=2)
    standalone_groups = [g for g in groups if g.name == "STANDALONE_EVENTS_GROUP"]
    assert len(standalone_groups) == 1
    metrics = standalone_groups[0].metrics
    # Expect 2 metrics: one with 2 events and one with 1 event.
    assert len(metrics) == 2
    counts = sorted(len(metric.events) for metric in metrics)
    assert counts == [1, 2]


def test_topdown_multiple_parents(complex_spec):
    """
    Validate that get_all_parents correctly identifies multiple parent nodes.
    For metric2, both metric1 and metric3 should be recognized as parents.
    """
    db = TelemetryDatabase(complex_spec)
    node2 = db.topdown.find_node("metric2")
    assert node2 is not None
    parents = db.topdown.get_all_parents(node2)
    parent_names = sorted([p.name for p in parents])
    assert parent_names == ["metric1", "metric3"]
