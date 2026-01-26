# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

# pylint: disable=duplicate-code

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from topdown_tool.cpu_probe.cpu_telemetry_database import Event
from topdown_tool.perf.perf import (
    Cpu,
    PerfRecords,
    PerfResults,
    PerfTimedResults,
    Uncore,
)
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.perf.windows_perf import WindowsPerfParser as PerfParser

# A single snapshot of counters at a given timestamp:
#   key   = core id (int), with -1 reserved for systemwide overall counters.
#   value = list of (event_idx_token, event_note, value)
CounterSnapshot = Dict[int, List[Tuple[str, Optional[str], Optional[float]]]]

# All snapshots keyed by timestamp in seconds (float). If there is no explicit
# timeline, the single snapshot is stored under the key None.
ParsedCounters = Dict[Optional[float], CounterSnapshot]


class WindowsPerfParser(PerfParser):
    """
    Parser for CPU for Windows Perf
    1. Prepares command line for Windows Perf
    2. Parses Windows Perf JSON output
    """
    def __init__(self, perf_groups: Sequence[Sequence[Event]], perf_instance: WindowsPerf):
        self.perf_instance = perf_instance
        self.perf_groups = tuple(tuple(perf_group) for perf_group in perf_groups)
        self.cmdfile: Optional[Path] = None

    def prepare_perf_command_line(self, run_id: str) -> Tuple[Optional[Path], Tuple[str, ...]]:
        """Prepares a command line for Windows Perf

        Returns:
            Tuple[str, Tuple[str, ...]]: command line for Windows Perf
        """
        # holds the long -e @file events list
        self.cmdfile = Path(f"wperf-{run_id}-cpu.cmdline")

        # Build per-core group lists from the instances that target those cores
        per_core_groups: Dict[int, List[str]] = defaultdict(list)

        # NOTE: if an instance registers no cores (meaning “all cores”), we may
        # want to fan these groups to `all_cores`. Here we assume instances
        # enumerate their cores explicitly.
        for core in list(self.perf_instance.get_cores() or []):
            for group in self.perf_groups:
                names = [ev.perf_name() for ev in group if ev is not None]
                if names:
                    per_core_groups[core].append("{" + ",".join(names) + "}")
        if not per_core_groups:
            logging.warning("No event groups to launch; skipping wperf run")
            return None, ()
        # Compose the core-targeted wperf expression:
        #   core_<id>/{g1},{g2}/ segments joined by commas
        events_text = ",".join(
            f"core_{core}/" + ",".join(groups) + "/"
            for core, groups in sorted(per_core_groups.items())
        )

        # Write long event list to file so we can use -e @file
        # Keep it as a single line with comma separation (what wperf expects).
        with open(self.cmdfile, "w", encoding="utf-8", newline="\n") as f:
            f.write(events_text)

        return self.cmdfile, ()

    def before_capture(self) -> Tuple[Tuple[Event, ...], ...]:
        """Return perf groups (this function is no-op for CPU probe)

        Returns:
            Tuple[CmnImmutablePerfGroup, ...]: perf groups
        """
        return self.perf_groups

    # pylint: disable=too-many-locals, too-many-nested-blocks, too-many-branches
    def parse_perf_data(self, data: dict) -> PerfRecords:
        """Parse Windows Perf JSON output with events values and set results

        Args:
            data (dict): events values JSON loaded into a dict
        """
        def _parse_block(
            block: Dict[str, Any]
        ) -> Dict[int, List[Tuple[str, Optional[str], Optional[float]]]]:
            out: Dict[int, List[Tuple[str, Optional[str], Optional[float]]]] = {}

            core_obj = block.get("core", {})
            for core_data in core_obj.get("cores", []):
                core_id = int(core_data["core_number"])
                counters_in = core_data.get("Performance_counter", [])
                counters_out: List[Tuple[str, Optional[str], Optional[float]]] = []
                for item in counters_in:
                    event_idx = item.get("event_idx", "")
                    if str(event_idx).lower() == "fixed":
                        continue
                    note = item.get("event_note")
                    value = None
                    try:
                        if "scaled_value" in item:
                            value = float(item["scaled_value"])
                        else:
                            value = float(item["counter_value"])
                    except (TypeError, ValueError):
                        pass
                    counters_out.append((event_idx, note, value))
                if counters_out:
                    out[core_id] = counters_out

            return out

        timeline = data.get("timeline")
        if isinstance(timeline, list) and timeline:
            # timeline format
            records: Dict[
                Optional[float], Dict[int, List[Tuple[str, Optional[str], Optional[float]]]]
            ] = {}
            t_accum = 0.0
            for entry in timeline:
                elapsed = entry.get("Time_elapsed")
                t_accum += float(elapsed) if elapsed is not None else 0.0
                records[t_accum] = _parse_block(entry)
        else:
            # no-timeline
            records = {None: _parse_block(data)}

        cores = list(self.perf_instance.get_cores() or [])

        out = PerfRecords({})

        # records: timestamp -> core_id -> [(event_idx, event_note, value), ...]
        for ts, core_map in records.items():
            ts = ts if self.perf_instance.get_interval() is not None else None
            for core_id, core_records in core_map.items():
                if core_id >= 0 and len(cores) > 0 and core_id not in cores:
                    continue

                loc = Uncore() if core_id == -1 else Cpu(core_id)
                out.setdefault(loc, PerfTimedResults())
                out[loc].setdefault(ts, PerfResults())

                idx = 0
                for g in self.perf_groups:
                    # in case of partial results set None to the values
                    if idx + len(g) > len(core_records):
                        out[loc][ts][g] = tuple(None for _ in g)
                    else:
                        group_records = core_records[idx : idx + len(g)]
                        out[loc][ts][g] = tuple(e[2] for e in group_records)
                    idx += len(g)

        return out
