# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

import argparse

import pytest

from topdown_tool.cmn_probe.cmn_factory import (
    CmnProbeFactory,
    CmnProbeFactoryConfigBuilder,
)
from topdown_tool.cmn_probe.common import CmnProbeFactoryConfig
from topdown_tool.common import ArgsError
from topdown_tool.perf.event_scheduler import CollectBy
import topdown_tool.cmn_probe.cmn_factory as cmn_factory_module


def _base_args() -> argparse.Namespace:
    return argparse.Namespace(
        cmn_generate_csv=None,
        csv_output_path=None,
        cmn_list=False,
        cmn_list_devices=False,
        cmn_list_groups=None,
        cmn_list_metrics=None,
        cmn_list_events=None,
        cmn_collect_by=CollectBy.METRIC,
        cmn_metrics=None,
        cmn_metric_groups=None,
        cmn_capture_per_device_id=False,
        cmn_print_descriptions=False,
        cmn_show_sample_events=False,
        cmn_debug_path=None,
        cmn_indices=None,
        cmn_mesh_layout_input=None,
        cmn_mesh_layout_output=None,
        cmn_specification=None,
    )


@pytest.mark.parametrize(
    "version, expected",
    [
        ("CMN-700", "700"),
        ("CMN_650", "650"),
        ("CMN700", "700"),
        ("CMN--X1", "X1"),
    ],
)
def test_parse_cmn_version(version: str, expected: str) -> None:
    assert CmnProbeFactory.parse_cmn_version(version) == expected


@pytest.mark.parametrize(
    "config, expected",
    [
        (CmnProbeFactoryConfig(), True),
        (CmnProbeFactoryConfig(cmn_list=True), False),
        (CmnProbeFactoryConfig(cmn_list_devices=True), False),
        (CmnProbeFactoryConfig(cmn_list_groups=[]), False),
        (CmnProbeFactoryConfig(cmn_list_metrics=[]), False),
        (CmnProbeFactoryConfig(cmn_list_events=[]), False),
    ],
)
def test_configure_returns_capture_flag(config: CmnProbeFactoryConfig, expected: bool) -> None:
    factory = CmnProbeFactory()
    assert factory.configure(config) is expected


def test_process_cli_arguments_requires_csv_output_path() -> None:
    factory = CmnProbeFactory()
    builder = CmnProbeFactoryConfigBuilder(factory)
    args = _base_args()
    args.cmn_generate_csv = ["metrics"]

    with pytest.raises(ArgsError):
        builder.process_cli_arguments(args)


def test_process_cli_arguments_populates_config() -> None:
    factory = CmnProbeFactory()
    builder = CmnProbeFactoryConfigBuilder(factory)
    args = _base_args()
    args.cmn_generate_csv = ["metrics", "events"]
    args.csv_output_path = "/tmp"
    args.cmn_list = True
    args.cmn_metrics = ["m1", "m2"]
    args.cmn_metric_groups = ["g1"]
    args.cmn_collect_by = CollectBy.NONE

    config = builder.process_cli_arguments(args)

    assert config.cmn_generate_metrics_csv is True
    assert config.cmn_generate_events_csv is True
    assert config.cmn_list is True
    assert config.metrics == ["m1", "m2"]
    assert config.groups == ["g1"]
    assert config.collect_by == CollectBy.NONE


def test_is_available_uses_perf_factory(monkeypatch) -> None:
    factory = CmnProbeFactory()

    monkeypatch.setattr(cmn_factory_module.perf_factory, "get_cmn_version", lambda: {})
    assert factory.is_available() is False

    monkeypatch.setattr(
        cmn_factory_module.perf_factory, "get_cmn_version", lambda: {0: "700"}
    )
    assert factory.is_available() is True


def test_create_rejects_unknown_indices() -> None:
    factory = CmnProbeFactory()
    factory.conf = CmnProbeFactoryConfig(cmn_index=[1])
    factory.cmns = {0: "700"}

    with pytest.raises(ArgsError):
        factory.create(capture_data=False)


def test_discover_cmn_unsupported_platform(monkeypatch) -> None:
    factory = CmnProbeFactory()
    monkeypatch.setattr(cmn_factory_module.sys, "platform", "darwin")

    with pytest.raises(RuntimeError):
        factory.discover_cmn()


def test_discover_cmn_json_windows_sets_versions(monkeypatch) -> None:
    factory = CmnProbeFactory()
    factory.cmns = {0: "700", 1: "650"}

    class _Result:
        stdout = '{"elements": [{}, {}]}'

    monkeypatch.setattr(cmn_factory_module, "run", lambda *args, **kwargs: _Result())

    topology_json = factory.discover_cmn_json_windows()

    assert topology_json["elements"][0]["version"] == "700"
    assert topology_json["elements"][1]["version"] == "650"


def test_discover_cmn_json_linux_missing_submodule(monkeypatch) -> None:
    monkeypatch.setattr(cmn_factory_module.os.path, "isfile", lambda _path: False)

    with pytest.raises(Exception, match="Uninitialized git submodules"):
        CmnProbeFactory.discover_cmn_json_linux()
