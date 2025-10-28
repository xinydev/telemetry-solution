# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import copy
import logging
import pytest

from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification


@pytest.fixture
def valid_document():
    """Fixture returning a valid telemetry configuration document."""
    return {
        "document": {},
        "product_configuration": {
            "product_name": "MyProduct",
            "part_num": "0x1234",
            "implementer": "0xABCD",
            "major_revision": 1,
            "minor_revision": 0,
            "num_slots": 4,
            "num_bus_slots": 2,
            "architecture": "ARMv8",
            "pmu_architecture": "ARMv8.2",
        },
        "events": {
            "EVENT1": {
                "code": "0x01",
                "title": "Event 1",
                "description": "First event",
                "common": True,
                "architecture_defined": False,
                "product_defined": False,
                "accesses": ["read", "execute"],
            }
        },
        "metrics": {
            "METRIC1": {
                "title": "Metric 1",
                "formula": "EVENT1 / 100",
                "description": "Sample metric",
                "units": "cycles",
                "events": ["EVENT1"],
                "sample_events": ["EVENT1"],
            }
        },
        "groups": {
            "function": {
                "GROUP1": {
                    "title": "Group 1",
                    "description": "Function group",
                    "events": ["EVENT1"],
                }
            },
            "metrics": {
                "METRIC_GROUP1": {
                    "title": "Metric Group 1",
                    "description": "Metrics group",
                    "metrics": ["METRIC1"],
                }
            },
        },
        "methodologies": {
            "topdown_methodology": {
                "title": "Top-Down Analysis",
                "description": "Methodology description",
                "metric_grouping": {"stage_1": ["METRIC_GROUP1"], "stage_2": []},
                "decision_tree": {
                    "root_nodes": ["METRIC1"],
                    "metrics": [
                        {
                            "name": "METRIC1",
                            "group": "METRIC_GROUP1",
                            "next_items": [],
                            "sample_events": [],
                        }
                    ],
                },
            }
        },
    }


def test_valid_spec(valid_document):
    """Test that a valid configuration loads all elements correctly."""
    config = TelemetrySpecification.model_validate(valid_document)
    # Validate document metadata
    assert isinstance(config.document, dict)
    # Validate product configuration
    pc = config.product_configuration
    assert pc.product_name == "MyProduct"
    assert pc.part_num == "0x1234"
    assert pc.implementer == "0xABCD"
    # Validate events
    assert "EVENT1" in config.events
    # Validate metrics
    assert "METRIC1" in config.metrics
    metric = config.metrics["METRIC1"]
    assert metric.title == "Metric 1"
    # Validate groups
    assert "GROUP1" in config.groups.function
    fg = config.groups.function["GROUP1"]
    assert fg.title == "Group 1"
    assert "METRIC_GROUP1" in config.groups.metrics
    mg = config.groups.metrics["METRIC_GROUP1"]
    assert mg.title == "Metric Group 1"
    # Validate methodologies
    td = config.methodologies.topdown_methodology
    assert td.title == "Top-Down Analysis"
    # Validate metric grouping
    assert td.metric_grouping.stage_1 == ("METRIC_GROUP1",)
    assert td.metric_grouping.stage_2 == ()
    # Validate decision tree
    dt = td.decision_tree
    assert dt.root_nodes == ("METRIC1",)
    assert any(node.name == "METRIC1" for node in dt.metrics)


def test_validate_metrics_events_failure(valid_document):
    """Test that an undefined event in a metric causes a validation error."""
    doc = copy.deepcopy(valid_document)
    # Remove EVENT1 so the metric reference is broken
    doc["events"].pop("EVENT1")
    with pytest.raises(ValueError, match="references undefined events"):
        TelemetrySpecification.model_validate(doc)


def test_validate_function_groups_events_failure(valid_document):
    """Test that a function group referencing an undefined event causes a validation error."""
    doc = copy.deepcopy(valid_document)
    # Replace the event from the function group
    doc["groups"]["function"]["GROUP1"]["events"] = ["EVENT2"]
    with pytest.raises(ValueError, match="Function group 'GROUP1' references undefined events"):
        TelemetrySpecification.model_validate(doc)


def test_function_group_duplicate_events(valid_document):
    """Function group with the same event twice should raise."""
    doc = copy.deepcopy(valid_document)
    doc["groups"]["function"]["GROUP1"]["events"] = ["EVENT1", "EVENT1"]
    with pytest.raises(
        ValueError, match="Function group 'GROUP1' defines duplicate events: \\('EVENT1',\\)"
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})


def test_validate_metric_groups_metrics_failure(valid_document):
    """Test that a metric group referencing an undefined metric triggers an error."""
    doc = copy.deepcopy(valid_document)
    # Remove METRIC1 so the metric group reference is broken.
    doc["metrics"].pop("METRIC1")
    with pytest.raises(
        ValueError, match="Metric group 'METRIC_GROUP1' references undefined metrics"
    ):
        TelemetrySpecification.model_validate(doc)


def test_metric_group_duplicate_metrics(valid_document):
    """Metric group with the same metric twice should raise."""
    doc = copy.deepcopy(valid_document)
    doc["groups"]["metrics"]["METRIC_GROUP1"]["metrics"] = ["METRIC1", "METRIC1"]
    with pytest.raises(
        ValueError,
        match="Metric group 'METRIC_GROUP1' defines duplicate metrics: \\('METRIC1',\\)",
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})


def test_metric_group_duplicate_metrics_logs(valid_document, caplog):
    """Duplicates should log when duplicate_policy='log'."""
    doc = copy.deepcopy(valid_document)
    doc["groups"]["metrics"]["METRIC_GROUP1"]["metrics"] = ["METRIC1", "METRIC1"]
    with caplog.at_level(logging.WARNING, logger="topdown_tool.cpu_probe.cpu_model"):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "log"})
    assert "Metric group 'METRIC_GROUP1' defines duplicate metrics" in caplog.text


def test_validate_metric_grouping_failure(valid_document):
    """Test that invalid metric grouping settings cause a validation error."""
    doc = copy.deepcopy(valid_document)
    # Introduce an undefined metric group in stage_1.
    doc["methodologies"]["topdown_methodology"]["metric_grouping"]["stage_1"] = ["UNDEFINED_GROUP"]
    with pytest.raises(ValueError, match="stage_1 contains undefined metrics groups"):
        TelemetrySpecification.model_validate(doc)

    # Now test duplicate appearance in stage_1 and stage_2.
    doc = copy.deepcopy(valid_document)
    doc["methodologies"]["topdown_methodology"]["metric_grouping"]["stage_1"] = ["METRIC_GROUP1"]
    doc["methodologies"]["topdown_methodology"]["metric_grouping"]["stage_2"] = ["METRIC_GROUP1"]
    with pytest.raises(ValueError, match="A metric cannot be defined in both stage_1 and stage_2"):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})


def test_metric_grouping_duplicate_stage_entries(valid_document):
    """Duplicate metric groups within a stage should be rejected."""
    doc = copy.deepcopy(valid_document)
    doc["methodologies"]["topdown_methodology"]["metric_grouping"]["stage_1"] = [
        "METRIC_GROUP1",
        "METRIC_GROUP1",
    ]
    with pytest.raises(
        ValueError,
        match="metric_grouping stage_1 contains duplicate metric groups: \\('METRIC_GROUP1',\\)",
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})

    doc = copy.deepcopy(valid_document)
    doc["methodologies"]["topdown_methodology"]["metric_grouping"]["stage_2"] = [
        "METRIC_GROUP1",
        "METRIC_GROUP1",
    ]
    with pytest.raises(
        ValueError,
        match="metric_grouping stage_2 contains duplicate metric groups: \\('METRIC_GROUP1',\\)",
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})


def test_validate_decision_tree_root_nodes_failure(valid_document):
    """Test that an undefined decision tree root node triggers an error."""
    doc = copy.deepcopy(valid_document)
    # Set an undefined metric as a root node.
    doc["methodologies"]["topdown_methodology"]["decision_tree"]["root_nodes"] = [
        "UNDEFINED_METRIC"
    ]
    with pytest.raises(ValueError, match="Decision tree root_nodes contain undefined metrics"):
        TelemetrySpecification.model_validate(doc)


def test_decision_tree_root_duplicate_entries(valid_document):
    """Duplicate root nodes should be rejected."""
    doc = copy.deepcopy(valid_document)
    doc["methodologies"]["topdown_methodology"]["decision_tree"]["root_nodes"] = [
        "METRIC1",
        "METRIC1",
    ]
    with pytest.raises(
        ValueError, match="Decision tree root_nodes contain duplicates: \\('METRIC1',\\)"
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})


def test_validate_decision_tree_metrics_failure(valid_document):
    """Test that errors in decision tree node setup are caught."""
    doc = copy.deepcopy(valid_document)
    # Change the decision tree node to reference an undefined metric in its name.
    dt_node = doc["methodologies"]["topdown_methodology"]["decision_tree"]["metrics"][0]
    dt_node["name"] = "UNDEFINED_METRIC"
    with pytest.raises(
        ValueError,
        match="Decision tree node 'UNDEFINED_METRIC' is not defined in metrics",
    ):
        TelemetrySpecification.model_validate(doc)

    # Test with an invalid group.
    doc = copy.deepcopy(valid_document)
    dt_node = doc["methodologies"]["topdown_methodology"]["decision_tree"]["metrics"][0]
    dt_node["group"] = "UNDEFINED_GROUP"
    with pytest.raises(
        ValueError,
        match="has group 'UNDEFINED_GROUP' which is not defined in groups.metrics",
    ):
        TelemetrySpecification.model_validate(doc)

    # Test next_items that reference neither a metric nor a metric group.
    doc = copy.deepcopy(valid_document)
    dt_node = doc["methodologies"]["topdown_methodology"]["decision_tree"]["metrics"][0]
    dt_node["next_items"] = ["NON_EXISTENT"]
    with pytest.raises(
        ValueError,
        match="has next_item 'NON_EXISTENT' which is neither a defined metric nor a defined metric group",
    ):
        TelemetrySpecification.model_validate(doc)


def test_decision_tree_duplicate_metric_nodes(valid_document):
    """Duplicate metric node names should raise."""
    doc = copy.deepcopy(valid_document)
    doc["methodologies"]["topdown_methodology"]["decision_tree"]["metrics"].append(
        {
            "name": "METRIC1",
            "group": "METRIC_GROUP1",
            "next_items": [],
            "sample_events": [],
        }
    )
    with pytest.raises(
        ValueError,
        match="Decision tree metrics contain duplicate node names: \\('METRIC1',\\)",
    ):
        TelemetrySpecification.model_validate(doc, context={"duplicate_policy": "error"})
