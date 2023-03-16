import math
import os.path
from pathlib import Path
from typing import List

import pytest

from topdown_tool.__main__ import COMBINED_STAGES, DEFAULT_ALL_STAGES, print_nested_metrics
from topdown_tool.metric_data import AnyMetricInstance, MetricData, MetricInstanceValue

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "metric-output")
TEST_CPUS = ["neoverse-n1", "neoverse-v1"]


def create_value_instances(metric_instances: List[AnyMetricInstance]):
    return [MetricInstanceValue(metric_instance=mi, value=0.0) for mi in metric_instances]


def test_nan_output(capsys):
    metric_data = MetricData("neoverse-n1")
    metric_instance = metric_data.metrics_for_group("cycle-accounting")[0]
    print_nested_metrics([MetricInstanceValue(metric_instance=metric_instance, value=math.nan)], COMBINED_STAGES, False, False)
    captured = capsys.readouterr()

    assert captured.out == "[Cycle Accounting]      [Topdown group]\n" \
                           "Frontend Stalled Cycles nan (division by zero?)\n"


@pytest.mark.parametrize("stages_mode", ["staged", "combined"])
@pytest.mark.parametrize("exec_mode", ["list", "run"])
@pytest.mark.parametrize("cpu", TEST_CPUS)
def test_metric_output(capsys, cpu, exec_mode, stages_mode):
    stages = {"staged": DEFAULT_ALL_STAGES, "combined": COMBINED_STAGES}[stages_mode]

    metric_data = MetricData(cpu)
    metrics = metric_data.all_metrics(stages)

    if exec_mode == "run":
        metrics = create_value_instances(metrics)

    print_nested_metrics(metrics, stages, False, False)
    captured = capsys.readouterr()

    expected = Path(TEST_DATA_DIR, f"{cpu}-{stages_mode}-{exec_mode}.txt").read_text(encoding="utf-8")
    assert captured.out == expected
