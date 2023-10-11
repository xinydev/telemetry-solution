# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import dataclasses
import json
import re
import sys
from typing import Iterable, Optional

import mrs_data

MNEMONICS_FILENAME = "categories.json"


class EventGrouper():
    def get_groups(self, event_name, source_file_name):
        pass


class ArmDataEventGrouper(EventGrouper):
    def __init__(self, path_to_group_data=MNEMONICS_FILENAME):
        with open(path_to_group_data, 'r') as f:
            group_data = json.load(f)

        self.mnemonic_matches = group_data["events"]
        self.regex = [(re.compile(r), g) for r, g in group_data["regex"]]

    def regex_group(self, event_name):
        for r, g in self.regex:
            if r.fullmatch(event_name):
                return g

    def get_groups(self, event_name, source_file_name):
        specific_group = self.mnemonic_matches.get(event_name)
        regex_group = self.regex_group(event_name)

        # If group is defined by specific value and regex, ensure they match
        assert not specific_group or not regex_group or specific_group == regex_group, \
               f"Conflicting groups for {event_name}: {specific_group} != {regex_group}"

        if regex_group and specific_group:
            print(f"{event_name} matches a regular expression and has a specific group entry",
                  file=sys.stderr)

        assert regex_group or specific_group, \
            f"{event_name} does not match any category, please update {MNEMONICS_FILENAME}"

        return [specific_group or regex_group]


class TelemetryEventGrouper(EventGrouper):
    def get_groups(self, event_name, source_file_name):
        def perf_name(group_name):
            return group_name.lower().replace(" ", "-")

        group_list = mrs_data.read_telemetry_function_groups(source_file_name)
        return [perf_name(g) for g in group_list if event_name in group_list[g]["events"]]


def add_categories(events: Iterable, event_grouper: Optional[EventGrouper], source_file_name: str):
    def add_topic(event):
        """Create new event dict with topics added"""
        topics = event_grouper.get_groups(event.EventName, source_file_name)
        return dataclasses.replace(event, Topics=topics)

    # Add core-imp-def topic if no grouping requested
    if not event_grouper:
        return [dataclasses.replace(e, Topics=["core-imp-def"]) for e in events]

    output = [add_topic(event) for event in events]

    events_with_no_group = [f'{e.EventName}: {e.BriefDescription}' for e in output if not e.Topics]
    if events_with_no_group:
        print('Warning: Not writing events which do not have a group (note that the mnemonic '
              'printed may differ to the one in the source json if it differs in the common Perf '
              'files):\n  %s' % "\n  ".join(events_with_no_group))
        return [dataclasses.replace(e, Topics=["core-imp-def"]) for e in events]

    return output
