# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import pytest
from tests.cpu_probe.helpers import get_fixture_path, get_fixture_dir
from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification
from topdown_tool.cpu_probe.cpu_telemetry_database import TelemetryDatabase


@pytest.fixture
def test_telemetry_spec():
    """
    Reusable fixture to load the test TelemetryDatabase from the main CLI test specification.
    Fixture file location follows the canonical fixtures/ structure.
    """
    json_path = get_fixture_path("telemetry_cli_test.json")
    schemas_dir = get_fixture_dir()
    return TelemetrySpecification.load_from_json_file(json_path, schemas_dir)


@pytest.fixture
def test_telemetry_db(test_telemetry_spec):
    """
    Reusable fixture to load the test TelemetryDatabase from the main CLI test specification.
    Fixture file location follows the canonical fixtures/ structure.
    """
    return TelemetryDatabase(test_telemetry_spec)
