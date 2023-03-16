# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

from topdown_tool.cpu_mapping import get_cpu
from topdown_tool.metric_data import MetricData


def test_get_cpu():
    assert get_cpu("0x00000000410fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000411fd0c0") == "neoverse-n1"
    assert get_cpu("0x00000000410fd400") == "neoverse-v1"


def test_no_mapping():
    assert "mapping" not in MetricData.list_cpus()
