# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from json import load
from pathlib import Path

import pytest

from topdown_tool.cmn_probe.cmn_database import CmnDatabase
from topdown_tool.cmn_probe.cmn_probe import CmnProbe
from topdown_tool.cmn_probe.common import CmnProbeFactoryConfig
from topdown_tool.perf.windows_perf import WindowsPerf


class _WindowsPerfFactory:
    @staticmethod
    def get_platform_class():
        return WindowsPerf


class _OtherPerfFactory:
    @staticmethod
    def get_platform_class():
        return object()


@pytest.fixture
def cmn_db() -> CmnDatabase:
    base_dir = Path(__file__).resolve().parent
    fixtures_dir = base_dir / "fixtures"
    with (fixtures_dir / "topology.json").open(encoding="utf-8") as f:
        topology_json = load(f)
    with (fixtures_dir / "cmn-700.json").open(encoding="utf-8") as f:
        specification_json = load(f)
    return CmnDatabase("700", (0,), topology_json, specification_json)


def test_uses_windows_perf(cmn_db: CmnDatabase) -> None:
    conf = CmnProbeFactoryConfig()
    probe = CmnProbe(conf, cmn_db, capture_data=False, base_csv_dir=None, perf_factory_instance=_WindowsPerfFactory())

    assert probe.uses_windows_perf() is True


def test_uses_windows_perf_false(cmn_db: CmnDatabase) -> None:
    conf = CmnProbeFactoryConfig()
    probe = CmnProbe(conf, cmn_db, capture_data=False, base_csv_dir=None, perf_factory_instance=_OtherPerfFactory())

    assert probe.uses_windows_perf() is False


def test_need_capture_transitions(cmn_db: CmnDatabase) -> None:
    conf = CmnProbeFactoryConfig()
    probe = CmnProbe(conf, cmn_db, capture_data=False, base_csv_dir=None, perf_factory_instance=_OtherPerfFactory())

    assert probe.need_capture() is False

    probe.capture_data = True
    probe.captured = False

    assert probe.need_capture() is True
    assert probe.need_capture() is False
