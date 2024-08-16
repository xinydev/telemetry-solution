import argparse
import math
import os.path
from pathlib import Path
import shlex
from typing import List

import pytest

from topdown_tool.__main__ import COMBINED_STAGES, DEFAULT_ALL_STAGES, get_arg_parser, main, print_nested_metrics
from topdown_tool.metric_data import AnyMetricInstance, MetricData, MetricInstanceValue

TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "data", "metric-output")
TEST_CPUS = ["neoverse-n1", "neoverse-v1", "neoverse-n2", "neoverse-v2"]
DEFAULT_TEST_ARGS = ["--cpu", "neoverse-n1"]  # Specify CPU as MIDR may not be available on test machine (/in CI)


def create_value_instances(metric_instances: List[AnyMetricInstance]):
    return [MetricInstanceValue(metric_instance=mi, value=0.0) for mi in metric_instances]


def test_nan_output(capsys):
    metric_data = MetricData.get_data_for_cpu("neoverse-n1")
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

    metric_data = MetricData.get_data_for_cpu(cpu)
    metrics = metric_data.all_metrics(stages)

    if exec_mode == "run":
        metrics = create_value_instances(metrics)

    print_nested_metrics(metrics, stages, False, False)
    captured = capsys.readouterr()

    expected = Path(TEST_DATA_DIR, f"{cpu}-{stages_mode}-{exec_mode}.txt").read_text(encoding="utf-8")
    assert captured.out == expected


@pytest.mark.parametrize("args", ["--list-metrics", "--list-groups"])
def test_main_valid_args(args):
    """Simple test to ensure entry point runs without errors."""

    with pytest.raises(SystemExit) as e_info:
        main(DEFAULT_TEST_ARGS + shlex.split(args))

    assert e_info.value.code == 0


@pytest.mark.parametrize("args", ["--blah",                     # Unknown argument
                                  "--cpu=bad",                  # Unknown CPU
                                  "--pid 100 ./a.out",          # Mutually exclusive
                                  "--metric-group a --node b",  # Mutually exclusive
                                  "--interval",                 # Value not specified
                                  "--interval 100"])            # Requires CSV
def test_main_invalid_args(args):
    """Simple test to ensure entry point exits with error code on invalid arguments."""

    with pytest.raises(SystemExit) as e_info:
        main(DEFAULT_TEST_ARGS + shlex.split(args))

    assert e_info.value.code != 0


def test_platform_arg_defaults():
    """
    Test that defaults specified to arguments are the same as what comes out when specifying no arguments.

    An example of where this is not the case is:
    add_argument(..., default=None, nargs=argparse.REMAINDER)

    This ensures we don't have unexpected default argument values, and helps keep PlatformArgumentParser behaviour is consistent with ArgumentParser.
    """
    parser = get_arg_parser()
    args = parser.parse_args([])
    for action in parser._actions:  # pylint: disable=protected-access
        if action.default == argparse.SUPPRESS:
            continue
        assert action.dest in args
        assert getattr(args, action.dest) == action.default
