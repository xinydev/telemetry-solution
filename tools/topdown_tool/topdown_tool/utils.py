# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

import json
import subprocess


def get_wperf_test_results(perf_path):
    result = subprocess.run([perf_path or "wperf", "test", "--json"], stdout=subprocess.PIPE, check=True)
    # {
    #   "Test_Results": [
    #     ...
    #     {
    #       "Result": "0x000000000000413fd0c1",
    #       "Test_Name": "PMU_CTL_QUERY_HW_CFG [midr_value]"
    #     },
    #     ...
    #   ]
    # }
    data = {
        item["Test_Name"]: item["Result"]
        for item in json.loads(result.stdout.decode("utf-8"))["Test_Results"]
    }
    return data


def get_midr_string_windows(perf_path: str) -> str:
    return get_wperf_test_results(perf_path)["PMU_CTL_QUERY_HW_CFG [midr_value]"]


def get_pmu_counters_windows(perf_path: str) -> int:
    return int(get_wperf_test_results(perf_path)["PMU_CTL_QUERY_HW_CFG [gpc_num]"], 16)
