# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

from time import sleep
from typing import Set

from topdown_tool.workload.workload import Workload


class SystemwideWorkload(Workload):
    """
    A workload implementation that does nothing except wait indefinitely.

    This workload does not monitor or process any PID. It simply waits in an infinite
    loop until the user interrupts the execution (e.g., via Ctrl + C). It is used for
    system-wide data capture where stopping is controlled externally.
    """

    def start(self) -> Set[int]:
        """
        Starts system-wide collection
        """
        return set()

    def wait(self) -> None:
        """
        Doesn't return, user must interrupt capture
        """
        # Do nothing, wait for user interrupt
        while True:
            sleep(86400)

    def kill(self) -> None:
        """
        Will throw an exception, not meant to be called
        """
        raise NotImplementedError("Can't kill workload in Systemwide mode")
