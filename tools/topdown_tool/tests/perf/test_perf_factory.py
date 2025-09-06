# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import sys
import os
from types import SimpleNamespace
from topdown_tool.perf.perf_factory import PerfFactory


class DummyEvent:
    name = "dummy_event"

    def perf_name(self):
        return "dummy_event"

    def __lt__(self, other):
        return False


def test_perf_factory_create_instance():
    factory = PerfFactory()
    factory._perf_path = "/bin/true"
    factory._perf_args = "--dry-run"
    factory._interval = 100

    event_groups = [(DummyEvent(),)]
    perf_instance = factory.create(event_groups, "output.txt", cores=[0])

    assert perf_instance is not None
    assert hasattr(perf_instance, "start")


def test_get_pmu_and_midr_dispatch(monkeypatch):
    factory = PerfFactory()

    called_args = {}

    def fake_get_pmu_counters(core, path):
        called_args["pmu"] = (core, path)
        return 4

    def fake_get_midr_value(core, path):
        called_args["midr"] = (core, path)
        return 0xDEADBEEF

    monkeypatch.setattr(
        factory._impl_class, "get_pmu_counters", staticmethod(fake_get_pmu_counters)
    )
    monkeypatch.setattr(factory._impl_class, "get_midr_value", staticmethod(fake_get_midr_value))

    # Case 1: default perf_path
    pmu = factory.get_pmu_counters(3)
    midr = factory.get_midr_value(3)

    assert pmu == 4
    assert midr == 0xDEADBEEF
    assert called_args["pmu"][0] == 3
    assert called_args["midr"][0] == 3

    expected_tool = "perf" if sys.platform == "linux" else "wperf"
    assert called_args["pmu"][1] == expected_tool
    assert called_args["midr"][1] == expected_tool

    # Case 2: overridden perf_path
    factory._perf_path = "/bin/fakeperf"
    factory.get_pmu_counters(1)
    factory.get_midr_value(1)
    assert called_args["pmu"][1] == "/bin/fakeperf"
    assert called_args["midr"][1] == "/bin/fakeperf"


def test_perf_command_valid():
    factory = PerfFactory()

    args = SimpleNamespace()
    args.perf_path = "true"
    args.perf_args = ""
    args.interval = 0

    factory.process_cli_arguments(args)

    assert factory.is_perf_runnable()


def test_perf_command_nonexisting(tmp_path):
    factory = PerfFactory()

    command = f"{tmp_path}/testperf"

    assert not os.path.exists(command)

    args = SimpleNamespace()
    args.perf_path = command
    args.perf_args = ""
    args.interval = 0

    factory.process_cli_arguments(args)

    assert not factory.is_perf_runnable()


def test_perf_command_permissionerror(tmp_path):
    factory = PerfFactory()

    command = f"{tmp_path}/testperf"

    with open(command, "w") as f:
        f.write("test content")

    assert os.path.exists(command)
    assert not os.access(command, os.X_OK)

    args = SimpleNamespace()
    args.perf_path = command
    args.perf_args = ""
    args.interval = 0

    factory.process_cli_arguments(args)

    assert not factory.is_perf_runnable()
