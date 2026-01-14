# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Factory for creating platform-specific Perf instances and managing perf-related configuration.

This module defines `PerfFactory`, which abstracts away platform resolution (Linux vs Windows)
and manages CLI options for customizing perf execution (e.g. binary path, arguments, sampling interval).
"""

import sys
import shutil
import logging
from dataclasses import dataclass
from typing import Type, Optional, Any, Dict
import argparse

from topdown_tool.perf.perf import Perf
from topdown_tool.perf.linux_perf import LinuxPerf
from topdown_tool.perf.remote_linux_perf import RemoteLinuxPerf
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.common import remote_target_manager


@dataclass
class PerfFactoryConfig:
    """Configuration for ``PerfFactory``."""

    perf_path: Optional[str] = None
    perf_args: Optional[str] = None
    interval: Optional[int] = None


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
        self._impl_class: Type[Perf]
        if sys.platform == "linux":
            self._impl_class = LinuxPerf
        else:
            self._impl_class = WindowsPerf
        self._perf_path: Optional[str] = None
        self._perf_args: Optional[str] = None
        self._interval: Optional[int] = None
        self._config = PerfFactoryConfig()

    @property
    def _current_impl(self) -> Type[Perf]:
        """Resolve the effective ``Perf`` implementation class.

        Returns:
            Type[Perf]: :class:`RemoteLinuxPerf` when a devlib Linux/Android target is
            configured, otherwise the platform-local class selected at init.
        """
        if (
            remote_target_manager.has_remote_target()
            and remote_target_manager.is_target_linuxlike()
        ):
            return RemoteLinuxPerf
        return self._impl_class

    def create(self) -> Perf:
        """Instantiate a ``Perf`` implementation for the active environment.

        Returns:
            Perf: A configured ``Perf`` instance ready for use.
        """
        impl = self._current_impl
        kwargs: Dict[str, Any] = {
            "perf_args": self._perf_args,
            "interval": self._interval,
        }
        if impl is RemoteLinuxPerf:
            kwargs["target"] = remote_target_manager.get_remote_target()
        return impl(**kwargs)

    # pylint: disable=import-outside-toplevel
    def have_perf_privilege(self) -> bool:
        """Check whether the active perf implementation reports sufficient privilege.

        Returns:
            bool: ``True`` when the delegated implementation (local or remote) indicates
            sufficient permissions. ``False`` means the check succeeded but reported limited
            privileges.

        """
        impl = self._current_impl
        logger = logging.getLogger(__name__)

        try:
            ok = impl.have_perf_privilege()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            message = f"Could not verify perf privileges via {impl.__name__}: {exc}"
            logger.error(message)
            raise RuntimeError(message) from exc

        if not ok:
            logger.warning(
                "%s reported limited perf privileges. Perf collection requires elevated access; aborting.",
                impl.__name__,
            )
        return ok

    def get_effective_perf_path(self) -> str:
        """
        Get the path to the perf executable that will be used.

        Returns:
            The path to the perf executable, or the default ("perf" or "wperf") if not set.
        """
        return self._perf_path or ("perf" if sys.platform == "linux" else "wperf")

    def is_perf_runnable(self) -> bool:
        """
        Check whether the selected perf tool appears runnable on the host or remote target.

        Returns:
            bool: ``True`` if the binary is discoverable/executable locally or if a remote
            target validates it on-device.
        """
        if remote_target_manager.has_remote_target():
            target = remote_target_manager.get_remote_target()
            if target is None:
                return False
            return RemoteLinuxPerf.is_remote_runnable(target, self._perf_path)
        return shutil.which(self.get_effective_perf_path()) is not None

    def get_pmu_counters(self, core: int) -> int:
        """
        Return the number of PMU counters available on a specific core.

        Args:
            core: CPU core index to query.

        Returns:
            Number of available performance monitoring counters on the given core.
        """
        return self._current_impl.get_pmu_counters(core)

    def get_midr_value(self, core: int) -> int:
        """Retrieve the MIDR (Main ID Register) value for a given core.

        Args:
            core (int): CPU core index to query.

        Returns:
            int: MIDR value supplied by the active implementation.
        """
        return self._current_impl.get_midr_value(core)

    def add_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Add CLI arguments related to perf configuration by creating a dedicated group.

        Args:
            parser: The top-level argument parser to which the perf options group will be added.
        """
        group = parser.add_argument_group("Perf Capture Options")
        group.add_argument(
            "--perf-path",
            type=str,
            help="Path to the perf executable (default: 'perf' on Linux, 'wperf' on Windows).",
        )
        group.add_argument(
            "--perf-args",
            type=str,
            help=(
                "Extra arguments passed verbatim to perf (quoted as a single string). "
                "Example: --perf-args '--call-graph dwarf'. "
                "Note: options may conflict with internally provided arguments."
            ),
        )
        group.add_argument(
            "--interval",
            "-I",
            type=int,
            help=(
                "Sampling/output interval in milliseconds. Only valid when CSV output is enabled "
                "(use with --cpu-generate-csv metrics and/or events)."
            ),
        )

    # pylint: disable=import-error, import-outside-toplevel
    def process_cli_arguments(self, args: argparse.Namespace) -> PerfFactoryConfig:
        """Build a perf configuration from CLI arguments without applying it.

        Args:
            args: Parsed namespace containing perf-related CLI options.

        Returns:
            PerfFactoryConfig: Configuration object reflecting the CLI input.
        """

        return PerfFactoryConfig(
            perf_path=getattr(args, "perf_path", None),
            perf_args=getattr(args, "perf_args", None),
            interval=getattr(args, "interval", None),
        )

    def configure_from_cli_arguments(self, args: argparse.Namespace) -> PerfFactoryConfig:
        """Apply CLI-derived perf configuration to the factory.

        Args:
            args: Parsed namespace containing perf-related CLI options.

        Returns:
            PerfFactoryConfig: The configuration that was applied.
        """

        config = self.process_cli_arguments(args)
        self.configure(config)
        return config

    def configure(self, config: PerfFactoryConfig) -> None:
        """Apply explicit configuration to the factory.

        Args:
            config (PerfFactoryConfig): Configuration values to apply.
        """

        self._config = config
        self._perf_path = config.perf_path
        self._perf_args = config.perf_args
        self._interval = config.interval
        if self._perf_path is not None:
            self._impl_class.update_perf_path(self._perf_path)
            # Ensure remote Linux runs pick up the same override even on non-Linux hosts.
            LinuxPerf.update_perf_path(self._perf_path)
            RemoteLinuxPerf.update_perf_path(self._perf_path)
