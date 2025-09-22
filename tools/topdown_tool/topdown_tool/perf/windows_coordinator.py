# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Windows `wperf` run coordinator.

This module coordinates a *single* ``wperf stat`` process that serves multiple
WindowsPerf instances concurrently. `WindowsPerf` instances register themselves;
the coordinator deduplicates their requested event groups per core, launches
one combined ``wperf`` run, and fans out parsed results back to each perf instance
in the Linux-parity shape (group-aligned tuples).

The coordinator is a process-wide singleton. Minimal per-perf instance lifecycle state
(``active``, ``started``, ``stopped``) is tracked in-memory, while perf instances
specific details (cores, event groups, callback) are read from the perf
instances when needed.

Platform notes:
  Ctrl-C broadcast helpers and signal masking are Windows-specific. Portable
  stubs are provided so importing on non-Windows platforms is safe; calling the
  Windows APIs on POSIX raises ``NotImplementedError``.

Key responsibilities:
  * Manage perf instances registration and lifecycle.
  * Build a combined ``wperf stat --json`` command (per-core grouped events).
  * Start and gracefully finalize a single shared ``wperf`` process.
  * Parse JSON output and filter results per perf instance registration.

"""
import logging
from pathlib import Path
import os
import time
from subprocess import Popen, TimeoutExpired, list2cmdline
from typing import Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING
from dataclasses import dataclass

from collections import defaultdict
from topdown_tool.perf.perf import (
    Cpu,
    PerfRecords,
    PerfTimedResults,
    PerfResults,
    PerfEvent,
    Uncore,
)
from topdown_tool.perf.windows_perf_parser import (
    parse_windows_perf_json,
    ParsedCounters,
)

from topdown_tool.common.win32 import (
    send_console_ctrl_c,
    ignore_sigint_temporarily,
    swallow_keyboard_interrupt,
)
from topdown_tool.perf.wperf_artifact_handler import (
    cleanup_run_artifacts_for_output_windows,
    is_json_stable,
    wait_for_json_stable,
)

if TYPE_CHECKING:
    from topdown_tool.perf.windows_perf import WindowsPerf


class WperfCoordinator:
    """Coordinate one shared ``wperf stat`` process for multiple WindowsPerf instances.

    Perf instances are concrete ``WindowsPerf`` instances. The coordinator reads
    per-instance configuration (cores, event groups, callback) from the instances,
    launches a single combined ``wperf`` run that covers all active instances, and
    then filters parsed results back to each perf instance.

    Thread Safety:
      Waiting on the underlying process is coordinated via an internal
      ``threading.Event`` that is set when a process exists and safe to wait on.

    Attributes:
      _instance: Process-wide singleton (or ``None`` if uninitialized).
      _global_perf_path: Global path/name of the ``wperf`` binary used by
        the singleton and all perf instances unless overridden at construction time.
      _registered: Mapping of ``WindowsPerf`` -> minimal ``WindowsPerfInstanceState``.
      _run_started: Whether a combined run has been launched.
      _capture_finalized: Whether the current run has been finalized.
      perf_path: Concrete ``wperf`` program path used by this instance.
      _output_file: Output JSON path for the current run (unique per run).
      _wperf_process: Handle to the running ``wperf`` process.
      _run_seq: Monotonic per-process run counter.
      _interval: Sample interval (ms) adopted from the first registering perf instance.
    """

    _instance = None
    # Single source of truth for the wperf binary path
    _global_perf_path: str = "wperf"

    @dataclass
    class WindowsPerfInstanceState:
        active: bool = True
        started: bool = False
        stopped: bool = False

    def __init__(self, perf_path: Optional[str], output_file: Optional[Path]):
        """Initialize a coordinator instance.

        Args:
            perf_path (Optional[str]): Path or program name of the ``wperf``
                binary. If ``None``, defaults to ``"wperf"``.
            output_file (Optional[Path]): Initial output path. Replaced with a
                unique per-run path when a combined run is launched.
        """
        self._registered: Dict["WindowsPerf", WperfCoordinator.WindowsPerfInstanceState] = {}
        self._run_started = False
        self._capture_finalized = False
        self.perf_path = perf_path or type(self)._global_perf_path
        self._output_file = output_file
        self._wperf_process: Optional[Popen] = None
        self._run_seq = 0
        self._interval: Optional[int] = None  # milliseconds, applied to the shared run

    @classmethod
    def get_instance(
        cls,
        perf_path: Optional[str] = None,
        output_file: Optional[Path] = Path("dummy.json"),
    ) -> "WperfCoordinator":
        """Return (and lazily create) the process-wide singleton.

        The first call constructs the instance; subsequent calls return the same
        object. If ``perf_path`` is omitted, the class-wide ``_global_perf_path``
        is used.

        Args:
        perf_path: Optional path/program name of the ``wperf`` binary to seed the
            singleton's ``perf_path``. If omitted, uses ``_global_perf_path``.
        output_file: Initial output path; replaced with a unique per-run path on
            launch.

        Returns:
        WperfCoordinator: The singleton instance.
        """
        if cls._instance is None:
            # If no perf_path provided, use the global one
            cls._instance = cls(perf_path or cls._global_perf_path, output_file)

        return cls._instance

    # ---- Global wperf path management ------------------------------------
    @classmethod
    def set_perf_path(cls, perf_path: str) -> None:
        """Set the global path to the ``wperf`` binary.

        Updates the class-wide path used for future singletons and also updates the
        live singleton (if it already exists).

        Args:
        perf_path: Absolute or relative path/name for the ``wperf`` binary.
        """
        cls._global_perf_path = perf_path
        # Keep the live instance in sync
        if cls._instance is not None:
            cls._instance.perf_path = perf_path

    @classmethod
    def get_perf_path(cls) -> str:
        """Return the global `wperf` binary path.

        This is the single source of truth for the executable used by both
        :class:`WindowsPerf` and the coordinator. It may be changed
        with :meth:`set_perf_path`.
        """
        return cls._global_perf_path

    # pylint: disable=too-many-arguments, too-many-positional-arguments, protected-access
    def register(
        self,
        perf_instance: "WindowsPerf",
    ) -> None:
        """Register a WindowsPerf instance for participation in the next combined run.

        Registration is additive and idempotent for a given instance. The first
        non-``None`` interval observed across instances is adopted for the shared
        run; later mismatches are logged and ignored.

        Args:
            windows_perf_instance: Instance to register.
        """
        # Adopt the first non-None interval we see; warn on later mismatches
        if perf_instance.get_interval() is not None:
            if self._interval is None:
                self._interval = perf_instance.get_interval()
            elif self._interval != perf_instance.get_interval():
                logging.warning(
                    "Ignoring interval mismatch from WindowsPerf instances %s: already set to %sms, got %sms",
                    perf_instance,
                    self._interval,
                    perf_instance.get_interval(),
                )

        # Insert or reset the minimal per-instance state
        self._registered[perf_instance] = WperfCoordinator.WindowsPerfInstanceState()

    def deactivate(self, windows_perf_instance: "WindowsPerf") -> None:
        """Mark a registered instance as inactive.

        Inactive instances do not block run start/stop decisions and will not
        receive results during finalization.

        Args:
            windows_perf_instance: Instance to deactivate.
        """
        state = self._registered.get(windows_perf_instance)
        if state:
            state.active = False

    def unregister(self, windows_perf_instance: "WindowsPerf") -> None:
        """Remove an instance from the coordinator.

        Safe to call at any time; unknown instances are ignored.

        Args:
            windows_perf_instance: Instance to remove.
        """
        self._registered.pop(windows_perf_instance, None)

    # pylint: disable=unused-argument
    def start(self, windows_perf_instance: "WindowsPerf", pid: Optional[int] = None) -> None:
        """Mark an instance as ready; launch the combined run when all are ready.

        When all *active* instances have called ``start()``, the coordinator
        launches a single ``wperf`` process covering their union of cores and
        deduplicated events.

        Args:
            windows_perf_instance: Instances calling ``start()``.
        """
        # FIXME: Should consider pid

        should_launch = False

        if windows_perf_instance not in self._registered:
            return

        logging.debug(
            "Instance %s starting: %s",
            windows_perf_instance,
            self._registered[windows_perf_instance].started,
        )
        self._registered[windows_perf_instance].started = True

        if (not self._run_started) and all(
            (not st.active) or st.started for st in self._registered.values()
        ):
            self._run_started = True
            should_launch = True

        if should_launch:
            self._launch_combined_wperf()
            logging.debug("started combined wperf")

    def stop(self, windows_perf_instance: "WindowsPerf") -> None:
        """Mark an instance as stopped; first stop triggers finalization.

        The first ``stop()`` after a run has started triggers graceful shutdown
        of ``wperf``, parsing of the JSON output, dispatch of filtered results
        to all active instances, and cleanup. Subsequent calls are recorded but do
        not re-finalize.

        Args:
            windows_perf_instance: Instances calling ``stop()``.
        """
        should_finalize = False

        if windows_perf_instance not in self._registered:
            logging.debug(
                "Ignoring stop() from unregistered instances %s",
                windows_perf_instance,
            )
            return

        prev = self._registered[windows_perf_instance].stopped
        self._registered[windows_perf_instance].stopped = True
        logging.debug(
            "Instances %s stopping (was_stopped=%s). Current stop states: %s",
            windows_perf_instance,
            prev,
            {str(k): v.stopped for k, v in self._registered.items()},
        )

        # Finalize as soon as the FIRST stop() arrives after the run has started.
        if self._run_started and not self._capture_finalized:
            self._capture_finalized = True
            should_finalize = True

        if should_finalize:
            logging.debug(
                "finalizing combined wperf (triggered by instances %s)",
                windows_perf_instance,
            )
            for _, pst in self._registered.items():
                if pst.active:
                    pst.stopped = True
            self._finalize_capture()

    # pylint: disable=too-many-branches
    def _launch_combined_wperf(self) -> None:
        """Launch a single ``wperf stat --json`` for all active instances.

        Builds per-core grouped event expressions from registered instances,
        deduplicating names while preserving group boundaries. Writes the long
        ``-e @<file>`` argument list and the exact CLI to disk, chooses a
        unique output JSON path, spawns the process, and signals waiting
        threads that a process is running.
        """

        # Build per-core group lists from the instances that target those cores
        per_core_groups: Dict[int, List[str]] = defaultdict(list)

        for perf_inst, st in self._registered.items():
            if not st.active:
                continue
            # Pull per-instance event groups from the instance.
            events_groups = perf_inst.get_events_groups()
            if not events_groups:
                continue
            # NOTE: if an instance registers no cores (meaning “all cores”), we may
            # want to fan these groups to `all_cores`. Here we assume instances
            # enumerate their cores explicitly.
            for core in list(perf_inst.get_cores() or []):
                for group in events_groups:
                    names = [ev.perf_name() for ev in group if ev is not None]
                    if names:
                        per_core_groups[core].append("{" + ",".join(names) + "}")

        if not per_core_groups:
            logging.warning("No event groups to launch; skipping wperf run")
            return

        # Compose the core-targeted wperf expression:
        #   core_<id>/{g1},{g2}/ segments joined by commas
        events_text = ",".join(
            f"core_{core}/" + ",".join(groups) + "/"
            for core, groups in sorted(per_core_groups.items())
        )

        # Unique run id and files
        self._run_seq += 1
        run_id = f"{int(time.time() * 1000)}-{os.getpid()}-{self._run_seq}"
        self._output_file = Path(f"wperf-{run_id}.json")
        cmdfile = Path(f"wperf-{run_id}.cmdline")  # holds the long -e @file events list
        clifile = Path(f"wperf-{run_id}.cli.txt")

        # Write long event list to file so we can use -e @file
        # Keep it as a single line with comma separation (what wperf expects).
        with open(cmdfile, "w", encoding="utf-8", newline="\n") as f:
            f.write(events_text)

        cmd = [
            self.perf_path,
            "stat",
            "--json",
            "-o",
            str(self._output_file),
            "-e",
            f"@{cmdfile}",
        ]

        # Interval (ms) - default to "infinite" if not set
        if self._interval is not None:
            cmd.extend(["-I", str(self._interval)])
        else:
            cmd.extend(["-I", "365d"])  # effectively "infinite" until stop()

        logging.info(
            "Running coordinated wperf:\n%s\n  with events: %s\n  (cli: %s)",
            " ".join(map(str, cmd)),
            events_text,
            f"wperf-{run_id}.cli.txt",
        )
        # Write the *exact* CLI we are about to execute (useful for repro)
        try:
            with open(clifile, "w", encoding="utf-8", newline="\n") as f:
                f.write(list2cmdline(cmd))
        except Exception:  # pylint: disable=broad-except
            logging.debug("Failed to write CLI file %s", clifile, exc_info=True)

        self._wperf_process = Popen(cmd)  # pylint: disable=consider-using-with

    # -- helper --------------------------------------------------------------
    def _shutdown_wperf_until_json_or_exit(self, proc: Popen, out_path: str) -> None:
        """Gracefully stop wperf, waiting for exit or for the JSON to appear.

        Tries:
          1) brief natural exit;
          2) broadcast CTRL_C and poll up to ~30s
             (either process exits or JSON file appears);
          3) terminate() → kill() as last resort if no JSON exists.

        This function does not block for the JSON to become *stable*; the caller
        may still run a separate “stable size” loop afterwards.
        """

        # phase 1 – hope wperf stops on its own
        try:
            logging.debug("Waiting up to 1 s for wperf to exit naturally")
            with swallow_keyboard_interrupt():
                proc.wait(timeout=1)
        except TimeoutExpired:
            # phase 2 – send Ctrl-C; ignore local SIGINT while broadcasting
            with ignore_sigint_temporarily():
                logging.info("wperf still running - sending CTRL_C_EVENT")
                try:
                    send_console_ctrl_c()
                except Exception as exc:  # pylint: disable=broad-except
                    logging.error("send_signal failed: %s", exc)

                deadline = time.time() + 30.0
                while time.time() < deadline:
                    if proc.poll() is not None:  # process exited
                        break
                    if is_json_stable(Path(out_path)):  # JSON flushed and settled
                        logging.debug("JSON file detected while wperf alive")
                        break
                    time.sleep(0.2)

            # phase 3 – last resort, only if no JSON and still alive
            if proc.poll() is None and not os.path.isfile(out_path):
                logging.warning("wperf ignored CTRL_C_EVENT - terminating")
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except TimeoutExpired:
                    logging.warning("terminate() failed - killing")
                    try:
                        proc.kill()
                    except Exception as exc:  # pylint: disable=broad-except
                        logging.error("Final kill failed: %s", exc)

    # pylint: disable=too-many-branches, too-many-locals, too-many-statements
    def _finalize_capture(self) -> None:
        """Finalize the run, parse JSON, and dispatch results.

        Procedure:
          1. Attempt graceful shutdown of ``wperf``: wait briefly, otherwise
             broadcast Ctrl-C; escalate to terminate/kill if necessary.
          2. Wait (≤30s) for the JSON file to appear and become size-stable.
          3. Parse counters via :func:`parse_windows_perf_json`.
          4. Filter and set results to each active instance.
          5. Clean up temporary artifacts and reset state for the next run.

        Exceptions from individual callbacks are logged and do not abort
        dispatch to other perf instances.
        """
        assert self._wperf_process is not None
        out_path = str(self._output_file)
        proc = self._wperf_process

        logging.info("Finalizing wperf (pid %s), output path: %s", proc.pid, out_path)

        # ------------------------------------------------------------------ 1
        # Attempt graceful shutdown; wait for either process exit or initial JSON
        self._shutdown_wperf_until_json_or_exit(proc, out_path)

        # ------------------------------------------------------------------ 2
        # Wait until the JSON file is present and stable (<= 30 s)
        with swallow_keyboard_interrupt():
            wait_for_json_stable(Path(out_path), timeout=30.0, interval=0.2)

        # ------------------------------------------------------------------ 3
        # CPU / regular uncore counters
        try:
            cpu_records: ParsedCounters = parse_windows_perf_json(out_path)

            logging.debug("CPU/uncore JSON parsed successfully")
        except Exception as exc:  # pylint: disable=broad-except
            logging.error("Could not parse CPU part of wperf output (%s): %s", out_path, exc)
            cpu_records = {}

        # ------------------------------------------------------------------ 4
        # deliver the final result to every perf instance
        for perf_instance, st in self._registered.items():
            if not st.active:
                continue
            try:
                cores = list(perf_instance.get_cores() or [])
                events_groups = perf_instance.get_events_groups()
                filtered = self._filter_instance_results(cpu_records, cores, events_groups)
                perf_instance.set_results(filtered)

            except Exception:  # pylint: disable=broad-except
                logging.exception(
                    "Failed to dispatch result to perf instances %s",
                    perf_instance,
                )

        # ------------------------------------------------------------------ 5
        # tidy up artifacts
        cleanup_run_artifacts_for_output_windows(Path(out_path))

        # reset coordinator state for the next run
        self._wperf_process = None
        for st in self._registered.values():
            st.started = False
            st.stopped = False
        self._run_started = False
        self._capture_finalized = False

    def _filter_instance_results(
        self,
        records: ParsedCounters,  # Dict[Optional[float], Dict[int, List[Tuple[str, Optional[str], float]]]]
        cores: Sequence[int],
        events: Sequence[Tuple[PerfEvent, ...]],
    ) -> PerfRecords:
        """Filter combined counters down to one instance's registration.

        Returns group-ordered tuples keyed by timestamp and core.

        Args:
            records: Parsed counters keyed by ``timestamp → core_id → [(token, note, value)]``.
            cores: Core filter; empty means “all cores”.
            events: Event groups in instance order.

        Returns:
            PerfRecords: Results limited to the requested cores and groups.
        """
        requested_groups: List[Tuple[PerfEvent, ...]] = [tuple(g) for g in (events or [])]
        out = PerfRecords({})
        core_allow = set(cores) if cores else None
        if not requested_groups:
            return out

        # records: timestamp -> core_id -> [(event_idx, event_note, value), ...]
        for ts, core_map in records.items():
            ts = ts if self._interval else None
            for core_id, core_records in core_map.items():
                if core_id >= 0 and core_allow is not None and core_id not in core_allow:
                    continue

                loc = Uncore() if core_id == -1 else Cpu(core_id)
                out.setdefault(loc, PerfTimedResults())
                out[loc].setdefault(ts, PerfResults())

                idx = 0
                for g in events:
                    # in case of partial results set None to the values
                    if idx + len(g) > len(core_records):
                        out[loc][ts][g] = tuple(None for _ in g)
                    else:
                        group_records = core_records[idx : idx + len(g)]
                        out[loc][ts][g] = tuple(e[2] for e in group_records)
                    idx += len(g)

        return out

    def cleanup(self) -> None:
        """Reset the coordinator to an empty state.

        Clears all registrations and per-run state, and drops the singleton so
        the next :meth:`get_instance` call returns a fresh object.
        """
        self._registered.clear()
        self._run_started = False
        self._wperf_process = None
        self._capture_finalized = False
        WperfCoordinator._instance = None
