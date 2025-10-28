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
import sys
import argparse
from typing import Annotated, Any, Dict, Iterable, List, Literal, Optional, Tuple, Union, cast
import logging
import jsonschema
from pydantic import BaseModel, Field, StringConstraints, ValidationInfo, model_validator
from rich.console import Console

# Reusable type for hexadecimal strings like "0x1A2B"
HexStr = Annotated[str, StringConstraints(pattern=r"^0x[0-9A-Fa-f]+$")]

DuplicatePolicy = Literal["error", "log"]


logger = logging.getLogger(__name__)


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
        path: Union[str, Path],
        schema_dir: Union[str, Path],
        encoding: str = "utf-8",
        duplicate_policy: DuplicatePolicy = "log",
    ) -> "TelemetrySpecification":
        """Loads a TelemetrySpecification from a JSON file.

        Args:
            path (Union[str, Path]): Path to the JSON file.
            schema_dir (Union[str, Path]): Base directory containing JSON schemas.
            encoding (str): File encoding; defaults to 'utf-8'.
            duplicate_policy (DuplicatePolicy): How to handle duplicates ("log" or "error").

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
                    raise ValueError(
                        f"JSON file {path} content is not valid. Please check the error at line {e.lineno}, column {e.colno}: {e.msg}"
                    ) from e
                if "$schema" in data:
                    schema_path = os.path.join(schema_dir, data["$schema"])
                    with open(schema_path, encoding="utf-8") as schema_file:
                        try:
                            schema = json.load(schema_file)
                        except json.JSONDecodeError as e:
                            raise ValueError(
                                f"Schema file {schema_path} content is not valid. Please check the error at line {e.lineno}, column {e.colno}: {e.msg}"
                            ) from e

                    try:
                        jsonschema.validate(instance=data, schema=schema)
                    except jsonschema.exceptions.ValidationError as e:
                        raise ValueError(
                            f"Schema validation failed for {path} with the following error: {e.message}\nPlease check the JSON file and the schema."
                        ) from e

                try:
                    result = TelemetrySpecification.model_validate(
                        data, context={"duplicate_policy": duplicate_policy}
                    )
                except Exception as e:
                    raise ValueError(
                        f"Schema validation failed for {path} with the following error: {e}\nPlease check the JSON file and the schema."
                    ) from e

                return result
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"File not found: {e.filename}. Please check that the file exists."
            ) from e

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

    @staticmethod
    def _find_duplicates(values: Tuple[str, ...]) -> Tuple[str, ...]:
        """Return a tuple of duplicated entries preserving first occurrence order."""
        seen = set()
        duplicates: List[str] = []
        for value in values:
            if value not in seen:
                seen.add(value)
            elif value not in duplicates:
                duplicates.append(value)
        return tuple(duplicates)

    @staticmethod
    def _duplicate_policy(info: ValidationInfo) -> DuplicatePolicy:
        context = info.context or {}
        policy = context.get("duplicate_policy")
        return policy if policy in ("log", "error") else "log"

    @classmethod
    def _handle_duplicates(cls, message: str, info: ValidationInfo) -> None:
        if cls._duplicate_policy(info) == "log":
            logger.warning(message)
        else:
            raise ValueError(message)

    @classmethod
    def _record_or_warn_duplicates(
        cls, message: str, info: ValidationInfo, errors: List[str]
    ) -> None:
        if cls._duplicate_policy(info) == "log":
            logger.warning(message)
        else:
            errors.append(message)

    @model_validator(mode="after")
    def _validate_function_groups_events(self, info: ValidationInfo) -> "TelemetrySpecification":
        # Validates function groups for duplicate and undefined event references.
        # Raises: ValueError if duplicates are present or an event reference is undefined.
        defined_events = set(self.events.keys())
        for fg_name, function_group in self.groups.function.items():
            duplicate_events = self._find_duplicates(function_group.events)
            if duplicate_events:
                self._handle_duplicates(
                    f"Function group '{fg_name}' defines duplicate events: {duplicate_events}",
                    info,
                )
            missing = set(function_group.events) - defined_events
            if missing:
                raise ValueError(
                    f"Function group '{fg_name}' references undefined events: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _validate_metric_groups_metrics(self, info: ValidationInfo) -> "TelemetrySpecification":
        # Validates metric groups for duplicate and undefined metric references.
        # Raises: ValueError if duplicates are present or a metric reference is undefined.
        defined_metrics = set(self.metrics.keys())
        for mg_name, metric_group in self.groups.metrics.items():
            duplicate_metrics = self._find_duplicates(metric_group.metrics)
            if duplicate_metrics:
                self._handle_duplicates(
                    f"Metric group '{mg_name}' defines duplicate metrics: {duplicate_metrics}",
                    info,
                )
            missing = set(metric_group.metrics) - defined_metrics
            if missing:
                raise ValueError(
                    f"Metric group '{mg_name}' references undefined metrics: {missing}"
                )
        return self

    @model_validator(mode="after")
    def _validate_metric_grouping(self, info: ValidationInfo) -> "TelemetrySpecification":
        # Validates the MetricGrouping settings.
        # Raises: ValueError if stages contain duplicates, undefined groups, or overlap.
        # Constraints:
        # - No duplicate metric group identifiers within stage_1 or stage_2.
        # - All identifiers in stage_1 and stage_2 are defined in groups.metrics.
        # - No group identifier appears in both stage_1 and stage_2.
        mg = self.methodologies.topdown_methodology.metric_grouping
        defined_metrics_groups = set(self.groups.metrics.keys())
        duplicate_stage_1 = self._find_duplicates(mg.stage_1)
        if duplicate_stage_1:
            self._handle_duplicates(
                f"metric_grouping stage_1 contains duplicate metric groups: {duplicate_stage_1}",
                info,
            )
        duplicate_stage_2 = self._find_duplicates(mg.stage_2)
        if duplicate_stage_2:
            self._handle_duplicates(
                f"metric_grouping stage_2 contains duplicate metric groups: {duplicate_stage_2}",
                info,
            )
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
    def _validate_decision_tree_root_nodes(self, info: ValidationInfo) -> "TelemetrySpecification":
        # Validates decision tree root nodes for duplicates and undefined metrics.
        # Raises: ValueError if a root node is duplicated or undefined.
        defined_metrics = set(self.metrics.keys())
        root_nodes = self.methodologies.topdown_methodology.decision_tree.root_nodes
        duplicate_roots = self._find_duplicates(root_nodes)
        if duplicate_roots:
            self._handle_duplicates(
                f"Decision tree root_nodes contain duplicates: {duplicate_roots}", info
            )
        missing = set(root_nodes) - defined_metrics
        if missing:
            raise ValueError(f"Decision tree root_nodes contain undefined metrics: {missing}")
        return self

    @model_validator(mode="after")
    def _validate_decision_tree_metrics(self, info: ValidationInfo) -> "TelemetrySpecification":
        # Validates decision tree nodes for duplicates and consistency.
        # Raises: ValueError if nodes are duplicated or reference undefined entities.
        # Node constraints:
        # - Node names must be unique and defined in metrics.
        # - 'group' must exist in groups.metrics and include the node name.
        # - Each 'next_item' must be a metric or metric group identifier.
        dt = self.methodologies.topdown_methodology.decision_tree
        errors: List[str] = []
        defined_metrics = set(self.metrics.keys())
        defined_metric_groups = set(self.groups.metrics.keys())
        duplicate_names = self._find_duplicates(tuple(node.name for node in dt.metrics))
        if duplicate_names:
            self._record_or_warn_duplicates(
                f"Decision tree metrics contain duplicate node names: {duplicate_names}",
                info,
                errors,
            )
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


def _validate_file(path: Path, schema_root: Path) -> Tuple[bool, Optional[str]]:
    path = path.resolve()
    schema_root = schema_root.resolve()
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, f"{path}: Unable to parse JSON: {exc}"

    schema_label = document.get("$schema")
    if schema_label:
        schema_path = schema_root / schema_label
        if not schema_path.exists():
            return False, f"{path}: Schema '{schema_label}' not found under {schema_root}"

    try:
        TelemetrySpecification.load_from_json_file(path, schema_root, duplicate_policy="error")
        return True, None
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return False, f"{path}: {exc}"


def _validate_directory(spec_dir: Path, schema_root: Path) -> Tuple[List[str], int]:
    spec_dir = spec_dir.resolve()
    schema_root = schema_root.resolve()
    json_files = sorted(p for p in spec_dir.rglob("*.json") if p.is_file())
    if not json_files:
        return [f"No JSON files found under {spec_dir}"], 0
    errors: List[str] = []
    for json_path in json_files:
        ok, message = _validate_file(json_path, schema_root)
        _report_validation(json_path, ok, message)
        if not ok and message:
            errors.append(message)
    return errors, len(json_files)


def _report_validation(path: Path, ok: bool, message: Optional[str]) -> None:
    """Pretty-print the validation outcome for a single file."""
    if ok:
        print(f"[PASS] {path}")
    else:
        print(f"[FAIL] {path}", file=sys.stderr)
        if message:
            print(message, file=sys.stderr)


def cli_main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate Arm CPU telemetry specification JSON files."
    )
    parser.add_argument(
        "--file",
        "-f",
        type=Path,
        help="Validate a single specification JSON file.",
    )
    parser.add_argument(
        "--spec-dir",
        "-d",
        type=Path,
        help="Recursively validate all specification JSON files under this directory.",
    )
    parser.add_argument(
        "--schema-dir",
        "-s",
        type=Path,
        help="Directory containing JSON schemas referenced by the specifications.",
        required=True,
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    file_path: Optional[Path] = args.file
    spec_dir: Optional[Path] = args.spec_dir
    schema_dir: Path = cast(Path, args.schema_dir)

    if file_path and spec_dir:
        parser.error("Use either --file or --spec-dir, not both.")

    if file_path is None and spec_dir is None:
        parser.error("Specify --file for a single spec or --spec-dir for bulk validation.")

    if file_path:
        ok, message = _validate_file(file_path, schema_dir)
        _report_validation(file_path.resolve(), ok, message)
        return 0 if ok else 1

    assert spec_dir is not None
    errors, total = _validate_directory(spec_dir, schema_dir)
    if errors:
        print("\nValidation failures:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"\nValidated {total} specification(s) successfully.")
    return 0


def main(argv: Optional[Iterable[str]] = None) -> int:
    try:
        return cli_main(argv)
    except Exception:  # pylint: disable=broad-exception-caught
        Console().print_exception(show_locals=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
