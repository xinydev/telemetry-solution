# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited
# pylint: disable=no-member, duplicate-code

"""This module loads a JSON file using TelemetrySpecification.load_from_json_file,
validates the configuration against a comprehensive Pydantic model, and returns a TelemetrySpecification instance.
It ensures that all telemetry configuration data conforms to expected type and relationship constraints.
"""
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, Iterable, List, Optional, Set, Tuple, Union, cast

from rich.console import Console

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

# Reusable type for hexadecimal strings like "0x1A2B"
HexStr = Annotated[str, StringConstraints(pattern=r"^0x[0-9A-Fa-f]+$")]


logger = logging.getLogger(__name__)


class ProductConfiguration(BaseModel):
    """Configuration parameters for a specific product variant.

    Attributes:
        product_name (str): Human-readable product name.
        major_revision (int): Major revision number.
        minor_revision (int): Minor revision number.
        device_id (int): CMN node device id.
    """
    product_name: Optional[str] = Field(None, description="Human-readable product name")
    major_revision: Optional[int] = Field(None, description="Major revision number")
    minor_revision: Optional[int] = Field(None, description="Minor revision number")
    device_id: Optional[int] = Field(None, description="CMN node device id")

    @model_validator(mode="after")
    def _xor_groups(self) -> "ProductConfiguration":
        """
        Enforce exactly one of:
          A) product_name + major_revision + minor_revision
          B) device_id
        """
        group_a_complete = all(x is not None for x in (self.product_name, self.major_revision, self.minor_revision))
        group_b_complete = self.device_id is not None

        # Exclusive-or: exactly one must be true
        if group_a_complete and group_b_complete:
            raise ValueError("Provide either (product_name, major_revision, minor_revision) or device_id, but not both.")

        if not group_a_complete and not group_b_complete:
            raise ValueError("Missing required group: provide either (product_name, major_revision, minor_revision) or device_id.")

        # If device_id is used, forbid any of the other fields
        if group_b_complete:
            extras = [
                name for name, value in (
                    ("product_name", self.product_name),
                    ("major_revision", self.major_revision),
                    ("minor_revision", self.minor_revision),
                ) if value is not None
            ]
            if extras:
                raise ValueError(f"When device_id is provided, these must be omitted: {', '.join(extras)}")

        # If group A is chosen, ensure none missing (already checked), and device_id omitted
        if group_a_complete and self.device_id is not None:
            # (covered by the XOR check above, but kept for clarity)
            raise ValueError("device_id must be omitted when using name+revision.")

        return self


class FilterEncoding(BaseModel):
    """Definition of a filter encoding.

    Attributes:
        description (str): Detailed description.
        encoding (int): Filter encoding value.
    """

    description: str = Field(..., description="Detailed description")
    encoding: int = Field(..., description="Filter encoding value")


class FilterAccess(BaseModel):
    """Definition of a filter access.

    Attributes:
        register (str): Filter register.
        field (str): Filter field.
    """

    model_config = ConfigDict(populate_by_name=True)

    register_name: str = Field(..., alias="register", description="Filter register")
    field: str = Field(..., description="Filter field")


class Filter(BaseModel):
    """Definition of a filter.

    Attributes:
        description (str): Detailed description.
        encodings (Dict[str, FilterEncoding]): Filter encodings.
        access (FilterAccess): Filter access.
    """

    description: str = Field(..., description="Detailed description")
    encodings: Dict[str, FilterEncoding] = Field(..., description="Filter encodings")
    access: FilterAccess = Field(..., description="Filter access")


class FilterSpecification(BaseModel):
    """Definition of a filter specification.

    Attributes:
        description (str): Detailed description.
        filters (Dict[str, Filter]): List of filters.
    """

    description: str = Field(..., description="Detailed description")
    filters: Dict[str, Filter] = Field(..., description="List of filters")


class Event(BaseModel):
    """Definition of a performance-monitoring event.

    Attributes:
        code (Optional[HexStr]): Event code in hexadecimal.
        title (str): Short title of the event.
        description (str): Detailed description.
        accesses (Tuple[str, ...]): List of access types (e.g. read, write).
        architecture_defined (bool): Flag for architecture-defined event.
        product_defined (bool): Flag for product-defined event.
        product_defined_attributes (Dict[str, Any]): Product Defined Attributes.
        system (Optional[bool]): Is system event?
    """

    code: Optional[HexStr] = Field(None, description="Event code, as a hex string")
    title: str = Field(..., description="Short title of the event")
    description: str = Field(..., description="Detailed description")
    accesses: Tuple[str, ...] = Field(..., description="List of access types (e.g., read, write)")
    architecture_defined: bool = Field(..., description="Is this an architecture-defined event?")
    product_defined: bool = Field(..., description="Is this an product-defined event?")
    product_defined_attributes: Dict[str, Any] = Field(..., description="Product Defined Attributes")
    system: Optional[bool] = Field(None, description="Is system event?")


class Watchpoint(BaseModel):
    """Definition of a performance-monitoring watchpoint.

    Attributes:
        description (str): = Detailed description.
        wp_val (HexStr): = Shifted watchpoint value aligned with the mask, as a hex string.
        wp_mask (HexStr): = Watchpoint bit field mask, as a hex string.
        field_name (Tuple[str, ...]): = Watchpoint field names.
        field_value (Tuple[int, ...]): = Watchpoint field values.
        mesh_flit_dir (str): = Watchpoint direction Upload/Download.
        wp_chn_num (str): = Watchpoint channel number.
        wp_chn_sel (str): = Watchpoint channel text representation.
        wp_dev_sel (str): = Lower bits of watchpoint port number.
        wp_dev_sel2 (str): = Higher bits of watchpoint port number.
        wp_grp (str): = Watchpoint group text representation.
    """

    description: str = Field(..., description="Detailed description")
    wp_val: HexStr = Field(..., description="Shifted watchpoint value aligned with the mask, as a hex string")
    wp_mask: HexStr = Field(..., description="Watchpoint bit field mask, as a hex string")
    field_name: Tuple[str, ...] = Field(..., description="Watchpoint field names")
    field_value: Tuple[int, ...] = Field(..., description="Watchpoint field values")
    mesh_flit_dir: str = Field(..., description="Watchpoint direction Upload/Download")
    wp_chn_num: str = Field(..., description="Watchpoint channel number")
    wp_chn_sel: str = Field(..., description="Watchpoint channel text representation")
    wp_dev_sel: str = Field(..., description="Lower bits of watchpoint port number")
    wp_dev_sel2: str = Field(..., description="Higher bits of watchpoint port number")
    wp_grp: str = Field(..., description="Watchpoint group text representation")


class Metric(BaseModel):
    """Computed metric based on one or more events.

    Attributes:
        title (str): Short title of the metric.
        formula (str): Formula to compute the metric.
        description (Optional[str]): Detailed metric description.
        units (Optional[str]): Units for the metric value.
        events (Optional[Tuple[str, ...]]): Event identifiers used in the formula.
        sample_events (Optional[Tuple[str, ...]]): Sample events for demonstration.
        watchpoints (Optional[Tuple[str, ...]]): Watchpoint identifiers used in the formula
        metrics (Optional[Tuple[str, ...]]): Metric identifiers used in the formula
    """

    title: str = Field(..., description="Short title of the metric")
    formula: str = Field(..., description="Formula expressing how to compute the metric")
    description: Optional[str] = Field(None, description="Detailed description")
    units: Optional[str] = Field(None, description="Units for the metric value")
    events: Optional[Tuple[str, ...]] = Field(None, description="Event identifiers used in the formula")
    sample_events: Optional[Tuple[str, ...]] = Field(None, description="Sample events for demonstration")
    watchpoints: Optional[Tuple[str, ...]] = Field(None, description="Watchpoint identifiers used in the formula")
    metrics: Optional[Tuple[str, ...]] = Field(None, description="Metric identifiers used in the formula")


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
        description (Optional[str]): Description of the metric grouping.
        metrics (Tuple[str, ...]): List of metric identifiers.
    """

    title: str = Field(..., description="Title of the metric group")
    description: Optional[str] = Field(None, description="Description of this metric grouping")
    metrics: Tuple[str, ...] = Field(..., description="List of metric identifiers in this group")


class Groups(BaseModel):
    """Collections of function and metric groups.

    Attributes:
        function (Optional[Dict[str, FunctionGroup]]): Mapping of function groups.
        metrics (Optional[Dict[str, MetricGroup]]): Mapping of metric groups.
    """

    function: Optional[Dict[str, FunctionGroup]] = Field(
        None, description="Mapping from function-group name to definition"
    )
    metrics: Optional[Dict[str, MetricGroup]] = Field(
        None, description="Mapping from metric-group name to definition"
    )


class Methodologies(BaseModel):
    """All methodology definitions.

    Attributes:
    """


class DeviceTelemetrySpecification(BaseModel):
    """Model for telemetry configuration for device.

    Attributes:
        product_configuration (ProductConfiguration): Product configuration details.
        filter_specification (Optional[FilterSpecification]): Filter specification for device.
        events (Dict[str, Event]): Mapping of event IDs to events.
        watchpoints (Optional[Dict[str, Watchpoint]]): Mapping from watchpoint ID to Watchpoint.
        metrics (Dict[str, Metric]): Mapping of metric IDs to metrics.
        groups (Groups): All function and metric groupings.
        methodologies (Methodologies): All methodology definitions.
    """

    product_configuration: ProductConfiguration = Field(..., description="Product configuration")
    filter_specification: Optional[FilterSpecification] = Field(None, description="Filter specification for device")
    events: Dict[str, Event] = Field(..., description="Mapping from event ID to Event")
    watchpoints: Optional[Dict[str, Watchpoint]] = Field(None, description="Mapping from watchpoint ID to Watchpoint")
    metrics: Dict[str, Metric] = Field(..., description="Mapping from metric ID to Metric")
    groups: Groups = Field(..., description="All function and metric groupings")
    methodologies: Methodologies = Field(..., description="All methodology definitions")


class TelemetrySpecification(BaseModel):
    """Root model for telemetry configuration JSON.

    Attributes:
        document (Dict[str, Any]): Arbitrary document metadata.
        product_configuration (ProductConfiguration): Product configuration details.
        events (Dict[str, Event]): Mapping of event IDs to events.
        metrics (Optional[Dict[str, Metric]]): Mapping of metric IDs to metrics.
        groups (Groups): All function and metric groupings.
        methodologies (Methodologies): All methodology definitions.
        components (Dict[str, DeviceTelemetrySpecification]): Devices specifications
    """

    document: Dict[str, Any] = Field(..., description="Arbitrary document metadata")
    product_configuration: ProductConfiguration = Field(..., description="Product configuration")
    events: Dict[str, Event] = Field(..., description="Mapping from event ID to Event")
    metrics: Optional[Dict[str, Metric]] = Field(None, description="Mapping from metric ID to Metric")
    groups: Groups = Field(..., description="All function and metric groupings")
    methodologies: Methodologies = Field(..., description="All methodology definitions")
    components: Dict[str, DeviceTelemetrySpecification] = Field(..., description="Devices specifications")

    @staticmethod
    def load_from_json_file(
        path: Union[str, Path],
        schema_dir: Union[str, Path],
        encoding: str = "utf-8",
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
                        # jsonschema.validate(instance=data, schema=schema)
                        jsonschema.Draft3Validator(schema, format_checker=jsonschema.FormatChecker())
                    except jsonschema.exceptions.ValidationError as e:
                        raise ValueError(
                            f"Schema validation failed for {path} with the following error: {e.message}\nPlease check the JSON file and the schema."
                        ) from e

                try:
                    result = TelemetrySpecification.model_validate(data)
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
        errors = []
        for device_data in self.components.values():
            events = set(device_data.events.keys())
            for metric, metric_data in device_data.metrics.items():
                metric_events = set(metric_data.events) if metric_data.events else set()
                sample_events = (
                    set(metric_data.sample_events) if metric_data.sample_events else set()
                )
                missing_events = metric_events - events
                missing_sample_events = sample_events - events
                if missing_events:
                    errors.append(f"Metric '{metric}' references undefined events: {missing_events}")
                if missing_sample_events:
                    errors.append(f"Metric '{metric}' references undefined sample events: {missing_sample_events}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_metrics_watchpoints(self) -> "TelemetrySpecification":
        # Validates that all metrics reference defined watchpoints.
        # Raises: ValueError if a metric references an undefined watchpoint.
        errors = []
        for device_data in self.components.values():
            watchpoints = set(device_data.watchpoints.keys()) if device_data.watchpoints is not None else set()
            for metric, metric_data in device_data.metrics.items():
                if not metric_data.watchpoints:
                    continue
                missing_watchpoints = set(metric_data.watchpoints) - watchpoints
                if missing_watchpoints:
                    errors.append(f"Metric '{metric}' references undefined watchpoints: {missing_watchpoints}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_function_groups_events(self) -> "TelemetrySpecification":
        # Validates function groups for duplicate and undefined event references.
        # Raises: ValueError if duplicates are present or an event reference is undefined.
        errors = []
        for device_data in self.components.values():
            if not device_data.groups.function:
                continue
            events = set(device_data.events.keys())
            for group, group_data in device_data.groups.function.items():
                duplicate_events = []
                missing_events = []
                for event in set(group_data.events):
                    if group_data.events.count(event) > 1:
                        duplicate_events.append(event)
                    if event not in events:
                        missing_events.append(event)
                if duplicate_events:
                    errors.append(f"Group '{group}' has duplicated events: {duplicate_events}")
                if missing_events:
                    errors.append(f"Group '{group}' references undefined events: {missing_events}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_metric_groups_metrics(self) -> "TelemetrySpecification":
        # Validates metric groups for duplicate and undefined metric references.
        # Raises: ValueError if duplicates are present or a metric reference is undefined.
        errors = []
        for device_data in self.components.values():
            metrics = set(device_data.metrics.keys())
            if device_data.groups.metrics is not None:
                for group, group_data in device_data.groups.metrics.items():
                    duplicate_metrics = []
                    missing_metrics = []
                    for metric in set(group_data.metrics):
                        if group_data.metrics.count(metric) > 1:
                            duplicate_metrics.append(metric)
                        if metric not in metrics:
                            missing_metrics.append(metric)
                    if duplicate_metrics:
                        errors.append(f"Group '{group}' has duplicated metrics: {duplicate_metrics}")
                    if missing_metrics:
                        errors.append(f"Group '{group}' references undefined metrics: {missing_metrics}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_topdown_metrics(self) -> "TelemetrySpecification":
        # Validates topdown metrics for undefined metric references.
        # Raises: ValueError if metric reference is undefined.
        if self.metrics is None:
            return self
        errors = []
        all_metrics = set(self.metrics.keys())
        for device_data in self.components.values():
            all_metrics.update(device_data.metrics.keys())
        for topdown_metric, topdown_metric_data in self.metrics.items():
            if not topdown_metric_data.metrics:
                continue
            missing_metrics = set(topdown_metric_data.metrics) - all_metrics
            if missing_metrics:
                errors.append(f"Topdown Metric '{topdown_metric}' references undefined metrics: {missing_metrics}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_topdown_metric_groups_metrics(self) -> "TelemetrySpecification":
        # Validates topdown metric groups for duplicate and undefined metric references.
        # Raises: ValueError if duplicates are present or a metric reference is undefined.
        errors = []
        metrics = set(self.metrics.keys()) if self.metrics is not None else set()
        for device_data in self.components.values():
            metrics.update(set(device_data.metrics.keys()))
        if self.groups.metrics is not None:
            for topdown_group, topdown_group_data in self.groups.metrics.items():
                duplicate_metrics = []
                missing_metrics = []
                for metric in set(topdown_group_data.metrics):
                    if topdown_group_data.metrics.count(metric) > 1:
                        duplicate_metrics.append(metric)
                    if metric not in metrics:
                        missing_metrics.append(metric)
                if duplicate_metrics:
                    errors.append(f"Topdown Group '{topdown_group}' has duplicated metrics: {duplicate_metrics}")
                if missing_metrics:
                    errors.append(f"Topdown Group '{topdown_group}' references undefined metrics: {missing_metrics}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_events_duplicates(self) -> "TelemetrySpecification":
        # Validates all devices for duplicate events
        # Raises: ValueError if duplicates are present
        errors = []
        all_events: Set[str] = set()
        for device, device_data in self.components.items():
            device_events = set(device_data.events.keys())
            device_events.discard("SYS_CMN_CYCLES")
            duplicate_events = all_events.intersection(device_events)
            all_events.update(device_events)
            if duplicate_events:
                errors.append(f"Device {device} has duplicated events: {duplicate_events}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_watchpoints_duplicates(self) -> "TelemetrySpecification":
        # Validates all devices for duplicate watchpoints
        # Raises: ValueError if duplicates are present
        errors = []
        all_watchpoints: Set[str] = set()
        for device, device_data in self.components.items():
            if not device_data.watchpoints:
                continue
            device_watchpoints = set(device_data.watchpoints.keys())
            duplicate_watchpoints = all_watchpoints.intersection(device_watchpoints)
            all_watchpoints.update(device_watchpoints)
            if duplicate_watchpoints:
                errors.append(f"Device {device} has duplicated watchpoints: {duplicate_watchpoints}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_metrics_duplicates(self) -> "TelemetrySpecification":
        # Validates all devices for duplicate metrics
        # Raises: ValueError if duplicates are present
        errors = []
        all_metrics = set(self.metrics.keys()) if self.metrics is not None else set()
        for device, device_data in self.components.items():
            device_metrics = set(device_data.metrics.keys())
            duplicate_metrics = all_metrics.intersection(device_metrics)
            all_metrics.update(device_metrics)
            if duplicate_metrics:
                errors.append(f"Device {device} has duplicated metrics: {duplicate_metrics}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def _validate_groups_duplicates(self) -> "TelemetrySpecification":
        # Validates all devices for duplicate groups
        # Raises: ValueError if duplicates are present
        errors = []
        all_groups = set(self.groups.metrics.keys()) if self.groups.metrics is not None else set()
        for device, device_data in self.components.items():
            if device_data.groups.metrics is not None:
                device_groups = set(device_data.groups.metrics.keys())
                duplicate_groups = all_groups.intersection(device_groups)
                all_groups.update(device_groups)
                if duplicate_groups:
                    errors.append(f"Device {device} has duplicated groups: {duplicate_groups}")
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
        TelemetrySpecification.load_from_json_file(path, schema_root)
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
