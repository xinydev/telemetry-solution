# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Tests for the cpu_detector module classes and helpers.
"""

import io
import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import topdown_tool.cpu_probe.cpu_detector as cpu_detector_module
from topdown_tool.cpu_probe.cpu_detector import (
    CpuDetector,
    CpuDetectorFactory,
    LocalLinuxCpuDetector,
    LocalWindowsCpuDetector,
    RemoteLinuxLikeCpuDetector,
    LINUX_MIDR_PATH,
)
from topdown_tool.common.remote_utils import remote_read_text


def _remote_detector(target) -> RemoteLinuxLikeCpuDetector:
    # The perf factory is not used directly in the helpers under test.
    return RemoteLinuxLikeCpuDetector(target=target, perf_factory_instance=SimpleNamespace())


def _linux_detector() -> LocalLinuxCpuDetector:
    return LocalLinuxCpuDetector(perf_factory_instance=SimpleNamespace())


def _windows_detector(perf_factory=None) -> LocalWindowsCpuDetector:
    perf = perf_factory or SimpleNamespace(get_midr_value=lambda core: 0xABC)
    return LocalWindowsCpuDetector(perf_factory_instance=perf)


def test_remote_read_text_returns_stripped_string():
    target = SimpleNamespace()
    target.execute = Mock(return_value=" foo \n")

    result = remote_read_text(target, "/tmp/test")

    assert result == "foo"
    target.execute.assert_called_once_with("cat /tmp/test", check_exit_code=False)


def test_remote_read_text_decodes_bytes_response():
    target = SimpleNamespace()
    target.execute = Mock(return_value=b"bar\n")

    assert remote_read_text(target, "/tmp/test") == "bar"


def test_remote_read_text_returns_none_when_output_empty():
    target = SimpleNamespace()
    target.execute = Mock(return_value="   \n")

    assert remote_read_text(target, "/tmp/empty") is None


def test_remote_read_text_returns_none_on_exception(caplog):
    target = SimpleNamespace()
    target.execute = Mock(side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.DEBUG):
        assert remote_read_text(target, "/tmp/missing") is None

    assert "Failed to read /tmp/missing" in caplog.text


def test_cpu_detector_static_helpers():
    midr = CpuDetector.compose_midr(
        implementer=0x41, variant=0x1, architecture=0xF, part_num=0xD0C, revision=0x2
    )
    assert CpuDetector.cpu_id(midr) == ((0x41 << 12) | 0xD0C)


def test_local_linux_cpu_midr_uses_cpuinfo_block(monkeypatch):
    path = LINUX_MIDR_PATH.format(1)

    def fake_open(requested_path, *args, **kwargs):
        if requested_path == path:
            # Simulate missing sysfs MIDR entry so detector falls back to /proc/cpuinfo.
            raise FileNotFoundError
        if requested_path == "/proc/cpuinfo":
            return io.StringIO(
                "processor   : 0\n"
                "CPU implementer : 0x41\n"
                "CPU variant : 0x1\n"
                "CPU part : 0xd0c\n"
                "CPU revision : 0x2\n\n"
                "processor   : 1\n"
                "CPU implementer : 0x42\n"
                "CPU variant : 0x0\n"
                "CPU part : 0xd13\n"
                "CPU revision : 0x1\n"
            )
        raise FileNotFoundError

    monkeypatch.setattr(cpu_detector_module, "open", fake_open, raising=False)
    detector = _linux_detector()
    expected = CpuDetector.compose_midr(0x42, 0x0, 0xF, 0xD13, 0x1)
    assert detector.cpu_midr(1) == expected


def test_local_linux_cpu_count(monkeypatch):
    monkeypatch.setattr(cpu_detector_module.os, "cpu_count", lambda: 8)
    detector = _linux_detector()
    assert detector.cpu_count() == 8


def test_local_linux_cpu_midr_from_sysfs(monkeypatch):
    path = LINUX_MIDR_PATH.format(2)

    def fake_open(requested_path, *args, **kwargs):
        if requested_path == path:
            return io.StringIO("0x1234\n")
        raise FileNotFoundError

    monkeypatch.setattr(cpu_detector_module, "open", fake_open, raising=False)
    detector = _linux_detector()
    assert detector.cpu_midr(2) == int("0x1234", 16)


def test_local_linux_cpu_midr_from_cpuinfo(monkeypatch):
    path = LINUX_MIDR_PATH.format(0)

    def fake_open(requested_path, *args, **kwargs):
        if requested_path == path:
            raise FileNotFoundError
        if requested_path == "/proc/cpuinfo":
            return io.StringIO(
                "processor : 0\nCPU implementer : 0x41\nCPU variant : 0x0\nCPU part : 0xd0c\nCPU revision : 0x1\n"
            )
        raise FileNotFoundError

    monkeypatch.setattr(cpu_detector_module, "open", fake_open, raising=False)
    detector = _linux_detector()
    midr = detector.cpu_midr(0)
    expected = CpuDetector.compose_midr(0x41, 0x0, 0xF, 0xD0C, 0x1)
    assert midr == expected


def test_local_linux_cpu_midr_raises_when_unavailable(monkeypatch):
    def fake_open(path, *args, **_kwargs):
        if path == "/proc/cpuinfo":
            return io.StringIO("processor : 0\nCPU implementer : 0x41\n")
        raise FileNotFoundError

    monkeypatch.setattr(cpu_detector_module, "open", fake_open, raising=False)
    detector = _linux_detector()
    with pytest.raises(RuntimeError):
        detector.cpu_midr(0)


def test_local_windows_cpu_detector(monkeypatch):
    perf = SimpleNamespace(get_midr_value=lambda idx: idx + 100)
    detector = _windows_detector(perf)
    monkeypatch.setattr(cpu_detector_module.os, "cpu_count", lambda: 4)
    assert detector.cpu_count() == 4
    assert detector.cpu_midr(2) == 102


def test_remote_linux_cpu_count_from_sysfs(monkeypatch):
    target = SimpleNamespace(execute=Mock())
    monkeypatch.setattr(
        cpu_detector_module,
        "remote_read_text",
        Mock(side_effect=lambda _target, path: "0-3" if "present" in path else None),
    )
    detector = _remote_detector(target)
    assert detector.cpu_count() == 4
    target.execute.assert_not_called()


def test_remote_linux_cpu_count_from_nproc(monkeypatch):
    target = SimpleNamespace(execute=Mock(return_value=b"6\n"))
    monkeypatch.setattr(cpu_detector_module, "remote_read_text", Mock(return_value=None))
    detector = _remote_detector(target)
    assert detector.cpu_count() == 6
    target.execute.assert_called_once_with("nproc", check_exit_code=False)


def test_remote_linux_cpu_count_raises_on_failure(monkeypatch, caplog):
    target = SimpleNamespace(execute=Mock(side_effect=RuntimeError("boom")))
    monkeypatch.setattr(cpu_detector_module, "remote_read_text", Mock(return_value=None))
    detector = _remote_detector(target)
    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError):
        detector.cpu_count()
    assert "Unable to determine CPU count from remote target" in caplog.text


def test_remote_linux_cpu_midr_from_sysfs(monkeypatch):
    target = SimpleNamespace(execute=Mock())
    detector = _remote_detector(target)
    monkeypatch.setattr(cpu_detector_module, "remote_path_exists", lambda _t, _p: True)
    monkeypatch.setattr(cpu_detector_module, "remote_read_text", Mock(return_value="0x1234"))
    assert detector.cpu_midr(0) == int("0x1234", 16)


def test_remote_linux_cpu_midr_from_cpuinfo(monkeypatch):
    target = SimpleNamespace(execute=Mock())
    detector = _remote_detector(target)

    def fake_read(_target, path):
        if path == "/proc/cpuinfo":
            return "processor : 0\nCPU implementer : 0x41\nCPU variant : 0x0\nCPU part : 0xd0c\nCPU revision : 0x1\n"
        return None

    monkeypatch.setattr(cpu_detector_module, "remote_path_exists", lambda _t, _p: False)
    monkeypatch.setattr(cpu_detector_module, "remote_read_text", Mock(side_effect=fake_read))
    assert detector.cpu_midr(0) == CpuDetector.compose_midr(0x41, 0x0, 0xF, 0xD0C, 0x1)


def test_remote_linux_cpu_midr_raises_when_missing(monkeypatch):
    target = SimpleNamespace(execute=Mock())
    detector = _remote_detector(target)
    monkeypatch.setattr(cpu_detector_module, "remote_path_exists", lambda _t, _p: False)
    monkeypatch.setattr(
        cpu_detector_module,
        "remote_read_text",
        Mock(return_value="processor : 0\nCPU implementer : 0x41\n"),
    )
    with pytest.raises(RuntimeError):
        detector.cpu_midr(0)


def test_cpu_detector_factory_remote_linux(monkeypatch):
    target = SimpleNamespace(execute=Mock(return_value=""))
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "get_remote_target", lambda: target
    )
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "is_target_linuxlike", lambda: True
    )
    monkeypatch.setattr(cpu_detector_module, "remote_path_exists", lambda *_args, **_kwargs: False)

    captured = {}

    def fake_remote_read(tgt, path):
        captured["target"] = tgt
        if "present" in path:
            return "0-1"
        if path == "/proc/cpuinfo":
            return (
                "processor : 0\n"
                "CPU implementer : 0x41\n"
                "CPU variant : 0x0\n"
                "CPU part : 0xd0c\n"
                "CPU revision : 0x1\n"
            )
        return None

    monkeypatch.setattr(cpu_detector_module, "remote_read_text", fake_remote_read)

    detector = CpuDetectorFactory.create(SimpleNamespace())

    assert isinstance(detector, RemoteLinuxLikeCpuDetector)
    assert detector.cpu_count() == 2
    assert captured["target"] is target


def test_cpu_detector_factory_remote_unsupported(monkeypatch):
    target = SimpleNamespace()
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "get_remote_target", lambda: target
    )
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "is_target_linuxlike", lambda: False
    )
    with pytest.raises(RuntimeError):
        CpuDetectorFactory.create(SimpleNamespace())


def test_cpu_detector_factory_local_linux(monkeypatch):
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "get_remote_target", lambda: None
    )
    monkeypatch.setattr(cpu_detector_module.sys, "platform", "linux")
    detector = CpuDetectorFactory.create(SimpleNamespace())
    assert isinstance(detector, LocalLinuxCpuDetector)


def test_cpu_detector_factory_local_windows(monkeypatch):
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "get_remote_target", lambda: None
    )
    monkeypatch.setattr(cpu_detector_module.sys, "platform", "win32")
    detector = CpuDetectorFactory.create(SimpleNamespace())
    assert isinstance(detector, LocalWindowsCpuDetector)


def test_cpu_detector_factory_unsupported_platform(monkeypatch):
    monkeypatch.setattr(
        cpu_detector_module.remote_target_manager, "get_remote_target", lambda: None
    )
    monkeypatch.setattr(cpu_detector_module.sys, "platform", "darwin")
    with pytest.raises(RuntimeError):
        CpuDetectorFactory.create(SimpleNamespace())
