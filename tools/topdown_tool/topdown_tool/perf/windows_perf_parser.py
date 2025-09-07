# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import json
from typing import Dict, List, Tuple, Mapping, Any, Optional

# A single snapshot of counters at a given timestamp:
#   key   = core id (int), with -1 reserved for systemwide overall counters.
#   value = list of (event_idx_token, event_note, value)
CounterSnapshot = Dict[int, List[Tuple[str, Optional[str], Optional[float]]]]

# All snapshots keyed by timestamp in seconds (float). If there is no explicit
# timeline, the single snapshot is stored under the key None.
ParsedCounters = Dict[Optional[float], CounterSnapshot]


def _pick_value(item: Mapping[str, Any]) -> Any:
    v = item.get("scaled_value", None)
    if v is None:
        v = item.get("counter_value", None)
    return v


def parse_windows_perf_json(filename: str) -> ParsedCounters:
    """
    Lightweight parse of wperf JSON output with optional timeline support.
    Preserves event_note (e.g., 'g0', 'g1') so group selection can disambiguate
    duplicate event_idx tokens across groups.
    """
    with open(filename, "r", encoding="utf-8") as f:
        data = json.load(f)

    def _to_float(val: Any) -> Optional[float]:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _parse_block(block: Dict[str, Any]) -> CounterSnapshot:
        out: CounterSnapshot = {}

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
                value = _to_float(_pick_value(item))
                counters_out.append((event_idx, note, value))
            if counters_out:
                out[core_id] = counters_out

        return out

    #  timeline format
    timeline = data.get("timeline")
    if isinstance(timeline, list) and timeline:
        out: ParsedCounters = {}
        t_accum = 0.0
        for entry in timeline:
            elapsed = entry.get("Time_elapsed")
            t_accum += float(elapsed) if elapsed is not None else 0.0
            out[t_accum] = _parse_block(entry)
        return out

    # no-timeline
    return {None: _parse_block(data)}
