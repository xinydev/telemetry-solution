# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Shared test utilities for remote perf scenarios."""

from typing import Callable, List, Optional, Tuple


class FakeProcess:
    """Record interactions with a background process used during tests."""

    def __init__(
        self, *, pid: Optional[int] = None, wait_impl: Optional[Callable[[], None]] = None
    ) -> None:
        self.pid = pid or 4242
        self.signals: List[int] = []
        self.wait_count = 0
        self._wait_impl = wait_impl

    def send_signal(self, sig) -> None:
        self.signals.append(sig)

    def wait(self) -> None:
        self.wait_count += 1
        if self._wait_impl is not None:
            self._wait_impl()


class RecordingTarget:
    """Minimal devlib-like target that tracks shell interactions."""

    def __init__(
        self,
        *,
        execute_impl: Optional[Callable[[str], str]] = None,
        background_impl: Optional[Callable[[str], FakeProcess]] = None,
        pull_impl: Optional[Callable[[str, str], None]] = None,
        push_impl: Optional[Callable[[str, str], None]] = None,
        process: Optional[FakeProcess] = None,
        working_directory: str = "/remote/workdir",
        abi: str = "fakeabi",
        workpath_impl: Optional[Callable[[str], str]] = None,
    ) -> None:
        self.os = "linux"
        self.username = "root"
        self.abi = abi
        self.working_directory = working_directory
        self.exec_calls: List[str] = []
        self.background_calls: List[str] = []
        self.pull_calls: List[Tuple[str, str]] = []
        self.push_calls: List[Tuple[str, str]] = []
        self.remote_files = {}

        self.execute_impl = execute_impl
        self.background_impl = background_impl
        self.pull_impl = pull_impl
        self.push_impl = push_impl
        self.process: FakeProcess = process or FakeProcess()
        self.workpath_impl = workpath_impl

    def get_workpath(self, _name: str) -> str:
        if self.workpath_impl is not None:
            return self.workpath_impl(_name)
        return self.working_directory

    def execute(self, cmd: str, **_kwargs) -> str:
        self.exec_calls.append(cmd)
        if self.execute_impl is not None:
            return self.execute_impl(cmd)
        return ""

    def background(self, cmd: str, **_kwargs) -> FakeProcess:
        self.background_calls.append(cmd)
        if self.background_impl is not None:
            return self.background_impl(cmd)
        return self.process

    def pull(self, remote: str, local: str, **_kwargs) -> None:
        self.pull_calls.append((remote, local))
        if self.pull_impl is not None:
            self.pull_impl(remote, local)
        else:
            raise RuntimeError("pull failed")

    def push(self, src: str, dst: str, **_kwargs) -> None:
        self.push_calls.append((src, dst))
        if self.push_impl is not None:
            self.push_impl(src, dst)
