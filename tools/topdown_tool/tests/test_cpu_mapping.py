# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

from cpu_mapping import get_cpu
from metric_data import MetricData


def test_get_cpu():
    assert get_cpu("0x00000000410fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000411fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000410fd400") == "neoverse-v1"
    assert get_cpu("0x00000000410fd490") == "neoverse-n2"


def test_no_mapping():
    assert "mapping" not in MetricData.list_cpus()
