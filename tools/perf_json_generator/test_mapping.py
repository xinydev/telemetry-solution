#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import io

import mapping

TEST_MAPFILE = """# Example Perf mapfile.csv
#
# It contains comments, followed CSV formatted data.
#
# Items are sorted by MidrFields, but grouped by filename.
# e.g. The last N1 entry has a later MIDR than other cores, but is still
# grouped with the other N1s.
#
#Family-model,Version,Filename,EventType
0x00000000410fd0b0,v1,arm/cortex-a76-n1,core
0x00000000410fd0c0,v1,arm/cortex-a76-n1,core
0x00000000990fd0d0,v1,arm/cortex-a76-n1,core
0x00000000410fd400,v1,arm/neoverse-v1,core
0x00000000410fd490,v1,arm/neoverse-n2,core
"""


def midr_to_cpuid(midr_string):
    """Create a CPU ID from the implementer and part num components of the specified MIDR string."""
    midr = int(midr_string, 16)
    implementer = (midr & 0xff000000) >> 24
    part_num = (midr & 0x0000fff0) >> 4
    return (implementer << 12) | part_num


def cpu_id_str(cpu_id):
    return hex(cpu_id)


def test_name_to_key():
    assert mapping.name_to_key("Cortex-A8") == "cortex-a8"
    assert mapping.name_to_key("Neoverse N1") == "neoverse-n1"


def test_convert_cpuid():
    # Neoverse N2
    cpu_id = "0x41d49"
    perf_midr = "0x00000000410fd490"

    converted_cpu_id = midr_to_cpuid(perf_midr)
    converted_midr = mapping.MidrFields.from_arm_data(name="", cpuid=cpu_id)

    # Check values
    assert converted_cpu_id == int(cpu_id, 16)
    assert converted_midr.midr == int(perf_midr, 16)

    # Check formatting back to string
    assert cpu_id_str(converted_cpu_id) == cpu_id
    assert converted_midr.midr_string == perf_midr


def test_stable_csv():
    perf_mappings = mapping.PerfCpuMappings(io.StringIO(TEST_MAPFILE))

    string_output = io.StringIO()
    perf_mappings.write(string_output)

    assert string_output.getvalue() == TEST_MAPFILE
