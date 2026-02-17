# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=duplicate-code

"""
Helper class for parsing CMN event values output from Windows Perf. Windows Perf doesn't use unified
format of events and output format differs between CPU and CMN, necessitating use of separate parser
for CMN.
"""

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, TypedDict, Union

from topdown_tool.cmn_probe.common import Event, Watchpoint
from topdown_tool.perf.perf import PerfRecords, PerfResults, PerfTimedResults, Uncore
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.perf.windows_perf import WindowsPerfParser as PerfParser

CmnEventLike = Union[Event, Watchpoint]
CmnPerfGroup = Sequence[CmnEventLike]
CmnImmutablePerfGroup = Tuple[CmnEventLike, ...]


class WindowsPerfParser(PerfParser):
    """
    Parser for CMN for Windows Perf
    1. Eliminates dtc_cycles event from perf groups (Windows Perf always provides dtc_cycles for
    free in each output, explicit use of this event in the command line is forbidden)
    2. Prepares command line for Windows Perf
    3. Parses Windows Perf JSON output
    """

    # pylint: disable=missing-class-docstring
    class CmnCyclesRestoreInformation(TypedDict):
        event_index: int
        cmn_index: int

    def __init__(self, perf_groups: Sequence[CmnPerfGroup], perf_instance: WindowsPerf):
        self.perf_instance = perf_instance
        """ Copy perf_groups as otherwise we would assign reference to which we have no guarantee of
        immutability """
        self.original_perf_groups: Tuple[CmnImmutablePerfGroup, ...] = tuple(
            tuple(perf_group) for perf_group in perf_groups
        )
        self.cmn_cycles_positions: Dict[
            int, List[WindowsPerfParser.CmnCyclesRestoreInformation]
        ] = {}

        modified_perf_groups: List[CmnImmutablePerfGroup] = []
        for group_index, perf_group in enumerate(self.original_perf_groups):
            modified_perf_group: List[CmnEventLike] = []
            for event_index, event in enumerate(perf_group):
                if event.name == "SYS_CMN_CYCLES":
                    self.cmn_cycles_positions.setdefault(group_index, []).append(
                        {
                            "event_index": event_index,
                            "cmn_index": event.cmn_index,
                        }
                    )
                else:
                    modified_perf_group.append(event)
            modified_perf_groups.append(tuple(modified_perf_group))
        self.modified_perf_groups: Tuple[CmnImmutablePerfGroup, ...] = tuple(modified_perf_groups)
        self.cmdfile: Optional[Path] = None

    def prepare_perf_command_line(self, run_id: str) -> Tuple[Optional[Path], Tuple[str, ...]]:
        """Prepares a command line for Windows Perf

        Returns:
            Tuple[str, Tuple[str, ...]]: command line for Windows Perf
        """
        # holds the long -e @file events list
        self.cmdfile = Path(f"wperf-{run_id}-cmn.cmdline")

        with open(self.cmdfile, "w", encoding="utf-8") as event_file:
            first_group = True
            for perf_group in self.modified_perf_groups:
                if len(perf_group) == 0:
                    continue
                if not first_group:
                    event_file.write(",")
                if len(perf_group) >= 2:
                    event_file.write("{")
                event_file.write(",".join(event.perf_name() for event in perf_group))
                if len(perf_group) >= 2:
                    event_file.write("}")
                if first_group:
                    first_group = False
        return self.cmdfile, ("--enable-dpc-overflow",)

    def before_capture(self) -> Tuple[CmnImmutablePerfGroup, ...]:
        """Remove dtc_cycles from perf groups and return modified groups

        Returns:
            Tuple[CmnImmutablePerfGroup, ...]: perf groups without dtc_cycles event
        """
        return self.modified_perf_groups

    # pylint: disable=too-many-locals, too-many-nested-blocks, too-many-branches, line-too-long, invalid-name, too-many-statements
    def parse_perf_data(self, data: dict) -> PerfRecords:
        """Parse Windows Perf JSON output with events values and set results

        Args:
            data (dict): events values JSON loaded into a dict
        """
        records = PerfRecords()

        if "counting" in data:
            cmn_data = data["counting"]
        elif "timeline" in data:
            cmn_data = data["timeline"][0]
        else:
            return records

        if "mesh" not in cmn_data[0]:
            return records

        cycles_values: Dict[int, Dict[int, int]] = {}  # Key: CMN Index, DTC domain
        dtc_mapping: Dict[int, Dict[str, int]] = {}  # Key: CMN Index, Event string
        default_cycles_value: int
        dtcs: Set[int]

        records[Uncore()] = PerfTimedResults()
        records[Uncore()][None] = PerfResults()

        # Pre-fill to keep registration order
        for perf_group in self.original_perf_groups:
            records[Uncore()][None][tuple(perf_group)] = ()

        # Cycles come from the CMN_DTC section
        for single_cmn in cmn_data:
            cmn_index = single_cmn["mesh"]
            # Cycles values for DTCs
            cycles_values[cmn_index] = {}
            for ev in single_cmn["CMN_DTC"]:
                if ev["event"] == "cycles" and ev["DTC_domain"] not in cycles_values[cmn_index]:
                    if "scaled_value" in ev:
                        cycles_values[cmn_index][ev["DTC_domain"]] = ev["scaled_value"]
                    elif "value" in ev:
                        cycles_values[cmn_index][ev["DTC_domain"]] = ev["value"]
            default_cycles_value = cycles_values[cmn_index][min(cycles_values[cmn_index])]
            # Remaining DTCs & mapping between event and DTC
            dtcs = set()
            dtc_mapping[cmn_index] = {}
            for ev in single_cmn["CMN_DTC"]:
                dtcs.add(ev["DTC_domain"])
                if ev["event"] not in dtc_mapping[cmn_index]:
                    dtc_mapping[cmn_index][ev["event"]] = ev["DTC_domain"]
            for dtc in dtcs:
                if dtc not in cycles_values[cmn_index]:
                    cycles_values[cmn_index][dtc] = default_cycles_value

        # Regular CMN events
        for single_cmn in cmn_data:
            cmn_index = single_cmn["mesh"]
            output_index = 0
            for perf_group in self.original_perf_groups:
                new_group = True
                results_for_group = []
                for event in perf_group:
                    if event.cmn_index != cmn_index:
                        continue
                    if event.name == "SYS_CMN_CYCLES":
                        cycles_value = cycles_values[cmn_index][
                            dtc_mapping[cmn_index][
                                single_cmn["CMN"][
                                    output_index + 1
                                    if new_group and output_index + 1 < len(single_cmn["CMN"])
                                    else output_index
                                ]["event"]
                            ]
                        ]
                        results_for_group.append(float(cycles_value))
                    else:
                        cmn_event = single_cmn["CMN"][output_index]
                        if "scaled_value" in cmn_event and cmn_event["scaled_value"] is not None:
                            results_for_group.append(float(cmn_event["scaled_value"]))
                        elif "value" in cmn_event and cmn_event["value"] is not None:
                            results_for_group.append(float(cmn_event["value"]))
                        else:
                            raise ValueError(
                                'Encountered event entry in Windows Perf output with neither "scaled_value" nor "value" set'
                            )
                        output_index += 1
                        new_group = False
                if len(records[Uncore()][None][tuple(perf_group)]) == 0 and results_for_group:
                    records[Uncore()][None][tuple(perf_group)] = tuple(results_for_group)

        return records
