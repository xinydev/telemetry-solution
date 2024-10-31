#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2024 Arm Limited

import json
import os
import sys

# Update path when running file/package directly (not as a module).
if __name__ == "__main__" and not __package__:
    sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

from topdown_tool.utils import get_midr_string_windows


MIDR_PATH = "/sys/devices/system/cpu/cpu0/regs/identification/midr_el1"
MAPPING_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metrics", "mapping.json")


def get_midr_string_linux():
    """Reads the Main ID Register (MIDR).

    See https://developer.arm.com/documentation/100616/0301/register-descriptions/aarch64-system-registers/midr-el1--main-id-register--el1
    """
    with open(MIDR_PATH, encoding="utf-8") as f:
        return f.readline().rstrip()


def get_cpuid(midr_string=None, perf_path=None):
    """Create a CPU ID from the implementer and part num components of the specified MIDR string.

    If no MIDR is specified, the MIDR of the first CPU/core on the current machine will be used."""
    if not midr_string:
        midr_string = get_midr_string_linux() if sys.platform == "linux" else get_midr_string_windows(perf_path)

    midr = int(midr_string, 16)
    implementer = (midr & 0xff000000) >> 24
    part_num = (midr & 0x0000fff0) >> 4
    return (implementer << 12) + part_num


def get_cpu(midr_string=None, perf_path=None):
    """Returns the name of the CPU/core specified MIDR string.

    If no MIDR is specified, the MIDR of the first CPU/core on the current machine will be used."""
    cpu_id = get_cpuid(midr_string, perf_path=perf_path)
    cpus = read_cpus()

    cpu = cpus.get(cpu_id)
    if cpu:
        cpu = cpu.lower().replace(" ", "-")
    return cpu


def read_cpus():
    """Returns a dict of cpuid => CPU name by fetching metadata from Arm's github repo"""
    with open(MAPPING_FILE_PATH, encoding="utf-8") as f:
        cpus_json = json.load(f)
    return {int(cpuid, 16): cpu["name"] for cpuid, cpu in cpus_json.items()}


if __name__ == "__main__":
    print(get_cpu())
