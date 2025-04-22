# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited


from pytest import Parser, FixtureRequest
import pytest


def pytest_addoption(parser: Parser) -> None:
    parser.addoption(
        "--regen-reference",
        action="store",
        default="off",
        choices=["off", "write", "dryrun"],
        help="How to handle reference output: 'off' (default): just compare, 'write': overwrite reference files, 'dryrun': show diffs for changes.",
    )


@pytest.fixture
def regen_reference_mode(request: FixtureRequest) -> str:
    """Returns the selected mode: 'off', 'write', or 'dryrun'."""
    return request.config.getoption("--regen-reference")
