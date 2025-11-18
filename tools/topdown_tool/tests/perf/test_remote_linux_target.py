# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import sys
from types import ModuleType

import pytest

from topdown_tool.common import remote_target_manager
from topdown_tool.perf.remote_linux_perf import RemoteLinuxPerf
from tests.perf.conftest import FakeProcess, RecordingTarget


class _DummyEvent:
    def __init__(self, name: str = "dummy") -> None:
        self.name = name

    def perf_name(self) -> str:
        return self.name

    def __lt__(self, other):  # pragma: no cover - deterministic ordering not needed
        return False


@pytest.fixture
def fake_target(monkeypatch) -> RecordingTarget:
    target = RecordingTarget()

    def execute_with_failure(cmd: str) -> str:
        if "sh -lc '>" in cmd:
            raise RuntimeError("pre-touch failed")
        return ""

    target.execute_impl = execute_with_failure
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: target)
    return target


def test_remote_linux_perf_start_stop_wait_handles_failures(monkeypatch, tmp_path, fake_target):
    # Force the workdir probe to raise so the recorder falls back to /data/local/tmp.
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "remote_workdir",
        staticmethod(lambda _target: (_ for _ in ()).throw(RuntimeError("failed"))),
    )
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )

    perf = RemoteLinuxPerf(perf_args="--dummy 1", interval=250, target=fake_target)

    event = _DummyEvent("instructions")
    output_path = tmp_path / "perf.stat"

    # Include full CLI knobs (cores, PID, interval) to ensure they land in the command string.
    perf.start(((event,),), str(output_path), pid=4242, cores=[0, 1])

    touch_calls = [cmd for cmd in fake_target.exec_calls if cmd.startswith("sh -lc '>")]
    assert touch_calls, "expected a remote touch command"
    remote_output = touch_calls[0].split(">", 1)[1].strip().strip("'")
    assert remote_output == "/data/local/tmp/perf.stat-0"
    assert any("--per-core" in call for call in fake_target.background_calls)
    assert any("-p 4242" in call for call in fake_target.background_calls)
    assert any("-I 250" in call for call in fake_target.background_calls)
    assert any("--control" in call for call in fake_target.background_calls)
    assert any("printf enable" in cmd for cmd in fake_target.exec_calls)
    assert any("dd if=" in cmd for cmd in fake_target.exec_calls)

    # Stop should tolerate failures when signalling the remote process.
    fake_target.process.send_signal = lambda sig: (_ for _ in ()).throw(RuntimeError(sig))
    perf.stop()

    perf.get_perf_result()

    # Waiting should trigger a sync and attempt pulling the remote output even after errors.
    assert any("sync" in cmd for cmd in fake_target.exec_calls)
    assert fake_target.pull_calls


def test_remote_linux_perf_start_background_failure(monkeypatch, tmp_path, fake_target):
    monkeypatch.setattr(RemoteLinuxPerf, "remote_workdir", staticmethod(lambda _target: "/r"))
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )

    def _background_failure(_cmd: str) -> FakeProcess:
        raise RuntimeError("background failed")

    fake_target.background_impl = _background_failure

    perf = RemoteLinuxPerf(target=fake_target)
    output_path = tmp_path / "perf.stat"
    # Ensure start() handles background execution failures gracefully.
    perf.start(((_DummyEvent(),),), str(output_path))
    perf.stop()


def test_remote_linux_perf_control_pipe_failure(monkeypatch, tmp_path, fake_target):
    monkeypatch.setattr(RemoteLinuxPerf, "remote_workdir", staticmethod(lambda _target: "/r"))
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )

    def _execute(cmd: str) -> str:
        if "mkfifo" in cmd:
            raise RuntimeError("mkfifo failed")
        return ""

    fake_target.exec_calls.clear()
    fake_target.background_calls.clear()
    fake_target.execute_impl = _execute

    perf = RemoteLinuxPerf(target=fake_target)
    output_path = tmp_path / "perf.stat"
    perf.start(((_DummyEvent(),),), str(output_path))

    assert not any("--control" in call for call in fake_target.background_calls)
    assert not any("printf enable" in cmd for cmd in fake_target.exec_calls)
    assert not any("dd if=" in cmd for cmd in fake_target.exec_calls)
    perf.stop()


def test_remote_linux_perf_have_privilege_no_target(monkeypatch):
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: None)
    assert RemoteLinuxPerf.have_perf_privilege() is True


def test_remote_linux_perf_has_remote_privileges(monkeypatch, fake_target):
    responses = {
        "cat /proc/sys/kernel/perf_event_paranoid": "-1\n",
        "cat /proc/self/status": "CapEff:\t0000000000200000",
    }

    def _execute(cmd: str) -> str:
        return responses[cmd]

    fake_target.execute_impl = _execute

    assert RemoteLinuxPerf.have_perf_privilege() is True

    # On failure the helper should fall back to False without raising.
    fake_target.execute_impl = lambda *_args: (_ for _ in ()).throw(RuntimeError("fail"))
    assert RemoteLinuxPerf.have_perf_privilege() is False


def test_remote_linux_perf_get_pmu_counters(monkeypatch, fake_target):
    checks = {}

    def _execute(cmd: str):
        event_count = cmd.count("instructions:u")
        checks.setdefault(event_count, 0)
        checks[event_count] += 1
        if event_count <= 3:
            return "0\t0\t0\t0\t100.0\n"
        if event_count == 4:
            return "0\t0\t0\t0\t50.0\n"
        raise RuntimeError("boom")

    fake_target.execute_impl = _execute
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )

    pmu = RemoteLinuxPerf.get_pmu_counters(0)
    assert pmu == 3
    assert any(count > 4 for count in checks)


def test_remote_linux_perf_remote_workdir_fallback(fake_target):
    fake_target.working_directory = "/alt/workdir"
    fake_target.workpath_impl = lambda _name: "/remote/workdir"
    assert RemoteLinuxPerf.remote_workdir(fake_target) == "/remote/workdir"

    fake_target.workpath_impl = lambda _name: (_ for _ in ()).throw(RuntimeError("no workpath"))
    assert RemoteLinuxPerf.remote_workdir(fake_target) == "/alt/workdir"


def test_remote_linux_perf_start_provisions_helpers(monkeypatch, tmp_path):
    helper = tmp_path / "devlib-signal-target"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    perf_binary = tmp_path / "perf"
    perf_binary.write_text("perf", encoding="utf-8")

    resources = {
        "scripts/devlib-signal-target": helper,
        "fakeabi/perf": perf_binary,
    }

    class _FakeResources:
        def __init__(self, mapping):
            self._mapping = mapping

        def joinpath(self, name):
            return self._mapping[name]

    def _fake_files(_module):
        return _FakeResources(resources)

    def _fake_as_file(resource):
        from contextlib import contextmanager

        @contextmanager
        def _manager():
            yield resource

        return _manager()

    module = ModuleType("devlib")
    bin_module = ModuleType("devlib.bin")
    import importlib.machinery

    module.__spec__ = importlib.machinery.ModuleSpec("devlib", loader=None, is_package=True)
    bin_module.__spec__ = importlib.machinery.ModuleSpec("devlib.bin", loader=None, is_package=True)
    module.bin = bin_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "devlib", module)
    monkeypatch.setitem(sys.modules, "devlib.bin", bin_module)

    import topdown_tool.perf.remote_linux_perf as remote_module

    # Point resource lookups at our temp files so provisioning can run without real devlib data.
    monkeypatch.setattr(remote_module, "files", _fake_files)
    monkeypatch.setattr(remote_module, "as_file", _fake_as_file)
    monkeypatch.setattr(remote_module, "remote_path_exists", lambda *_args, **_kwargs: False)

    target = RecordingTarget()

    def permissive_execute(_cmd: str) -> str:
        return ""

    target.execute_impl = permissive_execute

    perf = RemoteLinuxPerf(target=target)
    output_path = tmp_path / "perf.stat"

    perf.start(((_DummyEvent(),),), str(output_path))

    assert any(cmd.startswith("mkdir -p") for cmd in target.exec_calls)
    assert any(dst.endswith("devlib-signal-target") for _, dst in target.push_calls)

    perf.stop()


def test_remote_linux_perf_start_installs_binary_despite_mkdir_failure(monkeypatch, tmp_path):
    helper = tmp_path / "devlib-signal-target"
    helper.write_text("#!/bin/sh\n", encoding="utf-8")
    perf_binary = tmp_path / "perf"
    perf_binary.write_text("perf", encoding="utf-8")

    resources = {
        "scripts/devlib-signal-target": helper,
        "fakeabi/perf": perf_binary,
    }

    class _FakeResources:
        def __init__(self, mapping):
            self._mapping = mapping

        def joinpath(self, name):
            return self._mapping[name]

    def _fake_files(_module):
        return _FakeResources(resources)

    def _fake_as_file(resource):
        from contextlib import contextmanager

        @contextmanager
        def _manager():
            yield resource

        return _manager()

    module = ModuleType("devlib")
    bin_module = ModuleType("devlib.bin")
    import importlib.machinery

    module.__spec__ = importlib.machinery.ModuleSpec("devlib", loader=None, is_package=True)
    bin_module.__spec__ = importlib.machinery.ModuleSpec("devlib.bin", loader=None, is_package=True)
    module.bin = bin_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "devlib", module)
    monkeypatch.setitem(sys.modules, "devlib.bin", bin_module)

    import topdown_tool.perf.remote_linux_perf as remote_module

    monkeypatch.setattr(remote_module, "files", _fake_files)
    monkeypatch.setattr(remote_module, "as_file", _fake_as_file)
    monkeypatch.setattr(remote_module, "remote_path_exists", lambda *_args, **_kwargs: False)

    target = RecordingTarget()

    def _execute(cmd: str) -> str:
        if cmd.startswith("mkdir -p"):
            raise RuntimeError("mkdir fails")
        return ""

    target.execute_impl = _execute

    perf = RemoteLinuxPerf(target=target)
    output_path = tmp_path / "perf.stat"

    perf.start(((_DummyEvent(),),), str(output_path))

    assert target.push_calls
    assert any(cmd.startswith("chmod") for cmd in target.exec_calls)

    perf.stop()
