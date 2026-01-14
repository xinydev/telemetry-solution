# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Workload Automation instrument for integrating topdown-tool."""

import glob
import os
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

from topdown_tool import Probe
from topdown_tool.common import remote_target_manager
from topdown_tool.common.remote_utils import remote_cleanup_target_temp_dirs
from topdown_tool.cpu_probe import CpuProbeFactory, CpuProbeFactoryConfig
from topdown_tool.cpu_probe.common import DEFAULT_ALL_STAGES, CpuProbeConfiguration
from topdown_tool.perf import perf_factory
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf_factory import PerfFactoryConfig

try:  # pragma: no cover - optional WA dependency for linting environments
    from wa import Instrument, Parameter
    from wa.framework.execution import ExecutionContext
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "Workload Automation must be installed to use the topdown WA instrument. "
        "Install WA or remove this plugin from your environment."
    ) from exc


@dataclass
class _CpuUserConfig:
    """Subset of CPU probe settings exposed through the WA instrument."""

    spec_overrides: List[str] = field(default_factory=list)
    sme_overrides: List[Tuple[str, List[int]]] = field(default_factory=list)
    core_filter: Optional[List[int]] = None
    dump_events: Optional[Any] = None
    generate_csv: List[str] = field(default_factory=list)
    collect_by: CollectBy = CollectBy.METRIC
    metric_group: List[str] = field(default_factory=list)
    stages: List[int] = field(default_factory=lambda: list(DEFAULT_ALL_STAGES))


class TopdownInstrument(Instrument):
    """Instrument that configures and drives ``topdown-tool`` alongside WA workloads."""

    name: str = "topdown"
    description: str = "Runs topdown-tool with structured CPU/perf configuration and CSV export."

    _DEFAULT_CSV_SUBDIR = "topdown_output"

    parameters: List[Parameter] = [
        Parameter(
            "cpu_config",
            kind=dict,
            default=None,
            description=(
                "CPU capture config supporting spec_overrides, sme_overrides, core_filter, "
                "dump_events, collect_by, metric_group, and stages."
            ),
        ),
        Parameter(
            "perf_config",
            kind=dict,
            default=None,
            description="Optional perf overrides (perf_path, perf_args).",
        ),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._probes: Tuple[Probe, ...] = tuple()
        self._running_probes: Tuple[Probe, ...] = tuple()
        self._capture_active: bool = False
        self._cpu_factory: Optional[CpuProbeFactory] = None
        self.cpu_csv_path: str = ""

    def initialize(self, _context: ExecutionContext):
        self._reset_state()
        self._cpu_factory = None

    def start(self, context: ExecutionContext):
        """Prepare capture configuration and start topdown-tool."""

        if self._capture_active:
            self.logger.warning(
                "Previous topdown capture still active; stopping it before starting a new one."
            )
            try:
                self.stop(context)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                raise RuntimeError(
                    "Failed to stop existing topdown capture before starting a new one."
                ) from exc

        self._reset_state()
        target = getattr(self, "target", None)
        self._prepare_target(target)

        self._resolve_csv_path(context)
        perf_config = self._build_perf_config()
        self._configure_perf(perf_config)
        cpu_config = self._build_cpu_factory_config()
        should_capture = self._configure_cpu_factory(cpu_config)

        probes = self._cpu_factory.create(
            capture_data=should_capture,
            base_csv_dir=self.cpu_csv_path,
        )
        self._probes = probes

        if not self._prepare_probe_execution(probes, should_capture):
            return

        self._start_probes(self._running_probes)
        self._capture_active = True
        self.logger.info("Topdown-tool capture started for %d probe(s)", len(self._running_probes))

    def _reset_state(self) -> None:
        self._clear_probe_state()
        self._capture_active = False
        self._cpu_factory = CpuProbeFactory()

    def _prepare_target(self, target: Optional[object]) -> None:
        if getattr(target, "is_connected", True) is False:
            self.logger.warning("Target is not connected.")
        if target is not None:
            remote_target_manager.set_remote_target(target)
            self.logger.info("Target ABI: %s, Hostname: %s", target.abi, target.hostname)
        self.logger.info("Topdown instrument module path: %s", __file__)

    def _resolve_csv_path(self, context: ExecutionContext) -> str:
        csv_value = os.path.join(context.output_directory, self._DEFAULT_CSV_SUBDIR)
        base_path = os.path.abspath(csv_value)
        self.cpu_csv_path = os.path.abspath(base_path)
        os.makedirs(self.cpu_csv_path, exist_ok=True)
        return self.cpu_csv_path

    def _configure_perf(self, config: PerfFactoryConfig) -> None:
        perf_factory.configure(config)
        if not perf_factory.have_perf_privilege():
            raise RuntimeError(
                "topdown-tool requires perf_event_paranoid=-1, CAP_PERFMON, or CAP_SYS_ADMIN"
            )
        if not perf_factory.is_perf_runnable():
            raise RuntimeError(
                f"Perf tool at {perf_factory.get_effective_perf_path()} is not runnable."
            )

    def _configure_cpu_factory(self, config: CpuProbeFactoryConfig) -> bool:
        assert self._cpu_factory is not None
        return self._cpu_factory.configure(config)

    def _build_cpu_factory_config(self) -> CpuProbeFactoryConfig:
        base = self._parse_cpu_config()
        runtime_conf = CpuProbeConfiguration(
            cpu_dump_events=base.dump_events,
            cpu_generate_csv=list(base.generate_csv),
            multiplex=True,
            collect_by=base.collect_by,
            metric_group=list(base.metric_group),
            node=None,
            level=None,
            stages=list(base.stages),
        )
        core_filter = list(base.core_filter) if base.core_filter is not None else None
        return CpuProbeFactoryConfig(
            runtime=runtime_conf,
            spec_overrides=list(base.spec_overrides),
            sme_overrides=[(path, list(cores)) for path, cores in base.sme_overrides],
            core_filter=core_filter,
            list_cores=False,
            csv_output_path=self.cpu_csv_path,
            interval_ms=None,
        )

    def _build_perf_config(self) -> PerfFactoryConfig:
        config = getattr(self, "perf_config", None) or {}
        if not isinstance(config, dict):
            raise ValueError("perf_config must be a mapping of option names to values.")
        unknown_keys = set(config.keys()) - {"perf_path", "perf_args"}
        if unknown_keys:
            formatted = ", ".join(sorted(unknown_keys))
            raise ValueError(f"Unsupported perf_config option(s): {formatted}")
        return PerfFactoryConfig(
            perf_path=self._optional_string(config.get("perf_path"), "perf_config.perf_path"),
            perf_args=self._optional_string(config.get("perf_args"), "perf_config.perf_args"),
            interval=None,
        )

    def _parse_cpu_config(self) -> _CpuUserConfig:
        config = getattr(self, "cpu_config", None) or {}
        if not isinstance(config, dict):
            raise ValueError("cpu_config must be a mapping of option names to values.")

        spec_overrides = self._string_list(config.get("spec_overrides"), "spec_overrides")
        sme_overrides = self._normalize_sme_overrides(config.get("sme_overrides"))
        core_filter = self._normalize_core_filter(config.get("core_filter"))
        dump_events = config.get("dump_events")
        generate_csv = self._normalize_generate_csv(config.get("generate_csv"))
        collect_by = self._normalize_collect_by(config.get("collect_by"))
        metric_group = self._string_list(config.get("metric_group"), "metric_group")
        stages = self._normalize_stages(config.get("stages"))

        return _CpuUserConfig(
            spec_overrides=spec_overrides,
            sme_overrides=sme_overrides,
            core_filter=core_filter,
            dump_events=dump_events,
            generate_csv=generate_csv,
            collect_by=collect_by,
            metric_group=metric_group,
            stages=stages,
        )

    @staticmethod
    def _normalize_collect_by(value: Any) -> CollectBy:
        if value is None:
            return CollectBy.METRIC
        if isinstance(value, CollectBy):
            return value
        if isinstance(value, str):
            try:
                return CollectBy(value.lower())
            except ValueError as exc:
                raise ValueError(
                    'collect_by must be one of "none", "metric", or "group".'
                ) from exc
        raise ValueError("collect_by must be a CollectBy instance or string literal.")

    @staticmethod
    def _string_list(value: Optional[Any], field_name: str) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError(f"{field_name} must be a list, not a string.")
        if not isinstance(value, Sequence):
            raise ValueError(f"{field_name} must be a sequence of strings.")
        result: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError(f"{field_name} entries must be strings.")
            result.append(item)
        return result

    def _normalize_sme_overrides(self, value: Optional[Any]) -> List[Tuple[str, List[int]]]:
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError("sme_overrides must be a list of overrides, not a string.")
        if not isinstance(value, Sequence):
            raise ValueError("sme_overrides must be a sequence.")
        overrides: List[Tuple[str, List[int]]] = []
        for entry in value:
            path: Optional[str]
            cores_value: Any
            if isinstance(entry, dict):
                path = entry.get("path")
                cores_value = entry.get("cores")
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                path = entry[0]
                cores_value = entry[1]
            else:
                raise ValueError(
                    "Each sme_overrides entry must be a mapping with 'path'/'cores' or a (path, cores) pair."
                )
            if not isinstance(path, str) or not path:
                raise ValueError("sme_overrides entries must specify a non-empty path string.")
            cores = self._core_list(cores_value, "sme_overrides cores")
            overrides.append((path, cores))
        return overrides

    def _normalize_core_filter(self, value: Optional[Any]) -> Optional[List[int]]:
        if value is None:
            return None
        cores = self._core_list(value, "core_filter")
        return cores or None

    @staticmethod
    def _core_list(value: Any, field_name: str) -> List[int]:
        if isinstance(value, str):
            raise ValueError(f"{field_name} must be a list of integers, not a string.")
        if not isinstance(value, Sequence):
            raise ValueError(f"{field_name} must be a sequence of integers.")
        cores: List[int] = []
        for item in value:
            try:
                cores.append(int(item))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field_name} entries must be integers.") from exc
        return cores

    @staticmethod
    def _normalize_generate_csv(value: Optional[Any]) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raise ValueError("generate_csv must be a list of strings, not a single string.")
        if not isinstance(value, Sequence):
            raise ValueError("generate_csv must be provided as a sequence.")
        allowed = {"metrics", "events"}
        normalized: List[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("generate_csv entries must be strings.")
            entry = item.strip().lower()
            if entry not in allowed:
                raise ValueError('generate_csv entries must be "metrics" and/or "events".')
            if entry not in normalized:
                normalized.append(entry)
        return normalized

    @staticmethod
    def _normalize_stages(value: Optional[Any]) -> List[int]:
        if value is None:
            return list(DEFAULT_ALL_STAGES)
        if isinstance(value, str):
            raise ValueError("stages must be a list of integers, not a string.")
        if not isinstance(value, Sequence):
            raise ValueError("stages must be provided as a sequence of integers.")
        normalized: List[int] = []
        for item in value:
            try:
                stage = int(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("stages entries must be integers (1 or 2).") from exc
            if stage not in (1, 2):
                raise ValueError("stages entries must be 1 or 2.")
            normalized.append(stage)
        # Preserve input ordering but drop duplicates
        seen: List[int] = []
        for stage in normalized:
            if stage not in seen:
                seen.append(stage)
        return seen or list(DEFAULT_ALL_STAGES)

    @staticmethod
    def _optional_string(value: Optional[Any], field_name: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string when provided.")
        return value

    def _prepare_probe_execution(self, probes: Tuple[Probe, ...], should_capture: bool) -> bool:
        if not probes:
            if should_capture:
                self.logger.warning("No probe instances were created; skipping capture.")
            self._clear_probe_state()
            return False

        runnable = tuple(probe for probe in probes if probe.need_capture())
        self._running_probes = runnable
        if not should_capture or not runnable:
            if not should_capture:
                self.logger.info("Probe requested informational output only; no capture started.")
            for probe in probes:
                probe.output()
            self._clear_probe_state()
            return False
        return True

    def _start_probes(self, runnable: Tuple[Probe, ...]) -> None:
        started: List[Probe] = []
        try:
            for probe in runnable:
                probe.start_capture()
                started.append(probe)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.exception("Failed to start capture: %s", exc)
            self._stop_started_probes(started)
            self._clear_probe_state()
            raise

    def _stop_started_probes(self, started: List[Probe]) -> None:
        for probe in started:
            try:
                probe.stop_capture(interrupted=True)
            except Exception:  # pylint: disable=broad-exception-caught
                self.logger.exception("Failed to stop probe after start failure: %r", probe)

    def _clear_probe_state(self) -> None:
        self._probes = tuple()
        self._running_probes = tuple()

    def stop(self, _context: ExecutionContext):
        """Stop the running topdown-tool capture cleanly."""

        if not self._probes and not self._running_probes:
            return

        self.logger.info("Stopping topdown-tool capture...")

        errors = False
        if self._capture_active:
            for probe in self._running_probes:
                try:
                    probe.stop_capture()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    errors = True
                    self.logger.exception("Failed while stopping probe %r: %s", probe, exc)

        for probe in self._probes:
            try:
                probe.output()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors = True
                self.logger.exception("Failed while producing output for probe %r: %s", probe, exc)

        self._clear_probe_state()
        self._capture_active = False

        target = getattr(self, "target", None)
        if target is not None:
            try:
                remote_cleanup_target_temp_dirs(target)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                errors = True
                self.logger.exception("Failed to clean remote target directories: %s", exc)

        if errors:
            raise RuntimeError("Errors occurred while stopping topdown-tool capture. See logs.")

    def update_output(self, context: ExecutionContext):
        """
        Collect CSV outputs and register them as WA artifacts.

        CSV files are only produced when ``--cpu-generate-csv`` (or equivalent)
        is included in ``args``.
        """
        if os.path.isdir(self.cpu_csv_path):
            csv_files: List[str] = glob.glob(
                os.path.join(self.cpu_csv_path, "**", "*.csv"), recursive=True
            )
            if not csv_files:
                self.logger.warning("No CSV files found in: %s", self.cpu_csv_path)
            for csv_file in csv_files:
                relative_path = os.path.relpath(csv_file, self.cpu_csv_path)
                artifact_name = relative_path.replace(os.sep, "_").replace(".csv", "")
                context.add_artifact(
                    f"topdown-{artifact_name}",
                    csv_file,
                    kind="data",
                    classifiers={"relative_path": relative_path},
                )
        else:
            self.logger.warning("Topdown output directory not found: %s", self.cpu_csv_path)
