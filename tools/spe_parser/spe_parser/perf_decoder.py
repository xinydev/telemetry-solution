# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023
#
# This module is used to parse binary files generated
# by `perf record` and obtain the offset and size of
# SPE records stored in perf.data.
# Some reference links:
#   https://github.com/torvalds/linux/blob/master/tools/perf/Documentation/perf.data-file-format.txt

import logging
from typing import Dict, List

from construct import (
    Aligned,
    BitStruct,
    CString,
    Enum,
    Flag,
    GreedyRange,
    If,
    Int16un,
    Int32un,
    Int64un,
    Padding,
    Pointer,
    PrefixedArray,
    Struct,
    Switch,
    Tell,
)

# Construct(https://github.com/construct/construct) is a powerful declarative
# and symmetrical parser and builder for binary data.
# We followed the documentation of the perf.data format and used Construct
# to describe the corresponding data format. This allowed us to directly
# parse perf.data and obtain Pythonic objects

perf_file_section = Struct(
    "offset" / Int64un,
    "size" / Int64un,
)

perf_header_string = Struct(
    "offset" / Int64un,
    "size" / Int64un,
    "pointer"
    / Pointer(
        lambda this: this.offset,
        "str"
        / Struct(
            "len" / Int32un,
            "str" / CString("utf8"),
        ),
    ),
)

perf_header_string_list = Struct(
    "offset" / Int64un,
    "size" / Int64un,
    "pointer"
    / Pointer(
        lambda this: this.offset,
        "strlist"
        / PrefixedArray(
            Int32un,  # uint32_t nr
            Struct(
                "len" / Int32un,
                "str" / Aligned(lambda this: this.len, CString("utf8")),
            ),
        ),
    ),
)

# flags bits. A 256-bit bitmap has been set to indicate the optional features
# that are included in the current perf.data file
feature_flags = BitStruct(
    "HEADER_NRCPUS" / Flag,
    "HEADER_ARCH" / Flag,
    "HEADER_VERSION" / Flag,  # perf version
    "HEADER_OSRELEASE" / Flag,
    "HEADER_HOSTNAME" / Flag,
    "HEADER_BUILD_ID" / Flag,
    "HEADER_TRACING_DATA" / Flag,
    "HEADER_RESERVED" / Flag,  # 0
    "HEADER_BRANCH_STACK" / Flag,
    "HEADER_NUMA_TOPOLOGY" / Flag,
    "HEADER_CPU_TOPOLOGY" / Flag,
    "HEADER_EVENT_DESC" / Flag,
    "HEADER_CMDLINE" / Flag,
    "HEADER_TOTAL_MEM" / Flag,  # KB
    "HEADER_CPUID" / Flag,
    "HEADER_CPUDESC" / Flag,  # 8
    "HEADER_AUXTRACE " / Flag,
    "HEADER_STAT" / Flag,
    "HEADER_CACHE" / Flag,
    "HEADER_SAMPLE_TIME" / Flag,
    "HEADER_SAMPLE_TOPOLOGY" / Flag,
    "HEADER_CLOCKID" / Flag,
    "HEADER_GROUP_DESC" / Flag,
    "HEADER_PMU_MAPPINGS" / Flag,  # 16
    "HEADER_PMU_CAPS" / Flag,
    "HEADER_HYBRID_TOPOLOGY" / Flag,
    "HEADER_CLOCK_DATA" / Flag,
    "HEADER_CPU_PMU_CAPS" / Flag,
    "HEADER_COMPRESSED" / Flag,
    "HEADER_BPF_BTF" / Flag,
    "HEADER_BPF_PROG_INFO" / Flag,
    "HEADER_DIR_FORMAT" / Flag,  # 24
    Padding(256 - 3 * 8),
)

# Here is the specific format for various optional features. Currently, only some
# of the optional features that we may be interested in have been decoded, while
# the rest have been ignored.
# For a complete list of features, please refer to the reference link in the top comment.
features = Struct(
    "TRACING_DATA"
    / If(lambda this: this._.flags.HEADER_TRACING_DATA, perf_file_section),
    "BUILD_ID" / If(lambda this: this._.flags.HEADER_BUILD_ID, perf_file_section),
    "HOSTNAME" / If(lambda this: this._.flags.HEADER_HOSTNAME, perf_header_string),
    "OSRELEASE" / If(lambda this: this._.flags.HEADER_OSRELEASE, perf_header_string),
    "VERSION" / If(lambda this: this._.flags.HEADER_VERSION, perf_header_string),
    "ARCH" / If(lambda this: this._.flags.HEADER_ARCH, perf_header_string),
    "NRCPUS" / If(lambda this: this._.flags.HEADER_NRCPUS, perf_file_section),
    "CPUDESC" / If(lambda this: this._.flags.HEADER_CPUDESC, perf_header_string),
    "CPUID" / If(lambda this: this._.flags.HEADER_CPUID, perf_header_string),
    "TOTAL_MEM" / If(lambda this: this._.flags.HEADER_TOTAL_MEM, perf_file_section),
    "CMDLINE" / If(lambda this: this._.flags.HEADER_CMDLINE, perf_header_string_list),
)

# The definition of single perf event, currently only parses AUXTRACE
perf_event = Struct(
    "start" / Tell,
    "type"
    / Enum(
        Int32un,
        # We only need to focus on AUXTRACE events,
        # as SPE records are stored after AUXTRACE events.
        AUXTRACE=71,
    ),
    "misc" / Int16un,
    "size" / Int16un,
    "end" / Tell,
    "data"
    / Switch(
        lambda this: this.type,
        {
            "AUXTRACE": Struct(
                "auxsize" / Int64un,
                "offset" / Int64un,
                "reference" / Int64un,
                "idx" / Int32un,
                "tid" / Int32un,
                "cpu" / Int32un,
                "reserved__" / Int32un,
                "realData" / Padding(lambda this: this.auxsize),  # SPE records
            )
        },
        Padding(lambda this: this.size - (this.end - this.start)),
    ),
)

perf_header = Struct(
    "magic" / Padding(8),
    "size" / Int64un,
    "attr_size" / Int64un,
    "attrs" / perf_file_section,
    "data" / perf_file_section,
    "event_types" / perf_file_section,
    "flags" / feature_flags,
    "feature" / Pointer(lambda this: this.data.offset + this.data.size, features),
)

# The definition of perf.data file
perf_data = Struct(
    "header" / perf_header,
    "event" / Pointer(lambda this: this.header.data.offset, GreedyRange(perf_event)),
)


def get_spe_records_regions(perf_path: str) -> List[Dict]:
    """
    The SPE records have been stored in different locations within
    the perf.data file (following the AUXTRACE event, which defines
    the corresponding SPE record's offset and size).
    get_spe_records_regions() function is used to obtain the starting
    offset, size, and corresponding CPU number of all SPE records.

    After obtaining the SPE records regions, the spe_decoder can be
    used to parse the SPE binary content into a readable version
    """
    spe_regions = []
    parsed_data = perf_data.parse_file(perf_path)

    # log some metadata in perf.data
    logging.debug(f"size: {parsed_data.header.data.size}")
    logging.debug(f"hostname: {parsed_data.header.feature.HOSTNAME.pointer.str}")
    logging.debug(f"os: {parsed_data.header.feature.OSRELEASE.pointer.str}")
    logging.debug(f"perf version: {parsed_data.header.feature.VERSION.pointer.str}")
    logging.debug(f"arch: {parsed_data.header.feature.ARCH.pointer.str}")
    logging.debug(
        f"cmdline: {' '.join(x.str for x in parsed_data.header.feature.CMDLINE.pointer)}"
    )

    for evt in parsed_data.event:
        if evt.type == "AUXTRACE":
            spe_regions.append(
                {
                    "offset": evt.start + evt.size,
                    "size": evt.data.auxsize,
                    "cpu": evt.data.cpu,
                }
            )
    return spe_regions
