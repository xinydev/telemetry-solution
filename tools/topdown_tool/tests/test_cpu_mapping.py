# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2024 Arm Limited

from topdown_tool.cpu_mapping import get_cpu
from topdown_tool.metric_data import MetricData


def test_get_cpu():
    assert get_cpu("0x00000000410fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000411fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000410fd400") == "neoverse-v1"
    assert get_cpu("0x00000000410fd490") == "neoverse-n2"
    assert get_cpu("0x00000000410fd4f0") == "neoverse-v2"
    assert get_cpu("0x00000000410fd8e0") == "neoverse-n3"
    assert get_cpu("0x00000000410fd830") == "neoverse-v3"


def test_get_cpu_full_midr():
    # Special case for N2 r0p3
    assert get_cpu("0x00000000410fd493") == "neoverse-n2-r0p3"


def test_cobalt_100_cpu():
    # Microsoft Azure Cobalt 100 report a Neoverse N2 part number, but a different implementer code
    assert get_cpu("0x000000006d0fd490") == "neoverse-n2"
    assert get_cpu("0x000000006d0fd493") == "neoverse-n2-r0p3"


def test_no_mapping():
    assert "mapping" not in MetricData.list_cpus()
