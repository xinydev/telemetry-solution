# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
This module provides the factory for creating CPU probe instances used in telemetry data capture.
It defines the CpuProbeFactory class, which is responsible for processing CLI arguments specific to CPU probing,
detecting CPU hardware details (via pluggable CpuDetector implementations) and loading telemetry specifications,
and creating CpuProbe objects accordingly.

Other Key Components:
    - CpuDetector hierarchy: Helpers that retrieve CPU details such as the number of cores,
      MIDR values, and compute unique CPU identifiers for local and remote targets.
    - CpuProbeFactory: Processes configuration options, updates CPU description mappings, and instantiates CpuProbe
      instances based on available CPU hardware information and supplied telemetry JSON files.

Usage Example:
    parser = argparse.ArgumentParser()
    cpu_group = parser.add_argument_group("CPU Probe Options")
    probe_factory = CpuProbeFactory()
    probe_factory.add_cli_arguments(cpu_group)
    args = parser.parse_args()
    if probe_factory.is_available():
         capture = probe_factory.configure_from_cli_arguments(args)
         if capture:
              probes = probe_factory.create(capture_data=True)
              # Probes are ready for telemetry capture.
         else:
              print("Only listing information; no capture will take place.")
    else:
         print("CPU probing is not available on this system.")
"""

import argparse
from dataclasses import dataclass, field, replace
import json
import os
from typing import Dict, List, Optional, Sequence, Tuple, Union
from rich import get_console
from rich.table import Table
from topdown_tool.common import ArgsError, range_decode, unwrap
from topdown_tool.cpu_probe.common import (
    COMBINED_STAGES,
    DEFAULT_ALL_STAGES,
    CpuProbeConfiguration,
)
from topdown_tool.cpu_probe.cpu_detector import (
    CpuDetector,
    CpuDetectorFactory,
)
from topdown_tool.cpu_probe.cpu_model import TelemetrySpecification
from topdown_tool.cpu_probe.cpu_probe import CpuProbe
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf import perf_factory, PerfFactory
import topdown_tool.probe as Base


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


@dataclass
class CpuProbeFactoryConfig:
    """Input configuration for CpuProbeFactory."""

    runtime: CpuProbeConfiguration
    spec_overrides: List[str] = field(default_factory=list)
    sme_overrides: List[Tuple[str, List[int]]] = field(default_factory=list)
    core_filter: Optional[List[int]] = None
    list_cores: bool = False
    csv_output_path: Optional[str] = None
    interval_ms: Optional[int] = None


class CpuProbeFactoryConfigBuilder(
    Base.ProbeFactoryCliConfigBuilder[CpuProbeFactoryConfig]
):
    """Builder translating CLI arguments into CpuProbeFactoryConfig instances."""

    def __init__(self, factory: "CpuProbeFactory") -> None:
        self._factory = factory

    def add_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register CPU probe CLI arguments on parser.

        Args:
            parser (argparse.ArgumentParser): Parser that receives CPU-specific option groups.

        This method organizes CPU options into dedicated argument groups:

            - Specification inclusion: --cpu, --sme
            - Specification inspection: --cpu-list-cores, --cpu-list-groups, --cpu-list-metrics, --cpu-list-events, --cpu-descriptions, --cpu-show-sample-events
            - Capture selection: --core, --cpu-no-multiplex, --cpu-collect-by, --cpu-metric-group, --cpu-node, --cpu-stages
            - Output control: --cpu-generate-csv
        """

        factory = self._factory
        spec_group = parser.add_argument_group(f"{factory.name()} - Specification")
        inspect_group = parser.add_argument_group(f"{factory.name()} - Inspection")
        capture_group = parser.add_argument_group(f"{factory.name()} - Capture Selection")
        output_group = parser.add_argument_group(f"{factory.name()} - Output")

        spec_group.add_argument(
            "--cpu",
            action="append",
            help="CPU telemetry specification JSON. If omitted, the spec is auto-detected. May be provided multiple times.",
        )
        spec_group.add_argument(
            "--sme",
            action="append",
            type=self._decode_sme_arg,
            help="Add an SME (Scalable Matrix Extension) telemetry specification for specific cores. Format: file.json:core-list (e.g., sme.json:0,2-3).",
        )

        # Specification inspection
        inspect_group.add_argument(
            "--cpu-list-cores",
            action="store_true",
            help="List detected CPU models and the core indices where they are present.",
        )
        inspect_group.add_argument(
            "--cpu-list-groups",
            action="store_true",
            help="List metric groups defined in the CPU telemetry specification.",
        )
        inspect_group.add_argument(
            "--cpu-list-metrics",
            action="store_true",
            help="List metrics defined in the CPU telemetry specification.",
        )
        inspect_group.add_argument(
            "--cpu-list-events",
            action="store_true",
            help="List CPU PMU events referenced by the CPU telemetry specification.",
        )
        inspect_group.add_argument(
            "--cpu-descriptions",
            "-d",
            dest="cpu_descriptions",
            action="store_true",
            help="When listing, include description text from the specification (works with all CPU inspection list options).",
        )
        inspect_group.add_argument(
            "--cpu-show-sample-events",
            action="store_true",
            help='When listing metrics, include suggested sampling (leader) events for accurate collection (e.g., for branch MPKI, sample "branch-misses" rather than "instructions-retired").',
        )

        # Capture selection (includes --core)
        capture_group.add_argument(
            "--core",
            "-C",
            type=range_decode,
            help="Restrict counting to specific CPUs. Accepts a comma-separated list and ranges (e.g., 0,2-3).",
        )
        capture_group.add_argument(
            "--cpu-no-multiplex",
            action="store_true",
            help="Disable CPU multiplexing",
        )
        capture_group.add_argument(
            "--cpu-collect-by",
            "-c",
            type=CollectBy.from_string,
            choices=list(CollectBy),
            default=CollectBy.METRIC,
            help="R|Control how events are grouped into perf event-groups and scheduled across runs:\n"
            "  • none   - capture each event independently.\n"
            "  • metric - capture together the set of events that form a metric (default).\n"
            "  • group  - capture together events from the same metric group.\n"
            "With multiplexing enabled, grouping affects how events are scheduled across runs; "
            "without multiplexing, it affects how events are grouped within a single run.",
        )
        capture_group.add_argument(
            "--cpu-metric-group",
            "-m",
            type=lambda x: x.split(","),
            help="List of metric groups to collect (provided as a comma-separated list). Unknown group names are ignored for CPUs whose spec doesn't define them. See --cpu-list-groups for available groups.",
        )
        capture_group.add_argument(
            "--cpu-node",
            "-n",
            help='Start metric collection from this methodology node and include its subtree (e.g., "frontend_bound"). See --cpu-list-metrics for available nodes.',
        )
        capture_group.add_argument(
            "--cpu-level", "-l", type=int, choices=[1, 2], help=argparse.SUPPRESS
        )
        capture_group.add_argument(
            "--cpu-stages",
            "-s",
            action=_ProcessStageArgs,
            default=DEFAULT_ALL_STAGES,
            help='Methodology stages to collect. One of: topdown, uarch, 1, 2, all, combined. Combine with commas (e.g., "topdown,uarch"). "combined" collects topdown metrics as a tree.',
        )

        # Output control
        output_group.add_argument(
            "--cpu-generate-csv",
            type=lambda s: [x.strip().lower() for x in s.split(",") if x.strip()],
            metavar="metrics[,events]",
            help="Generate CSV output for one or both: 'metrics' and/or 'events' (comma-separated). Requires --csv-output-path.",
        )
        output_group.add_argument("--cpu-dump-events", help=argparse.SUPPRESS)

        # Append CPU-specific examples to the global parser epilog
        cpu_examples = (
            "Examples:\n"
            "  Command capture (CSV metrics every 1000 ms):\n"
            "    topdown-tool --cpu-generate-csv metrics --csv-output-path out -I 1000 -- sleep 10\n"
            "  PID capture (events to CSV):\n"
            "    topdown-tool -p 1234 --cpu-generate-csv events --csv-output-path out\n"
            "  Capture both metrics and events to CSV:\n"
            "    topdown-tool --cpu-generate-csv metrics,events --csv-output-path out -- sleep 5\n"
            "  Inspect metrics with descriptions and sample events:\n"
            "    topdown-tool --cpu-list-metrics -d --cpu-show-sample-events\n"
        )
        if parser.epilog:
            parser.epilog = f"{parser.epilog}\n\n{cpu_examples}"
        else:
            parser.epilog = cpu_examples

    def process_cli_arguments(self, args: argparse.Namespace) -> CpuProbeFactoryConfig:
        """Convert parsed CLI arguments into a CpuProbeFactoryConfig.

        Args:
            args (argparse.Namespace): Parsed CLI arguments to interpret.

        Returns:
            CpuProbeFactoryConfig: Configuration populated from args.
        """

        return self.config_from_namespace(args)

    @staticmethod
    def config_from_namespace(args: argparse.Namespace) -> CpuProbeFactoryConfig:
        """Convert parsed CLI arguments into a CPU probe configuration.

        Args:
            args (argparse.Namespace): Parsed command-line arguments.

        Returns:
            CpuProbeFactoryConfig: Normalized configuration derived from args.
        """
        spec_overrides = list(getattr(args, "cpu", []) or [])
        sme_overrides_raw = getattr(args, "sme", None) or []
        sme_overrides = [entry for entry in sme_overrides_raw if entry]
        core_filter = getattr(args, "core", None)
        if core_filter is not None:
            core_filter = list(core_filter)
        metric_groups = getattr(args, "cpu_metric_group", None) or []
        stages = getattr(args, "cpu_stages", None)
        stages_list = list(stages) if stages is not None else list(DEFAULT_ALL_STAGES)
        runtime = CpuProbeConfiguration(
            cpu_dump_events=getattr(args, "cpu_dump_events", None),
            cpu_generate_csv=list(getattr(args, "cpu_generate_csv", []) or []),
            cpu_list_groups=getattr(args, "cpu_list_groups", False),
            cpu_list_metrics=getattr(args, "cpu_list_metrics", False),
            cpu_list_events=getattr(args, "cpu_list_events", False),
            multiplex=not getattr(args, "cpu_no_multiplex", False),
            collect_by=getattr(args, "cpu_collect_by", CollectBy.METRIC),
            metric_group=list(metric_groups),
            node=getattr(args, "cpu_node", None),
            level=getattr(args, "cpu_level", None),
            stages=stages_list,
            descriptions=getattr(args, "cpu_descriptions", False),
            show_sample_events=getattr(args, "cpu_show_sample_events", False),
        )

        return CpuProbeFactoryConfig(
            runtime=runtime,
            spec_overrides=spec_overrides,
            sme_overrides=sme_overrides,
            core_filter=core_filter,
            list_cores=getattr(args, "cpu_list_cores", False),
            csv_output_path=getattr(args, "csv_output_path", None),
            interval_ms=getattr(args, "interval", None),
        )

    @staticmethod
    def _decode_sme_arg(arg: str) -> Optional[Tuple[str, List[int]]]:
        """Decode the SME (Scalable Matrix Extension) argument from the CLI.

        Args:
            arg (str): Value formatted as file.json:core1,core2-coreN.

        Returns:
            Optional[Tuple[str, List[int]]]: The spec path and list of cores, or None.

        Example:
            --sme file.json:0,2-3 -> ("file.json", [0, 2, 3]).
        """

        if arg is None:
            return None
        path, temp = arg.rsplit(":", 1)
        return path, unwrap(range_decode(temp))


class CpuProbeFactory(Base.ProbeFactory[CpuProbeFactoryConfig]):
    """Factory class for creating CPU probe instances.

    Processes command line arguments related to CPU probing, sets up CPU-specific configurations by
    detecting hardware parameters via CpuDetector implementations, and creates CpuProbe instances configured with the appropriate
    telemetry specification.

    Example:
        cpu_group = parser.add_argument_group("CPU Probe Options")
        factory = CpuProbeFactory()
        factory.add_cli_arguments(cpu_group)

        should_capture = factory.configure_from_cli_arguments(args)
        if should_capture:
            probes = factory.create(capture_data=True)
    """

    METRICS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics")
    SCHEMAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")

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
        self._factory_config: Optional[CpuProbeFactoryConfig] = None
        self._sme_overrides: List[Tuple[str, List[int]]] = []
        self._cpu_detector: Optional[CpuDetector] = None

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

    def _get_config_builder(self) -> Base.ProbeFactoryCliConfigBuilder[CpuProbeFactoryConfig]:
        """Construct the CPU probe config builder tied to this factory."""

        return CpuProbeFactoryConfigBuilder(self)

    def get_description(self) -> str:
        """Return a short description of the CPU probe."""
        return "Collect Top-down CPU metrics; advanced options for specification inspection and targeted capture."

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

    def configure(self, config: CpuProbeFactoryConfig, **kwargs: object) -> bool:
        """Apply a configuration for the CPU probe factory.

        Args:
            config (CpuProbeFactoryConfig): CPU probe configuration values.
            **kwargs: Supported keyword-only arguments:
                cpu_detector (Optional[CpuDetector]): Preconfigured detector to reuse instead
                of creating a new one.

        Returns:
            bool: True if telemetry capture should proceed, False when only
                informational listing is required.
        """

        if not isinstance(config, CpuProbeFactoryConfig):
            raise TypeError("CpuProbeFactory.configure expected CpuProbeFactoryConfig")

        cpu_detector_kw = kwargs.pop("cpu_detector", None)
        if kwargs:
            unexpected_args = ", ".join(sorted(kwargs.keys()))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected_args}")

        cpu_detector: Optional[CpuDetector]
        if cpu_detector_kw is None:
            cpu_detector = None
        elif isinstance(cpu_detector_kw, CpuDetector):
            cpu_detector = cpu_detector_kw
        else:
            raise TypeError("cpu_detector must be a CpuDetector instance or None")

        runtime_conf = replace(config.runtime)
        normalized_csv: List[str] = []
        seen = set()
        for item in runtime_conf.cpu_generate_csv:
            lower = item.lower()
            if lower not in ("metrics", "events"):
                raise ArgsError(
                    f"Invalid value for --cpu-generate-csv: {item}. Use 'metrics' and/or 'events'."
                )
            if lower not in seen:
                seen.add(lower)
                normalized_csv.append(lower)

        # Replace returns a shallow copy; clone mutable lists so factory/runtime
        # tweaks don't mutate the config stored on CpuProbeFactoryConfig.
        runtime_conf.metric_group = list(runtime_conf.metric_group)
        runtime_conf.stages = list(runtime_conf.stages)

        require_csv_path = bool(runtime_conf.cpu_dump_events) or bool(normalized_csv)
        if require_csv_path and not config.csv_output_path:
            raise ArgsError("CSV output path must be specified with --csv-output-path")
        if config.interval_ms is not None and not normalized_csv:
            raise ArgsError("Must use interval option with CSV option")

        runtime_conf.cpu_generate_csv = normalized_csv

        self._cpu_detector = cpu_detector or CpuDetectorFactory.create(perf_factory)

        self._update_midr_cpu_core_map(config.core_filter, self._cpu_detector)
        self._update_cpu_descriptions(config, self._cpu_detector)
        self._list_cpus(config.list_cores, self._cpu_detector)

        runtime_conf.pid_tracking_applicable = (
            len(self._midr_core_map) == 1 and config.core_filter is None
        )

        self._conf = runtime_conf
        self._factory_config = config
        self._sme_overrides = [(path, list(cores)) for path, cores in config.sme_overrides]

        return not (
            config.list_cores
            or runtime_conf.cpu_list_groups
            or runtime_conf.cpu_list_metrics
            or runtime_conf.cpu_list_events
        )

    def _update_midr_cpu_core_map(
        self, core_filter: Optional[List[int]], cpu_detector: CpuDetector
    ) -> None:
        # Update the mapping of MIDR values to core indices based on the current configuration.
        #
        # This method populates the _midr_core_map dictionary, which maps each detected CPU's MIDR
        # to the list of core indices where that CPU is present.
        # Determine which cores to monitor; if none specified, use all available cores.
        cores_to_monitor = (
            list(range(cpu_detector.cpu_count())) if core_filter is None else list(core_filter)
        )

        # Build a mapping from MIDR to the list of core indices.
        self._midr_core_map = {}
        for core in cores_to_monitor:
            try:
                # Attempt to read the MIDR value for the core. If unsuccessful, skip the core.
                midr = cpu_detector.cpu_midr(core)
                self._midr_core_map.setdefault(midr, []).append(core)
            except Exception:  # pylint: disable=broad-exception-caught
                # Skip cores we can't read MIDR from (e.g., permission issues on target)
                pass

    # pylint: disable=too-many-locals
    def _update_cpu_descriptions(
        self, config: CpuProbeFactoryConfig, cpu_detector: CpuDetector
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

        # If the user provided CPU JSON files via configuration, override defaults.
        for cpu_file in config.spec_overrides:
            cpu_desc = TelemetrySpecification.load_from_json_file(cpu_file, self.SCHEMAS_DIR)
            implementer = int(cpu_desc.product_configuration.implementer, 16)
            variant = cpu_desc.product_configuration.major_revision
            architecture = 0xF
            part_num = int(cpu_desc.product_configuration.part_num, 16)
            revision = cpu_desc.product_configuration.minor_revision

            midr = self.build_midr(implementer, variant, architecture, part_num, revision)
            short_id = cpu_detector.cpu_id(midr)

            # Override both full and short format keys.
            cpu_descriptions[midr] = cpu_descriptions[short_id] = self._CpuDescription(
                path=cpu_file,
                content=cpu_desc,
            )

        # For cores without a user override, load the default JSON files.
        for midr, locations in self._midr_core_map.items():
            cpu_id = cpu_detector.cpu_id(midr)
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
                desc.content = TelemetrySpecification.load_from_json_file(
                    desc.path, self.SCHEMAS_DIR
                )

        self._cpu_descriptions = cpu_descriptions

    # FIXME: To move into cpu_cli_renderer
    def _list_cpus(self, list_requested: bool, cpu_detector: CpuDetector) -> None:
        # List the available CPUs and their corresponding core indices.
        #
        # This method outputs a table of detected CPUs, showing the product name and the indices of
        # the cores where each CPU is present. It is used for informational purposes to help users
        # understand the CPU topology on the system.
        if not list_requested:
            return

        table = Table(title="Available CPUs")
        for column in ("CPU", "Cores indices"):
            table.add_column(column)
        for midr, locations in self._midr_core_map.items():
            cpu_id = cpu_detector.cpu_id(midr)
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

    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def create(
        self,
        capture_data: bool = True,
        base_csv_dir: Optional[str] = None,
        perf_factory_instance: "PerfFactory" = perf_factory,
        cpu_detector: Optional[CpuDetector] = None,
    ) -> Tuple["CpuProbe", ...]:
        """Create CpuProbe instances based on the configured state and detected CPUs.

        This method generates a CpuProbe per unique MIDR type found across selected cores.
        Each probe is initialized with its corresponding telemetry specification and receives
        a shared PerfFactory instance, which constructs platform-specific Perf implementations
        internally.
        Args:
            capture_data (bool, optional): Flag indicating whether telemetry capture should be performed.
                Defaults to True.
            perf_factory_instance (PerfFactory): The factory used to create Perf instances.
            cpu_detector (Optional[CpuDetector]): Detector instance to use. When omitted, the
                detector resolved during CLI processing (or a new environment-appropriate one) is used.

        Returns:
            Tuple[CpuProbe, ...]: A tuple of instantiated CpuProbe objects.
        """
        if self._factory_config is None:
            raise RuntimeError("CpuProbeFactory must be configured before calling create().")

        self._cpu_detector = (
            cpu_detector or self._cpu_detector or CpuDetectorFactory.create(perf_factory_instance)
        )

        cpu_probes = []
        # Instantiate a CpuProbe for each detected CPU configuration.
        for midr, locations in self._midr_core_map.items():
            cpu_id = self._cpu_detector.cpu_id(midr)
            spec = None
            if midr in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[midr].content)
            elif cpu_id in self._cpu_descriptions:
                spec = unwrap(self._cpu_descriptions[cpu_id].content)

            if spec is not None:
                cpu_probes.append(
                    CpuProbe(
                        self._conf,
                        spec,
                        locations,
                        capture_data,
                        base_csv_dir,
                        perf_factory_instance,
                    )
                )

        # Create additional CpuProbe instances for SME elements if specified.
        for path, cores in self._sme_overrides:
            cme_desc = TelemetrySpecification.load_from_json_file(path, self.SCHEMAS_DIR)
            cpu_probes.append(
                CpuProbe(
                    self._conf,
                    cme_desc,
                    list(cores),
                    capture_data,
                    base_csv_dir,
                    perf_factory_instance,
                )
            )

        return tuple(cpu_probes)
