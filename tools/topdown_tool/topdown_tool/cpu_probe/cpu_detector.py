# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""CPU detector implementations and factory selection helpers."""

import logging
import os
import sys
from abc import ABC, abstractmethod
from typing import Optional

from topdown_tool.common import range_decode, remote_target_manager, unwrap
from topdown_tool.common.devlib_types import Target
from topdown_tool.common.remote_utils import remote_path_exists, remote_read_text
from topdown_tool.perf import PerfFactory, perf_factory

LINUX_MIDR_PATH = "/sys/devices/system/cpu/cpu{}/regs/identification/midr_el1"


class CpuDetector(ABC):
    """Abstract interface for retrieving CPU topology information.

    Sub-classes expose platform-specific mechanisms to detect logical core counts
    and extract MIDR (Main ID Register) values.
    """

    def __init__(self, perf_factory_instance: "PerfFactory" = perf_factory) -> None:
        """Initialise the detector.

        Args:
            perf_factory_instance: Perf factory used for MIDR helpers on platforms where
                direct access is not available.
        """
        self._perf_factory = perf_factory_instance

    @abstractmethod
    def cpu_count(self) -> int:
        """Return the number of available CPU cores.

        Returns:
            int: Logical CPU count visible to the detector.
        """

    @abstractmethod
    def cpu_midr(self, core: int) -> int:
        """Return the MIDR value for the requested core.

        Args:
            core: Zero-based core index whose MIDR value should be returned.

        Returns:
            int: Raw MIDR value for the provided core.
        """

    @staticmethod
    def cpu_id(midr: int) -> int:
        """Derive a compact CPU identifier from an MIDR value.

        Args:
            midr: Raw MIDR value.

        Returns:
            int: Identifier composed from implementer and part number fields.
        """

        implementer = (midr >> 24) & 0xFF
        part_num = (midr >> 4) & 0xFFF
        return (implementer << 12) | part_num

    @staticmethod
    def compose_midr(
        implementer: int, variant: int, architecture: int, part_num: int, revision: int
    ) -> int:
        """Compose an MIDR value from individual fields.

        Args:
            implementer: Implementer field value.
            variant: Variant field value.
            architecture: Architecture field value.
            part_num: Part-number field value.
            revision: Revision field value.

        Returns:
            int: Synthesised MIDR value.
        """

        return (
            (implementer << 24)
            | (variant << 20)
            | (architecture << 16)
            | (part_num << 4)
            | revision
        )


def _midr_from_cpuinfo(cpuinfo: str, core: int) -> Optional[int]:
    """Extract the MIDR value for *core* from a ``/proc/cpuinfo``-style dump.

    Args:
        cpuinfo: Full text contents of ``/proc/cpuinfo``.
        core: Zero-based core index whose MIDR should be located.

    Returns:
        Optional[int]: MIDR value when all required fields are present, otherwise ``None``.
    """

    # pylint: disable=too-many-branches
    blocks = [b.strip() for b in cpuinfo.split("\n\n") if b.strip()]
    block = None
    for candidate in blocks:
        for line in candidate.splitlines():
            if line.lower().startswith("processor"):
                try:
                    idx = int(line.split(":")[1].strip())
                    if idx == core:
                        block = candidate
                        break
                except Exception:  # pylint: disable=broad-exception-caught
                    continue
        if block is not None:
            break
    if block is None:
        block = cpuinfo

    impl = var = part = rev = None
    for line in block.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        val = value.strip()
        if key == "cpu implementer":
            impl = int(val, 16) if val.startswith("0x") else int(val)
        elif key == "cpu variant":
            var = int(val, 16) if val.startswith("0x") else int(val)
        elif key == "cpu part":
            part = int(val, 16) if val.startswith("0x") else int(val)
        elif key == "cpu revision":
            rev = int(val, 16) if val.startswith("0x") else int(val)

    if impl is not None and var is not None and part is not None and rev is not None:
        return CpuDetector.compose_midr(impl, var, 0xF, part, rev)
    return None


class LocalLinuxCpuDetector(CpuDetector):
    """CpuDetector implementation for local Linux hosts."""

    def cpu_count(self) -> int:
        """Return the logical CPU count for the local Linux system.

        Returns:
            int: Number of logical CPUs reported by the host kernel.
        """
        count = os.cpu_count()
        return unwrap(count, "os.cpu_count() returned an unexpected value")

    def cpu_midr(self, core: int) -> int:
        """Read the MIDR value for a local Linux core.

        Args:
            core: Zero-based core index to query.

        Returns:
            int: MIDR value for the core.

        Raises:
            RuntimeError: If the MIDR cannot be determined.
        """
        try:
            with open(LINUX_MIDR_PATH.format(core), encoding="utf-8") as midr_file:
                return int(midr_file.readline(), 16)
        except OSError:
            pass

        try:
            with open("/proc/cpuinfo", encoding="utf-8") as cpuinfo_file:
                cpuinfo = cpuinfo_file.read()
            midr = _midr_from_cpuinfo(cpuinfo, core)
            if midr is not None:
                return midr
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        raise RuntimeError(f"Unable to read MIDR for core {core} from local Linux host")


class LocalWindowsCpuDetector(CpuDetector):
    """CpuDetector implementation for local Windows hosts."""

    def cpu_count(self) -> int:
        """Return the logical CPU count for Windows.

        Returns:
            int: Number of logical CPUs reported by the Windows runtime.
        """
        count = os.cpu_count()
        return unwrap(count, "os.cpu_count() returned an unexpected value")

    def cpu_midr(self, core: int) -> int:
        """Query the MIDR value for a Windows core via the perf backend.

        Args:
            core: Zero-based core index to query.

        Returns:
            int: MIDR value supplied by the platform perf factory.
        """
        return self._perf_factory.get_midr_value(core)


class RemoteLinuxLikeCpuDetector(CpuDetector):
    """CpuDetector implementation for remote Linux/Android targets accessed via devlib."""

    def __init__(self, target: "Target", perf_factory_instance: "PerfFactory") -> None:
        """Initialise the detector with a connected devlib target.

        Args:
            target: Devlib target providing remote execution helpers.
            perf_factory_instance: Perf factory used for additional helpers.
        """
        super().__init__(perf_factory_instance)
        self._target = target

    def cpu_count(self) -> int:
        """Return the logical CPU count for the remote target.

        Returns:
            int: Number of logical CPUs detected on the remote device.

        Raises:
            RuntimeError: If the CPU count cannot be determined from the target.
        """
        txt = remote_read_text(self._target, "/sys/devices/system/cpu/present")
        if not txt:
            txt = remote_read_text(self._target, "/sys/devices/system/cpu/online")
        if txt:
            cores = range_decode(txt) or []
            if cores:
                return max(cores) + 1
        try:
            out = self._target.execute("nproc", check_exit_code=False)
            if isinstance(out, (bytes, bytearray)):
                out = out.decode("utf-8", errors="ignore")
            return int(str(out).strip())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.exception("Unable to determine CPU count from remote target: %s", exc)
            raise RuntimeError("Unable to determine CPU count from remote target") from exc

    # pylint: disable=too-many-locals,too-many-branches,too-many-nested-blocks
    def cpu_midr(self, core: int) -> int:
        """Return the raw MIDR value for a remote core.

        Args:
            core: Zero-based core index on the remote target.

        Returns:
            int: MIDR value as an integer.

        Raises:
            RuntimeError: If the MIDR cannot be obtained.
        """
        sysfs_path = LINUX_MIDR_PATH.format(core)
        if remote_path_exists(self._target, sysfs_path):
            txt = remote_read_text(self._target, sysfs_path)
            if txt:
                try:
                    return int(txt, 16)
                except Exception:  # pylint: disable=broad-exception-caught
                    pass

        try:
            # Mirror the LocalLinuxCpuDetector fallback: a number of remote devices (Android kernels
            # and some cloud targets) expose MIDR values only via /proc/cpuinfo.
            cpuinfo = remote_read_text(self._target, "/proc/cpuinfo") or ""
            midr = _midr_from_cpuinfo(cpuinfo, core)
            if midr is not None:
                return midr
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        raise RuntimeError(f"Unable to read MIDR for core {core} from remote target")


class CpuDetectorFactory:
    """Factory that selects the appropriate :class:`CpuDetector` for the environment."""

    @staticmethod
    def create(perf_factory_instance: "PerfFactory" = perf_factory) -> CpuDetector:
        """Create a detector matching the current host or configured target.

        Args:
            perf_factory_instance: Perf factory whose target configuration is inspected.

        Returns:
            CpuDetector: Detector tailored to the local host or remote target.

        Raises:
            RuntimeError: If the current platform or target type is unsupported.
        """
        target: Optional["Target"] = remote_target_manager.get_remote_target()
        if target is not None:
            if remote_target_manager.is_target_linuxlike():
                return RemoteLinuxLikeCpuDetector(target, perf_factory_instance)
            raise RuntimeError("CPU detection is not implemented for the configured remote target")

        if sys.platform == "linux":
            return LocalLinuxCpuDetector(perf_factory_instance)
        if sys.platform == "win32":
            return LocalWindowsCpuDetector(perf_factory_instance)
        raise RuntimeError(f"Unsupported platform for CPU detection: {sys.platform}")


__all__ = [
    "CpuDetector",
    "CpuDetectorFactory",
    "LocalLinuxCpuDetector",
    "LocalWindowsCpuDetector",
    "RemoteLinuxLikeCpuDetector",
]
