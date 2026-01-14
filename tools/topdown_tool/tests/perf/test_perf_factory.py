# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

import logging
import pytest
import os
import shutil
from signal import SIGINT
from types import SimpleNamespace


from topdown_tool.perf.perf_factory import PerfFactory, PerfFactoryConfig
from topdown_tool.perf.perf import Uncore
from topdown_tool.perf.remote_linux_perf import RemoteLinuxPerf
from topdown_tool.common import remote_target_manager
from tests.perf.conftest import FakeProcess, RecordingTarget


class DummyEvent:
    name = "dummy_event"

    def perf_name(self):
        return "dummy_event"

    def __lt__(self, other):
        return False


@pytest.fixture
def remote_linux_target(monkeypatch):
    """Provide a fake remote Linux target and configure the manager accordingly."""

    target = object()
    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: True)
    return target


def test_perf_factory_create_instance():
    factory = PerfFactory()
    factory.configure(PerfFactoryConfig(perf_path="/bin/true", perf_args="--dry-run", interval=100))

    perf_instance = factory.create()

    assert perf_instance is not None
    assert hasattr(perf_instance, "start")


def test_process_cli_arguments_returns_config() -> None:
    factory = PerfFactory()
    args = SimpleNamespace(perf_path="/usr/bin/perf", perf_args="--foo", interval=250)

    config = factory.process_cli_arguments(args)

    assert isinstance(config, PerfFactoryConfig)
    assert config.perf_path == "/usr/bin/perf"
    assert config.perf_args == "--foo"
    assert config.interval == 250
    # Ensure calling the helper didn't mutate factory state.
    assert factory._perf_path is None


def test_perf_command_valid():
    factory = PerfFactory()

    args = SimpleNamespace()
    args.perf_path = "true"
    args.perf_args = ""
    args.interval = 0

    factory.configure_from_cli_arguments(args)

    assert factory.is_perf_runnable()


def test_perf_command_nonexisting(tmp_path):
    factory = PerfFactory()

    command = f"{tmp_path}/testperf"

    assert not os.path.exists(command)

    args = SimpleNamespace()
    args.perf_path = command
    args.perf_args = ""
    args.interval = 0

    factory.configure_from_cli_arguments(args)

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

    factory.configure_from_cli_arguments(args)

    assert not factory.is_perf_runnable()


def test_perf_factory_create_remote_linux(monkeypatch, remote_linux_target):
    factory = PerfFactory()

    captured = {}
    original_init = RemoteLinuxPerf.__init__

    def patched_init(self, *, perf_args=None, interval=None, target=None):
        captured["target"] = target
        original_init(self, perf_args=perf_args, interval=interval, target=target)

    monkeypatch.setattr(RemoteLinuxPerf, "__init__", patched_init)

    perf_instance = factory.create()

    assert isinstance(perf_instance, RemoteLinuxPerf)
    assert captured["target"] is remote_linux_target


def test_perf_factory_is_runnable_remote(monkeypatch, remote_linux_target):
    factory = PerfFactory()

    def _fail_which(_path: str) -> str:
        raise AssertionError("should not call shutil.which")

    monkeypatch.setattr(shutil, "which", _fail_which)

    captured = {}

    def _remote_check(cls, target, perf_path):  # pylint: disable=unused-argument
        captured["args"] = (target, perf_path)
        return True

    monkeypatch.setattr(
        RemoteLinuxPerf,
        "is_remote_runnable",
        classmethod(_remote_check),
    )

    assert factory.is_perf_runnable()
    assert captured["args"][0] is remote_linux_target
    assert captured["args"][1] is None


def test_perf_factory_is_runnable_remote_failure(monkeypatch, remote_linux_target):
    factory = PerfFactory()

    monkeypatch.setattr(
        RemoteLinuxPerf,
        "is_remote_runnable",
        classmethod(lambda cls, target, perf_path: False),
    )

    assert not factory.is_perf_runnable()


def test_perf_factory_have_privilege_remote(monkeypatch, caplog, remote_linux_target):
    factory = PerfFactory()
    called = {"invoked": False}

    def _fake_have_privilege() -> bool:
        called["invoked"] = True
        return False

    monkeypatch.setattr(
        RemoteLinuxPerf,
        "have_perf_privilege",
        staticmethod(_fake_have_privilege),
    )

    caplog.set_level(logging.WARNING)
    assert not factory.have_perf_privilege()
    assert called["invoked"]
    assert any(
        "limited perf privileges. Perf collection requires elevated access; aborting" in message
        for message in caplog.messages
    )


def test_perf_factory_have_privilege_remote_failure(monkeypatch, caplog, remote_linux_target):
    factory = PerfFactory()

    def _raise_privilege_error() -> bool:
        raise RuntimeError("simulated privilege check failure")

    monkeypatch.setattr(
        RemoteLinuxPerf,
        "have_perf_privilege",
        staticmethod(_raise_privilege_error),
    )

    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError) as excinfo:
        factory.have_perf_privilege()

    assert "simulated privilege check failure" in str(excinfo.value)
    assert any("Could not verify perf privileges" in message for message in caplog.messages)


def test_remote_linux_perf_end_to_end_flow(monkeypatch, tmp_path):
    remote_target = RecordingTarget()
    remote_target.remote_files = {}

    active_process = {}

    def background_impl(_cmd: str) -> FakeProcess:
        process = FakeProcess()
        active_process["proc"] = process
        return process

    def pull_impl(remote_path: str, local_path: str) -> None:
        content = remote_target.remote_files.get(remote_path, "")
        with open(local_path, "w", encoding="utf-8") as handle:
            handle.write(content)

    remote_target.background_impl = background_impl
    remote_target.pull_impl = pull_impl

    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: remote_target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: True)
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )

    factory = PerfFactory()
    perf_instance = factory.create()

    event = DummyEvent()
    output_path = tmp_path / "perf.stat"

    perf_instance.start(((event,),), str(output_path))

    # Perf pre-creates its output via a remote shell; parse that command to recover the path.
    touch_calls = [cmd for cmd in remote_target.exec_calls if cmd.startswith("sh -lc '>")]
    assert touch_calls, "expected remote output pre-touch command"
    remote_output = touch_calls[0].split(">", 1)[1].strip().strip("'")
    remote_target.remote_files[remote_output] = f"123;;{event.perf_name()}\n"

    perf_instance.stop()
    assert active_process["proc"].signals == [SIGINT]

    records = perf_instance.get_perf_result()

    assert remote_target.background_calls, "Expected remote background execution"
    assert (event,) in next(iter(records[Uncore()].values()))
    assert records[Uncore()][None][(event,)] == (123.0,)


def test_perf_factory_remote_cli_override(monkeypatch, tmp_path):
    factory = PerfFactory()
    args = SimpleNamespace(perf_path="/custom/perf", perf_args="--foo", interval=99)
    factory.configure_from_cli_arguments(args)
    remote_target = RecordingTarget()
    remote_target.pull_impl = lambda *_args, **_kwargs: None

    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: remote_target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: True)

    captured = {}

    def _resolve(cls, target):  # pylint: disable=unused-argument
        captured["perf_path"] = cls._perf_path
        return "/resolved/perf"

    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(_resolve),
    )

    perf_instance = factory.create()

    event = DummyEvent()
    output_path = tmp_path / "perf.stat"

    perf_instance.start(((event,),), str(output_path))

    assert captured["perf_path"] == "/custom/perf"
    assert any("/resolved/perf" in call for call in remote_target.background_calls)

    perf_instance.stop()
