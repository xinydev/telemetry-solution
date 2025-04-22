# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
This module provides the factory for creating CPU probe instances used in telemetry data capture.
It defines the CpuProbeFactory class, which is responsible for processing CLI arguments specific to CPU probing,
detecting CPU hardware details (using the CPUDetect helper class) and loading telemetry specifications, and creating
CpuProbe objects accordingly.

Other Key Components:
    - CPUDetect: A helper class that retrieves CPU details such as the number of cores,
      MIDR values, and computes unique CPU identifiers.
    - CpuProbeFactory: Processes configuration options, updates CPU description mappings, and instantiates CpuProbe instances
      based on available CPU hardware information and supplied telemetry JSON files.

Usage Example:
    parser = argparse.ArgumentParser()
    cpu_group = parser.add_argument_group("CPU Probe Options")
    probe_factory = CpuProbeFactory()
    probe_factory.add_cli_arguments(cpu_group)
    args = parser.parse_args()
    if probe_factory.is_available():
         if probe_factory.process_cli_arguments(args):
              probes = probe_factory.create(args, capture_data=True)
              # Probes are ready for telemetry capture.
         else:
              print("Only listing information; no capture will take place.")
    else:
         print("CPU probing is not available on this system.")
"""

import argparse
from dataclasses import dataclass
import json
import os
import sys
from typing import Dict, List, Optional, Sequence, Tuple, Type, Union
from rich import get_console
from rich.table import Table
from topdown_tool.common import ArgsError, range_decode, unwrap
from topdown_tool.cpu_probe.common import (
    COMBINED_STAGES,
    DEFAULT_ALL_STAGES,
    CpuProbeConfiguration,
)
from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification
from topdown_tool.cpu_probe.cpu_probe import CpuProbe
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf import Perf
import topdown_tool.probe as Base


class CPUDetect:
    """Helper class for detecting CPU details necessary for CPU probing.

    This class provides utility methods for obtaining the number of CPU cores, reading a core's MIDR value,
    and computing a unique CPU identifier based on the MIDR.
    """

    MIDR_PATH = "/sys/devices/system/cpu/cpu{}/regs/identification/midr_el1"

    @staticmethod
    def cpu_count() -> int:
        """Return the number of CPU cores detected on the system.

        Returns:
            int: The count of CPU cores.

        Raises:
            Exception: If os.cpu_count() returns None.
        """
        return unwrap(os.cpu_count(), "os.cpu_count() returned an unnexpected value")

    @staticmethod
    def cpu_midr(core: int) -> int:
        """Retrieve the MIDR (Main ID Register) value for a specified core.

        Args:
            core (int): The core index to query.

        Returns:
            int: The MIDR value as an integer.
        """
        if sys.platform == "linux":
            with open(CPUDetect.MIDR_PATH.format(core), encoding="utf-8") as f:
                midr = int(f.readline(), 16)
        elif sys.platform == "win32":
            midr = Perf.get_midr_value_windows()
        else:
            raise RuntimeError("MIDR only available on Linux and Windows platforms")
        return midr

    @staticmethod
    def cpu_id(midr: int) -> int:
        """Compute a unique CPU identifier from the MIDR value.

        Args:
            midr (int): The MIDR value.

        Returns:
            int: The computed CPU identifier.
        """
        implementer = midr >> 24 & 0xFF
        part_num = midr >> 4 & 0xFFF
        return (implementer << 12) | part_num


class _ProcessStageArgs(argparse.Action):
    STAGE_NAMES = {"topdown": 1, "uarch": 2, "1": 1, "2": 2}

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Union[str, Sequence, None],
        _option_string: Optional[str] = None,
    ) -> None:
        if isinstance(values, str):
            if values.lower() == "all":
                value = DEFAULT_ALL_STAGES
            elif values.lower() == "combined":
                value = COMBINED_STAGES
            else:
                try:
                    value = sorted(
                        set(
                            _ProcessStageArgs.STAGE_NAMES[x.lower().strip()]
                            for x in values.split(",")
                        )
                    )
                except KeyError as e:
                    parser.error(f'"{e.args[0]}" is not a valid stage name.')
        else:
            assert False
        setattr(namespace, self.dest, value)


class CpuProbeFactory(Base.ProbeFactory):
    """Factory class for creating CPU probe instances.

    Processes command line arguments related to CPU probing, sets up CPU-specific configurations by
    detecting hardware parameters via CPUDetect, and creates CpuProbe instances configured with the appropriate
    telemetry specification.

    Example:
        cpu_group = parser.add_argument_group("CPU Probe Options")
        factory = CpuProbeFactory()
        factory.add_cli_arguments(cpu_group)

        if factory.process_cli_arguments(args):
            probes = factory.create(args, capture_data=True)
    """

    METRICS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics")

    @dataclass
    class _CpuDescription:
        # Internal dataclass for storing CPU probe JSON descriptions.
        path: str
        content: Optional[TelemetrySpecification] = None

    def __init__(self) -> None:
        """Initialize a CpuProbeFactory instance.

        Sets up the default configuration and mappings for CPU descriptions.
        """
        super().__init__()
        self._conf = CpuProbeConfiguration()
        self._midr_core_map: Dict[int, List[int]] = {}
        # Default mapping of CPU ID to a JSON description file
        self._cpu_descriptions: Dict[int, CpuProbeFactory._CpuDescription] = {}

    def name(self) -> str:
        """Return the name of the probe.

        Returns:
            str: The string "CPU".
        """
        return "CPU"

    def is_available(self) -> bool:
        """Check if CPU probing is available on the current system.

        Returns:
            bool: Always returns True (can be extended in the future to check hardware support).
        """
        return True

    @staticmethod
    def _decode_sme_arg(arg: str) -> Optional[Tuple[str, List[int]]]:
        """Decode the SME (Scallable Matrix Extension) argument from the command line.

        Args:
            arg (str): The SME argument string, expected format: 'file.json:core1,core2-coreN'.

        Returns:
            Optional[Tuple[str, List[int]]]: A tuple containing the file path and a list of core indices.

        Example:
            --sme file.json:0,2-3
            -> ('file.json', [0, 2, 3])
        """
        if arg is None:
            return None
        path, temp = arg.rsplit(":", 1)
        return path, unwrap(range_decode(temp))

    @staticmethod
    def build_midr(
        implementer: int, variant: int, architecture: int, part_num: int, revision: int
    ) -> int:
        """Constructs an MIDR value from its field components.

        Args:
            implementer: The implementer field (8 bits).
            variant: The major revision or variant field (4 bits).
            architecture: The architecture field (4 bits).
            part_num: The part number field (12 bits).
            revision: The minor revision field (4 bits).

        Returns:
            The constructed MIDR value (int).
        """
        return implementer << 24 | variant << 20 | architecture << 16 | part_num << 4 | revision

    def add_cli_arguments(self, argument_group: argparse._ArgumentGroup) -> None:
        """Register CPU-probing command-line arguments to the parser.

        Args:
            argument_group (argparse._ArgumentGroup): The argument group where CPU-specific options are added.

        The registered options include specifying CPU description files, selecting cores,
        providing SME JSON file configurations, and options to list available CPUs, metric groups, metrics, or events.
        """
        argument_group.add_argument(
            "--cpu",
            action="append",
            help="CPU information file name. CPU name is auto-detected, if this option is not provided.",
        )
        argument_group.add_argument(
            "--core",
            "-C",
            type=range_decode,
            help="Count only on the list of CPUs provided. Multiple CPUs can be provided as a comma-separated list with no space.",
        )
        argument_group.add_argument(
            "--sme",
            action="append",
            type=self._decode_sme_arg,
            help="Specify a SME JSON file and cores to which it applies. Format: cme_data.json:0,2-3.",
        )
        argument_group.add_argument(
            "--cpu-list", action="store_true", help="List available CPUs and exit"
        )
        argument_group.add_argument(
            "--cpu-list-groups",
            action="store_true",
            help="List available CPU metric groups and exit",
        )
        argument_group.add_argument(
            "--cpu-list-metrics",
            action="store_true",
            help="List available CPU metrics and exit",
        )
        argument_group.add_argument(
            "--cpu-list-events",
            action="store_true",
            help="List available CPU events and exit",
        )
        argument_group.add_argument(
            "--cpu-no-multiplex",
            action="store_true",
            help="Don't use CPU PMU multiplexing.",
        )
        argument_group.add_argument(
            "--cpu-collect-by",
            "-c",
            type=CollectBy.from_string,
            choices=list(CollectBy),
            default=CollectBy.METRIC,
            help='When multiplexing, collect events grouped by "none", "metric" (default), or "group". This can avoid comparing data collected during different time periods.',
        )
        argument_group.add_argument(
            "--cpu-metric-group",
            "-m",
            type=lambda x: x.split(","),
            help="Comma separated list of metric groups to collect. See --cpu-list-groups for available groups",
        )
        argument_group.add_argument(
            "--cpu-node",
            "-n",
            help='Name of topdown node and its descendants (e.g. "frontend_bound"). See --cpu-list-metrics for available nodes',
        )
        argument_group.add_argument(
            "--cpu-level", "-l", type=int, choices=[1, 2], help=argparse.SUPPRESS
        )
        argument_group.add_argument(
            "--cpu-stages",
            "-s",
            action=_ProcessStageArgs,
            default=DEFAULT_ALL_STAGES,
            help='Control which stages to display, separated by a comma. e.g. "topdown,uarch" or "1,2" or "all". "combined" can be used to display topdown metrics as a tree.',
        )
        argument_group.add_argument(
            "--cpu-descriptions",
            "-d",
            action="store_true",
            help="Show group/metric descriptions",
        )
        argument_group.add_argument(
            "--cpu-show-sample-events",
            action="store_true",
            help="Show sample events for metrics",
        )
        argument_group.add_argument("--cpu-csv", help="Output directory for metric CSV data")
        argument_group.add_argument("--cpu-dump-events", help=argparse.SUPPRESS)

    def process_cli_arguments(
        self, args: argparse.Namespace, cpu_detect: Type[CPUDetect] = CPUDetect
    ) -> bool:
        """Process and validate command-line arguments for CPU probing.

        This method updates internal configuration based on CLI input, detects available CPUs,
        and optionally lists CPU information if requested.

        Args:
            args (argparse.Namespace): Parsed command-line arguments.
            cpu_detect (Type[CPUDetect], optional): Utility class for CPU detection. Defaults to CPUDetect.

        Returns:
            bool: True if actual telemetry capture should proceed; False if only informational output is desired.

        Raises:
            ArgsError: If required argument combinations are missing.
        """
        conf = self._conf
        conf.csv = args.cpu_csv
        conf.cpu_dump_events = args.cpu_dump_events
        if args.interval is not None and conf.csv is None and conf.cpu_dump_events is None:
            raise ArgsError("Must use interval option with CSV option")
        conf.cpu_list_groups = args.cpu_list_groups
        conf.cpu_list_metrics = args.cpu_list_metrics
        conf.cpu_list_events = args.cpu_list_events
        conf.multiplex = not args.cpu_no_multiplex
        conf.collect_by = args.cpu_collect_by
        conf.metric_group = args.cpu_metric_group
        conf.node = args.cpu_node
        conf.level = args.cpu_level
        conf.stages = args.cpu_stages
        conf.descriptions = args.cpu_descriptions
        conf.show_sample_events = args.cpu_show_sample_events
        conf.events_csv = args.events_csv

        # Update CPU core mapping based on provided or default core list.
        self._update_midr_cpu_core_map(args, cpu_detect)
        # Update CPU descriptions by loading telemetry JSON files, with CLI overrides if provided.
        self._update_cpu_descriptions(args, cpu_detect)
        # List detected CPUs if the --cpu-list argument was specified.
        self._list_cpus(args, cpu_detect)  # Kind of hacky to have it here.

        return not (
            args.cpu_list or args.cpu_list_groups or args.cpu_list_metrics or args.cpu_list_events
        )

    def _update_midr_cpu_core_map(
        self, args: argparse.Namespace, cpu_detect: Type[CPUDetect] = CPUDetect
    ) -> None:
        # Update the mapping of MIDR values to core indices based on the current configuration.
        #
        # This method populates the _midr_core_map dictionary, which maps each detected CPU's MIDR
        # to the list of core indices where that CPU is present.

        # Determine which cores to monitor; if none specified, use all available cores.
        cores_to_monitor = list(range(cpu_detect.cpu_count())) if args.core is None else args.core

        # Build a mapping from MIDR to the list of core indices.
        self._midr_core_map = {}
        for core in cores_to_monitor:
            try:
                # Attempt to read the MIDR value for the core. If unsuccessful, skip the core.
                midr = cpu_detect.cpu_midr(core)
                self._midr_core_map.setdefault(midr, []).append(core)
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    # pylint: disable=too-many-locals
    def _update_cpu_descriptions(
        self, args: argparse.Namespace, cpu_detect: Type[CPUDetect] = CPUDetect
    ) -> None:
        # Update the CPU descriptions mapping based on available telemetry JSON files and user configuration.
        #
        # This method loads the default CPU descriptions from the mapping.json file, overrides them with
        # any user-specified CPU JSON files, and ensures that all CPU configurations have a valid description
        # loaded.
        cpu_descriptions: Dict[int, CpuProbeFactory._CpuDescription] = {}

        with open(os.path.join(self.METRICS_DIR, "mapping.json"), encoding="utf-8") as f:
            cpus_mapping = json.load(f)
        for cpu_id, information in cpus_mapping.items():
            cpu_descriptions[int(cpu_id, 16)] = self._CpuDescription(
                path=os.path.join(self.METRICS_DIR, information["name"] + ".json")
            )

        # If the user provided CPU JSON files via CLI, override defaults.
        if args.cpu is not None:
            for cpu_file in args.cpu:
                cpu_desc = TelemetrySpecification.load_from_json_file(cpu_file)
                implementer = int(cpu_desc.product_configuration.implementer, 16)
                variant = cpu_desc.product_configuration.major_revision
                architecture = 0xF
                part_num = int(cpu_desc.product_configuration.part_num, 16)
                revision = cpu_desc.product_configuration.minor_revision

                midr = self.build_midr(implementer, variant, architecture, part_num, revision)
                short_id = cpu_detect.cpu_id(midr)

                # Override both full and short format keys.
                cpu_descriptions[midr] = cpu_descriptions[short_id] = self._CpuDescription(
                    path=cpu_file,
                    content=cpu_desc,
                )

        # For cores without a user override, load the default JSON files.
        for midr, locations in self._midr_core_map.items():
            cpu_id = cpu_detect.cpu_id(midr)
            desc = None
            if midr in cpu_descriptions:
                desc = cpu_descriptions[midr]
            elif cpu_id in cpu_descriptions:
                desc = cpu_descriptions[cpu_id]
            else:
                get_console().print(
                    "Unknown CPU at cores:",
                    ", ".join(map(str, locations)),
                    "(skipping capture)",
                )
            if desc is not None and not desc.content:
                desc.content = TelemetrySpecification.load_from_json_file(desc.path)

        self._cpu_descriptions = cpu_descriptions

    # FIXME: To move into cpu_cli_renderer
    def _list_cpus(self, args: argparse.Namespace, cpu_detect: Type[CPUDetect] = CPUDetect) -> None:
        # List the available CPUs and their corresponding core indices.
        #
        # This method outputs a table of detected CPUs, showing the product name and the indices of the cores
        # where each CPU is present. It is used for informational purposes to help users understand the
        # CPU topology on the system.
        if not args.cpu_list:
            return

        table = Table(title="Available CPUs")
        for column in ("CPU", "Cores indices"):
            table.add_column(column)
        for midr, locations in self._midr_core_map.items():
            cpu_id = cpu_detect.cpu_id(midr)
            if midr in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[midr].content)
            elif cpu_id in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[cpu_id].content)
            else:
                # This should not happen
                continue
            table.add_row(
                spec.product_configuration.product_name,
                ", ".join(map(str, locations)),
            )
        get_console().print(table)

    def create(
        self,
        args: argparse.Namespace,
        capture_data: bool = True,
        perf_class: Type[Perf] = Perf,
        cpu_detect: Type[CPUDetect] = CPUDetect,
    ) -> Tuple["CpuProbe", ...]:
        """Create CpuProbe instances based on CLI configuration and detected CPUs.

        Args:
            args (argparse.Namespace): The parsed command-line arguments.
            capture_data (bool, optional): Flag indicating whether telemetry capture should be performed.
                Defaults to True.
            perf_class (Type[Perf], optional): The class responsible for performance event capture.
                Defaults to Perf.
            cpu_detect (Type[CPUDetect], optional): The CPU detection utility class.
                Defaults to CPUDetect.

        Returns:
            Tuple[CpuProbe, ...]: A tuple of instantiated CpuProbe objects.
        """
        cpu_probes = []
        # Instantiate a CpuProbe for each detected CPU configuration.
        for midr, locations in self._midr_core_map.items():
            cpu_id = cpu_detect.cpu_id(midr)
            spec = None
            if midr in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[midr].content)
            elif cpu_id in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[cpu_id].content)

            if spec is not None:
                cpu_probes.append(CpuProbe(self._conf, spec, locations, capture_data, perf_class))

        # Create additional CpuProbe instances for SME elements if specified.
        if args.sme is not None:
            for cme in args.sme:
                cme_desc = TelemetrySpecification.load_from_json_file(cme[0])
                cpu_probes.append(CpuProbe(self._conf, cme_desc, cme[1], capture_data, perf_class))

        return tuple(cpu_probes)
