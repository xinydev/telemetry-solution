# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Factory for creating platform-specific Perf instances and managing perf-related configuration.

This module defines `PerfFactory`, which abstracts away platform resolution (Linux vs Windows)
and manages CLI options for customizing perf execution (e.g. binary path, arguments, sampling interval).
"""

import sys
import shutil
from typing import Type, Optional, Sequence
import argparse

from topdown_tool.perf.perf import Perf
from topdown_tool.perf.linux_perf import LinuxPerf
from topdown_tool.perf.windows_perf import WindowsPerf


class PerfFactory:
    """
    Factory class for instantiating the appropriate Perf implementation (LinuxPerf or WindowsPerf)
    based on the host platform.

    This class also encapsulates configuration passed via CLI (perf path, args, interval) and provides
    utility methods to query PMU capabilities or MIDR values in a unified way.
    """

    def __init__(self) -> None:
        """
        Initializes the factory with the correct platform-specific Perf implementation
        and sets default perf-related parameters (path, args, interval).
        """
        self._impl_class: Type[Perf] = LinuxPerf if sys.platform == "linux" else WindowsPerf
        self._perf_path: Optional[str] = None
        self._perf_args: Optional[str] = None
        self._interval: Optional[int] = None

    # pylint: disable=too-many-function-args
    def create(
        self,
        cores: Optional[Sequence[int]] = None,
    ) -> Perf:
        """
        Create a Perf instance using the resolved platform-specific class.

        Args:
            cores: Optional list of CPU core indices to record data on.

        Returns:
            A fully initialized Perf object (LinuxPerf or WindowsPerf).
        """
        return self._impl_class(
            cores,
            perf_args=self._perf_args,
            interval=self._interval,
        )

    def have_perf_privilege(self) -> bool:
        """
        Check whether the current user has sufficient privileges to run perf.

        Returns:
            True if perf can be used fully (e.g. CAP_PERFMON or -1 paranoid on Linux); otherwise False.
        """
        return self._impl_class.have_perf_privilege()

    def get_effective_perf_path(self) -> str:
        """
        Get the path to the perf executable that will be used.

        Returns:
            The path to the perf executable, or the default ("perf" or "wperf") if not set.
        """
        return self._perf_path or ("perf" if sys.platform == "linux" else "wperf")

    def is_perf_runnable(self) -> bool:
        """
        Check whether the perf tool is runnable on this system.

        Returns:
            True if the perf tool can be executed; otherwise False.
        """
        return shutil.which(self.get_effective_perf_path()) is not None

    def get_pmu_counters(self, core: int) -> int:
        """
        Return the number of PMU counters available on a specific core.

        Args:
            core: CPU core index to query.

        Returns:
            Number of available performance monitoring counters on the given core.
        """
        return self._impl_class.get_pmu_counters(core)

    def get_midr_value(self, core: int) -> int:
        """
        Retrieve the MIDR (Main ID Register) value for a given core.

        Args:
            core: CPU core index to query.

        Returns:
            MIDR_EL1 register value as an integer.
        """
        return self._impl_class.get_midr_value(core)

    def add_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        """
        Add CLI arguments related to perf configuration by creating a dedicated group.

        Args:
            parser: The top-level argument parser to which the perf options group will be added.
        """
        group = parser.add_argument_group("General perf capture options")
        group.add_argument("--perf-path", type=str, help="Path to perf executable")
        group.add_argument(
            "--perf-args",
            type=str,
            help="Additional command line arguments to pass to perf",
        )
        group.add_argument(
            "--interval",
            "-I",
            "-i",
            type=int,
            help="Collect/output data every <interval> milliseconds",
        )

    def process_cli_arguments(self, args: argparse.Namespace) -> None:
        """
        Store CLI-supplied perf configuration for later use in Perf creation.

        Args:
            args: Parsed argparse namespace containing perf-related flags.
        """
        self._perf_path = args.perf_path
        self._perf_args = args.perf_args
        self._interval = args.interval
        if self._perf_path is not None:
            self._impl_class.update_perf_path(self._perf_path)
