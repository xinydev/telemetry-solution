# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path
import warnings

import pytest

from topdown_tool.cmn_probe.cmn_model import (
    ProductConfiguration,
    _validate_directory,
    _validate_file,
)


def test_product_configuration_allows_name_revision() -> None:
    config = ProductConfiguration(product_name="CMN-700", major_revision=1, minor_revision=0)
    assert config.product_name == "CMN-700"


def test_product_configuration_allows_device_id_only() -> None:
    config = ProductConfiguration(device_id=1234)
    assert config.device_id == 1234


def test_product_configuration_rejects_both_groups() -> None:
    with pytest.raises(ValueError):
        ProductConfiguration(
            product_name="CMN-700",
            major_revision=1,
            minor_revision=0,
            device_id=1,
        )


def test_product_configuration_rejects_missing_groups() -> None:
    with pytest.raises(ValueError):
        ProductConfiguration()


def test_validate_file_reports_parse_errors(tmp_path) -> None:
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not valid json")

    ok, message = _validate_file(bad_json, tmp_path)

    assert ok is False
    assert message is not None
    assert "Unable to parse JSON" in message


def test_validate_file_reports_missing_schema(tmp_path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text('{"$schema": "missing.json"}')

    ok, message = _validate_file(spec, tmp_path)

    assert ok is False
    assert message is not None
    assert "Schema 'missing.json' not found" in message


def test_validate_file_accepts_fixture_schema() -> None:
    base_dir = Path(__file__).resolve().parent
    spec_path = base_dir / "fixtures" / "cmn-700.json"
    schema_root = base_dir.parent.parent / "topdown_tool" / "cmn_probe" / "schemas"
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message='Field name "register" in "FilterAccess" shadows an attribute in parent "BaseModel"',
        )
        ok, message = _validate_file(spec_path, schema_root)

    assert ok is True
    assert message is None


def test_validate_directory_counts_errors(tmp_path) -> None:
    base_dir = Path(__file__).resolve().parent
    spec_path = base_dir / "fixtures" / "cmn-700.json"
    schema_root = base_dir.parent.parent / "topdown_tool" / "cmn_probe" / "schemas"

    valid_spec = tmp_path / "valid.json"
    valid_spec.write_text(spec_path.read_text())

    invalid_spec = tmp_path / "invalid.json"
    invalid_spec.write_text('{"$schema": "missing.json"}')

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message='Field name "register" in "FilterAccess" shadows an attribute in parent "BaseModel"',
        )
        errors, count = _validate_directory(tmp_path, schema_root)

    assert count == 2
    assert len(errors) == 1
    assert "Schema 'missing.json' not found" in errors[0]
