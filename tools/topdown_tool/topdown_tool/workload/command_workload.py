# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm LimitedLimited

from types import TracebackType
from typing import List, Optional, Set, Type

from topdown_tool.workload.workload import Workload
from topdown_tool.common import CommandRunner, ManagedProcess, get_command_runner


class CommandWorkload(Workload):
    """
    CommandWorkload delegates process management to a platform-specific CommandRunner.

    On Linux, the spawned process is initially stopped (SIGSTOP) and resumed on start()
    with SIGCONT. On Windows, the process starts immediately and is tracked via a Job
    Object that ensures its entire process tree is terminated on kill().
    """

    def __init__(self, command: List[str], runner: Optional[CommandRunner] = None):
        # Setup signal handlers
        super().__init__()

        self.finished = False
        self.pid: int = -1

        # Create platform-specific runner and spawn the process
        self._runner: CommandRunner = runner or get_command_runner()
        self._proc: ManagedProcess = self._runner.spawn(command)

        # Expose standard fields
        self.pid = self._proc.pid
        self.pids = {self.pid}

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        self.kill()

    def __del__(self) -> None:
        if getattr(self, "_proc", None):
            self.kill()

    def start(self) -> Set[int]:
        """
        Starts collection for a single command
        """
        self._proc.start()
        return {self.pid}

    def wait(self) -> Optional[int]:
        """
        Wait for a single process to complete
        """
        if self.finished:
            return None

        try:
            self._proc.wait()
        except (InterruptedError, KeyboardInterrupt) as e:
            self.kill()
            raise e
        self.finished = True
        return self.pid

    def kill(self) -> None:
        """
        This function will kill command workload
        """
        if not self.finished:
            try:
                self._proc.kill()
            finally:
                self.finished = True
