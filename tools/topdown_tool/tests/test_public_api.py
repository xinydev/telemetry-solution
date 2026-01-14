# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Unit tests covering the public re-exports under ``topdown_tool``."""

import argparse
import importlib.metadata
from typing import Optional, Set, Tuple

import pytest
import topdown_tool
from topdown_tool import (
    Probe,
    ProbeFactory,
    ProbeFactoryCliConfigBuilder,
    Target,
    get_remote_target,
    get_target_os,
    get_target_type,
    has_remote_target,
    is_target_linuxlike,
    load_probe_factories,
    set_remote_target,
)
from topdown_tool.common import remote_target_manager
from topdown_tool.common.devlib_types import Target as UnderlyingTarget
from topdown_tool.probe.probe import Probe as ProbeDefinition
from topdown_tool.probe.probe import ProbeFactory as ProbeFactoryDefinition
from topdown_tool.probe.probe import (
    load_probe_factories as load_probe_factories_definition,
)
from topdown_tool.probe.probe import (
    ProbeFactoryCliConfigBuilder as ProbeFactoryCliConfigBuilderDefinition,
)


class _DummyProbe(Probe):
    def __init__(self, capture_enabled: bool = True) -> None:
        self.started: bool = False
        self.stopped: bool = False
        self.output_called: bool = False
        self.capture_enabled = capture_enabled

    def start_capture(self, run: int = 1, pids: Optional[Set[int]] = None) -> None:
        self.started = True

    def stop_capture(
        self,
        run: int = 1,
        pid: Optional[int] = None,
        interrupted: bool = False,
    ) -> None:
        self.stopped = True

    def need_capture(self) -> bool:
        return not self.started

    def output(self) -> None:
        self.output_called = True


class _DummyConfig:
    pass


class _DummyBuilder(ProbeFactoryCliConfigBuilder[_DummyConfig]):
    def __init__(self, factory: "_DummyFactory") -> None:
        self._factory = factory
        self.add_called_with: Optional[argparse.ArgumentParser] = None
        self.process_called_with: Optional[argparse.Namespace] = None

    def add_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        self.add_called_with = parser

    def process_cli_arguments(self, args: argparse.Namespace) -> _DummyConfig:
        self.process_called_with = args
        return _DummyConfig()


class _DummyFactory(ProbeFactory[_DummyConfig]):
    def __init__(self) -> None:
        self.configured_with: Optional[_DummyConfig] = None
        self.capture_flag: bool = True
        self.last_capture_flag: Optional[bool] = None
        self.builder_instance: Optional[_DummyBuilder] = None
        super().__init__()

    def name(self) -> str:
        return "DUMMY"

    def is_available(self) -> bool:
        return True

    def _get_config_builder(self) -> ProbeFactoryCliConfigBuilder[_DummyConfig]:
        self.builder_instance = _DummyBuilder(self)
        return self.builder_instance

    def configure(self, config: _DummyConfig, **kwargs: object) -> bool:
        self.configured_with = config
        return self.capture_flag

    def create(self, capture_data: bool, base_csv_dir: Optional[str]) -> Tuple[Probe, ...]:
        self.last_capture_flag = capture_data
        probe = _DummyProbe(capture_enabled=capture_data)
        return (probe,)

    def get_description(self) -> str:
        return "Dummy factory"


def test_reexports_match_internal_definitions() -> None:
    assert Probe is ProbeDefinition
    assert ProbeFactory is ProbeFactoryDefinition
    assert ProbeFactoryCliConfigBuilder is ProbeFactoryCliConfigBuilderDefinition
    assert Target is UnderlyingTarget
    assert load_probe_factories is load_probe_factories_definition
    assert get_remote_target is remote_target_manager.get_remote_target
    assert set_remote_target is remote_target_manager.set_remote_target
    assert has_remote_target is remote_target_manager.has_remote_target
    assert get_target_type is remote_target_manager.get_target_type
    assert get_target_os is remote_target_manager.get_target_os
    assert is_target_linuxlike is remote_target_manager.is_target_linuxlike


def test_probe_and_factory_methods_used_directly() -> None:
    factory = _DummyFactory()
    config = _DummyConfig()

    capture_flag = factory.configure(config)
    assert capture_flag is True
    assert factory.configured_with is config

    probes = factory.create(capture_data=capture_flag, base_csv_dir=None)
    assert len(probes) == 1
    probe = probes[0]
    assert probe.capture_enabled is True
    assert probe.need_capture() is True

    probe.start_capture()
    assert probe.started is True
    assert probe.need_capture() is False

    for each_probe in probes:
        each_probe.stop_capture()
    assert probe.stopped is True

    probe.output()
    assert probe.output_called is True


def test_configure_respects_factory_flag() -> None:
    factory = _DummyFactory()
    factory.capture_flag = False
    capture_flag = factory.configure(_DummyConfig())
    assert capture_flag is False


def test_create_propagates_capture_flag() -> None:
    factory = _DummyFactory()
    _assert_capture(factory, False)
    _assert_capture(factory, True)


def test_add_cli_arguments_delegates_to_builder() -> None:
    factory = _DummyFactory()
    parser = argparse.ArgumentParser()
    factory.add_cli_arguments(parser)
    assert factory.builder_instance is not None
    assert factory.builder_instance.add_called_with is parser


def test_configure_from_cli_arguments_uses_builder() -> None:
    factory = _DummyFactory()
    args = argparse.Namespace()
    capture_flag = factory.configure_from_cli_arguments(args)
    assert capture_flag is True
    assert factory.configured_with is not None
    assert factory.builder_instance is not None
    assert factory.builder_instance.process_called_with is args


def _assert_capture(factory: _DummyFactory, capture: bool) -> None:
    probes = factory.create(capture_data=capture, base_csv_dir=None)
    assert len(probes) == 1
    probe = probes[0]
    assert isinstance(probe, _DummyProbe)
    assert factory.last_capture_flag is capture
    assert probe.capture_enabled is capture


def test_module_exports_are_consistent() -> None:
    expected_exports = set(topdown_tool.PROBE_PUBLIC_EXPORTS) | {
        "Target",
        "get_remote_target",
        "set_remote_target",
        "has_remote_target",
        "get_target_type",
        "get_target_os",
        "is_target_linuxlike",
    }
    assert set(topdown_tool.__all__) == expected_exports
    for name in topdown_tool.__all__:
        assert hasattr(topdown_tool, name), f"Missing attribute for export {name}"


def test_probe_public_exports_subset_of_module_all() -> None:
    for name in topdown_tool.PROBE_PUBLIC_EXPORTS:
        assert name in topdown_tool.__all__


def test_load_probe_factories_uses_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    class _EntryPoint:
        def __init__(self, obj, name: str) -> None:
            self._obj = obj
            self.name = name

        def load(self):
            return self._obj

    class _EntryPoints:
        def __init__(self, entries) -> None:
            self._entries = entries

        def select(self, *, group: str):
            assert group == "topdown_tool.probe_factories"
            return self._entries

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda: _EntryPoints(
            (
                _EntryPoint(_DummyFactory, "dummy"),
                _EntryPoint(str, "not-a-factory"),
            )
        ),
    )

    factories = load_probe_factories()
    assert len(factories) == 1
    assert isinstance(factories[0], _DummyFactory)


def test_remote_target_helpers_round_trip() -> None:
    manager = remote_target_manager.REMOTE_TARGET_MANAGER
    previous_target = manager.get_target()
    previous_type = manager.get_target_type()
    previous_os = manager.get_target_os()
    manager._clear_target()  # type: ignore[attr-defined]

    sentinel = object()
    try:
        set_remote_target(sentinel, target_type="ssh", target_os="Linux")
        assert has_remote_target() is True
        assert get_remote_target() is sentinel
        assert get_target_type() == "ssh"
        assert get_target_os() == "linux"
        assert is_target_linuxlike() is True
    finally:
        manager._clear_target()  # type: ignore[attr-defined]
        if previous_target is not None:
            manager.set_target(previous_target, previous_type, previous_os)
