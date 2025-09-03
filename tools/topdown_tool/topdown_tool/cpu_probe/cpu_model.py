# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=no-member

"""This module loads a JSON file using TelemetrySpecification.load_from_json_file,
validates the configuration against a comprehensive Pydantic model, and returns a TelemetrySpecification instance.
It ensures that all telemetry configuration data conforms to expected type and relationship constraints.
"""
from pathlib import Path
import json
import os
from typing import Annotated, Any, Dict, Tuple, Union
import jsonschema
from pydantic import BaseModel, Field, StringConstraints, model_validator

# Reusable type for hexadecimal strings like "0x1A2B"
HexStr = Annotated[str, StringConstraints(pattern=r"^0x[0-9A-Fa-f]+$")]


class ProductConfiguration(BaseModel):
    """Configuration parameters for a specific product variant.

    Attributes:
        product_name (str): Human-readable product name.
        part_num (HexStr): Part number as a hexadecimal string.
        implementer (HexStr): Implementer code as a hexadecimal string.
        major_revision (int): Major revision number.
        minor_revision (int): Minor revision number.
        num_slots (int): Number of available slots.
        num_bus_slots (int): Number of bus slots available.
        architecture (str): Underlying architecture.
        pmu_architecture (str): PMU architecture string.
    """

    product_name: str = Field(..., description="Human-readable product name")
    part_num: HexStr = Field(..., description="Part number, as a hex string")
    implementer: HexStr = Field(..., description="Implementer code, as a hex string")
    major_revision: int = Field(..., description="Major revision number")
    minor_revision: int = Field(..., description="Minor revision number")
    num_slots: int = Field(..., description="Number of slots available")
    num_bus_slots: int = Field(..., description="Number of bus slots available")
    architecture: str = Field(..., description="Underlying architecture")
    pmu_architecture: str = Field(..., description="PMU architecture string")


class Event(BaseModel):
    """Definition of a performance-monitoring event.

    Attributes:
        code (HexStr): Event code in hexadecimal.
        title (str): Short title of the event.
        description (str): Detailed description.
        architecture_defined (bool): Flag for architecture-defined event.
        product_defined (bool): Flag for product-defined event.
        accesses (Tuple[str, ...]): List of access types (e.g. read, write).
    """

    code: HexStr = Field(..., description="Event code, as a hex string")
    title: str = Field(..., description="Short title of the event")
    description: str = Field(..., description="Detailed description")
    architecture_defined: bool = Field(..., description="Is this an architecture-defined event?")
    product_defined: bool = Field(..., description="Is this an product-defined event?")
    accesses: Tuple[str, ...] = Field(..., description="List of access types (e.g., read, write)")


class Metric(BaseModel):
    """Computed metric based on one or more events.

    Attributes:
        title (str): Short title of the metric.
        formula (str): Formula to compute the metric.
        description (str): Detailed metric description.
        units (str): Units for the metric value.
        events (Tuple[str, ...]): Event identifiers used in the formula.
        sample_events (Tuple[str, ...]): Sample events for demonstration.
    """

    title: str = Field(..., description="Short title of the metric")
    formula: str = Field(..., description="Formula expressing how to compute the metric")
    description: str = Field(..., description="Detailed description")
    units: str = Field(..., description="Units for the metric value")
    events: Tuple[str, ...] = Field(..., description="Event identifiers used in the formula")
    sample_events: Tuple[str, ...] = Field(..., description="Sample events for demonstration")


class FunctionGroup(BaseModel):
    """Grouping of events by functional domain.

    Attributes:
        title (str): Title of the function group.
        description (str): Description of the group.
        events (Tuple[str, ...]): List of event identifiers.
    """

    title: str = Field(..., description="Title of the function group")
    description: str = Field(..., description="Description of what this group represents")
    events: Tuple[str, ...] = Field(..., description="List of event identifiers in this group")


class MetricGroup(BaseModel):
    """Grouping of metrics for higher-level analysis.

    Attributes:
        title (str): Title of the metric group.
        description (str): Description of the metric grouping.
        metrics (Tuple[str, ...]): List of metric identifiers.
    """

    title: str = Field(..., description="Title of the metric group")
    description: str = Field(..., description="Description of this metric grouping")
    metrics: Tuple[str, ...] = Field(..., description="List of metric identifiers in this group")


class TopdownMethodologyNode(BaseModel):
    """A single node in the top-down decision tree.

    Attributes:
        name (str): Unique node name.
        group (str): Metric group name the node belongs to.
        next_items (Tuple[str, ...]): Names of subsequent nodes.
        sample_events (Tuple[str, ...]): Sample events for demonstration.
    """

    name: str = Field(..., description="Unique name of the node")
    group: str = Field(..., description="Metric group this node belongs to")
    next_items: Tuple[str, ...] = Field(..., description="Names of subsequent nodes")
    sample_events: Tuple[str, ...] = Field(..., description="Sample events for demonstration")


class MetricGrouping(BaseModel):
    """Stages for grouping metrics in the methodology.

    Attributes:
        stage_1 (Tuple[str, ...]): Metrics for stage 1.
        stage_2 (Tuple[str, ...]): Metrics for stage 2.
    """

    stage_1: Tuple[str, ...] = Field(..., description="Metrics for stage 1")
    stage_2: Tuple[str, ...] = Field(..., description="Metrics for stage 2")


class DecisionTree(BaseModel):
    """Structure of the decision tree for the methodology.

    Attributes:
        root_nodes (Tuple[str, ...]): Names of root nodes.
        metrics (Tuple[TopdownMethodologyNode, ...]): List of decision tree nodes.
    """

    root_nodes: Tuple[str, ...] = Field(..., description="Names of root nodes")
    metrics: Tuple[TopdownMethodologyNode, ...] = Field(
        ..., description="Mapping from node name to node definition"
    )


class TopdownMethodology(BaseModel):
    """Top-down performance analysis methodology.

    Attributes:
        title (str): Title of the methodology.
        description (str): Detailed description.
        metric_grouping (MetricGrouping): Grouping settings for metrics.
        decision_tree (DecisionTree): Structure of the decision tree.
    """

    title: str = Field(..., description="Title of the methodology")
    description: str = Field(..., description="Detailed description")
    metric_grouping: MetricGrouping = Field(..., description="How metrics are grouped by stage")
    decision_tree: DecisionTree = Field(..., description="Decision tree structure")


class Groups(BaseModel):
    """Collections of function and metric groups.

    Attributes:
        function (Dict[str, FunctionGroup]): Mapping of function groups.
        metrics (Dict[str, MetricGroup]): Mapping of metric groups.
    """

    function: Dict[str, FunctionGroup] = Field(
        ..., description="Mapping from function-group name to definition"
    )
    metrics: Dict[str, MetricGroup] = Field(
        ..., description="Mapping from metric-group name to definition"
    )


class Methodologies(BaseModel):
    """All methodology definitions.

    Attributes:
        topdown_methodology (TopdownMethodology): The top-down analysis methodology.
    """

    topdown_methodology: TopdownMethodology = Field(
        ..., description="The top-down analysis methodology"
    )


class TelemetrySpecification(BaseModel):
    """Root model for telemetry configuration JSON.

    Attributes:
        document (Dict[str, Any]): Arbitrary document metadata.
        product_configuration (ProductConfiguration): Product configuration details.
        events (Dict[str, Event]): Mapping of event IDs to events.
        metrics (Dict[str, Metric]): Mapping of metric IDs to metrics.
        groups (Groups): All function and metric groupings.
        methodologies (Methodologies): All methodology definitions.
    """

    document: Dict[str, Any] = Field(..., description="Arbitrary document metadata")
    product_configuration: ProductConfiguration = Field(..., description="Product configuration")
    events: Dict[str, Event] = Field(..., description="Mapping from event ID to Event")
    metrics: Dict[str, Metric] = Field(..., description="Mapping from metric ID to Metric")
    groups: Groups = Field(..., description="All function and metric groupings")
    methodologies: Methodologies = Field(..., description="All methodology definitions")

    @staticmethod
    def load_from_json_file(
        path: Union[str, Path], schema_dir: Union[str, Path], encoding: str = "utf-8"
    ) -> "TelemetrySpecification":
        """Loads a TelemetrySpecification from a JSON file.

        Args:
            path (Union[str, Path]): Path to the JSON file.
            encoding (str): File encoding; defaults to 'utf-8'.

        Returns:
            TelemetrySpecification: The parsed telemetry configuration.

        Raises:
            ValueError: If the JSON cannot be parsed into a TelemetrySpecification.
        """
        try:
            with open(path, encoding=encoding) as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON file {path} content is not valid. Please check the error at line {e.lineno}, column {e.colno}: {e.msg}") from e
                if "$schema" in data:
                    schema_path = os.path.join(schema_dir, data["$schema"])
                    with open(schema_path, encoding="utf-8") as schema_file:
                        try:
                            schema = json.load(schema_file)
                        except json.JSONDecodeError as e:
                            raise ValueError(f"Schema file {schema_path} content is not valid. Please check the error at line {e.lineno}, column {e.colno}: {e.msg}") from e

                    try:
                        jsonschema.validate(instance=data, schema=schema)
                    except jsonschema.exceptions.ValidationError as e:
                        raise ValueError(f"Schema validation failed for {path} with the following error: {e.message}\nPlease check the JSON file and the schema.") from e

                try:
                    result = TelemetrySpecification.model_validate(data)
                except Exception as e:
                    raise ValueError(f"Schema validation failed for {path} with the following error: {e}\nPlease check the JSON file and the schema.") from e

                return result
        except FileNotFoundError as e:
            raise FileNotFoundError(f"File not found: {e.filename}. Please check that the file exists.") from e

    @model_validator(mode="after")
    def _validate_metrics_events(self) -> "TelemetrySpecification":
        # Validates that all metrics reference defined events.
        # Raises: ValueError if a metric references an undefined event.
        defined_events = set(self.events.keys())
        errors = []
        for m_name, metric in self.metrics.items():
            # check the .events list
            missing = set(metric.events) - defined_events
            if missing:
                errors.append(f"Metric '{m_name}' references undefined events: {missing}")
            # (optionally) also sample_events
            missing_samples = set(metric.sample_events) - defined_events
            if missing_samples:
                errors.append(
                    f"Metric '{m_name}' references undefined sample_events: {missing_samples}"
                )

        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_function_groups_events(self) -> "TelemetrySpecification":
        # Validates that all function groups reference defined events.
        # Raises: ValueError if a function group references an undefined event.
        defined_events = set(self.events.keys())
        for fg_name, function_group in self.groups.function.items():
            missing = set(function_group.events) - defined_events
            if missing:
                raise ValueError(
                    f"Function group '{fg_name}' references undefined events: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _validate_metric_groups_metrics(self) -> "TelemetrySpecification":
        # Validates that all metric groups reference defined metrics.
        # Raises: ValueError if a metric group references an undefined metric.
        defined_metrics = set(self.metrics.keys())
        for mg_name, metric_group in self.groups.metrics.items():
            missing = set(metric_group.metrics) - defined_metrics
            if missing:
                raise ValueError(
                    f"Metric group '{mg_name}' references undefined metrics: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _validate_metric_grouping(self) -> "TelemetrySpecification":
        # Validates the MetricGrouping settings.
        # Raises: ValueError if stage settings contain undefined or duplicate groups.
        # Constraints:
        # - All metric identifiers in stage_1 and stage_2 are defined in the groups.metrics dictionary.
        # - No group identifier appears in both stage_1 and stage_2.
        mg = self.methodologies.topdown_methodology.metric_grouping
        defined_metrics_groups = set(self.groups.metrics.keys())
        stage_1 = set(mg.stage_1)
        stage_2 = set(mg.stage_2)
        errors = []
        missing_stage_1 = stage_1 - defined_metrics_groups
        if missing_stage_1:
            errors.append(f"stage_1 contains undefined metrics groups: {missing_stage_1}")
        missing_stage_2 = stage_2 - defined_metrics_groups
        if missing_stage_2:
            errors.append(f"stage_2 contains undefined metrics groups: {missing_stage_2}")
        intersection = stage_1.intersection(stage_2)
        if intersection:
            errors.append(f"A metric cannot be defined in both stage_1 and stage_2: {intersection}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_decision_tree_root_nodes(self) -> "TelemetrySpecification":
        # Validates that decision tree root nodes reference defined metrics.
        # Raises: ValueError if a root node is undefined.
        defined_metrics = set(self.metrics.keys())
        root_nodes = set(self.methodologies.topdown_methodology.decision_tree.root_nodes)
        missing = root_nodes - defined_metrics
        if missing:
            raise ValueError(f"Decision tree root_nodes contain undefined metrics: {missing}")
        return self

    @model_validator(mode="after")
    def _validate_decision_tree_metrics(self) -> "TelemetrySpecification":
        # Validates the decision tree nodes for consistency.
        # Raises: ValueError if any decision tree node is invalid.
        # Node constraints:
        # - 'name' must be a key of the top-level metrics dictionary.
        # - 'group' must be a key in groups.metrics and the node's name must be included in that MetricGroup's metrics list.
        # - Every string in 'next_items' must be either a key in groups.metrics or a key in the top-level metrics dictionary.
        dt = self.methodologies.topdown_methodology.decision_tree
        errors = []
        defined_metrics = set(self.metrics.keys())
        defined_metric_groups = set(self.groups.metrics.keys())
        for node in dt.metrics:
            # Validate 'name'
            if node.name not in defined_metrics:
                errors.append(f"Decision tree node '{node.name}' is not defined in metrics.")
            # Validate 'group'
            if node.group not in defined_metric_groups:
                errors.append(
                    f"Decision tree node '{node.name}' has group '{node.group}' which is not defined in groups.metrics."
                )
            else:
                group_metrics = set(self.groups.metrics[node.group].metrics)
                if node.name not in group_metrics:
                    errors.append(
                        f"Decision tree node '{node.name}' is not listed in its group '{node.group}' metrics."
                    )
            # Validate 'next_items'
            for item in node.next_items:
                if item not in defined_metrics and item not in defined_metric_groups:
                    errors.append(
                        f"Decision tree node '{node.name}' has next_item '{item}' which is neither a defined metric nor a defined metric group."
                    )
        if errors:
            raise ValueError("; ".join(errors))
        return self


if __name__ == "__main__":
    import sys
    from rich.console import Console

    if len(sys.argv) != 3:
        sys.stderr.write(f"Usage: {sys.argv[0]} path_to_json_file path_to_schemas_dir\n")
        sys.exit(1)
    json_path = sys.argv[1]
    schemas_dir = sys.argv[2]
    try:
        spec = TelemetrySpecification.load_from_json_file(json_path, schemas_dir)
        print(f"Valid TelemetrySpecification loaded from {json_path}.")
        sys.exit(0)
    except Exception as e:  # pylint: disable=broad-exception-caught
        sys.stderr.write(f"Error loading TelemetrySpecification: {e}\n")
        Console().print_exception(show_locals=True)
        sys.exit(1)
