#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2024

import sys
from argparse import ArgumentParser
from csv import reader
from os import remove
from pathlib import Path
from statistics import mean
from subprocess import DEVNULL, PIPE, CalledProcessError, run
from typing import Dict, Iterable, List, Optional, Tuple, TypedDict, Union

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure


class InternalPlotDataSeries(TypedDict):
    label: str
    x: List[float]
    y: List[float]
    bottom: List[float]


class PlotDataSeries(TypedDict):
    label: str
    values: List[float]


def main() -> None:
    parser = ArgumentParser(
        description="Test compiled ustress workloads with topdown tool",
        allow_abbrev=False,
    )
    parser.add_argument(
        "-w",
        "--workload",
        action="extend",
        nargs="+",
        required=True,
        help="List of ustress suite workloads to run",
    )
    parser.add_argument("-c", "--cpu", help="CPU description JSON file path")
    parser.add_argument(
        "-C", "--core", type=int, default=0, help="CPU core to run workload on"
    )
    parser.add_argument(
        "-r", "--run", type=int, default=1, help="repeat run of each workload"
    )
    parser.add_argument(
        "-m", "--multiplex", action="store_true", help="Allow PMU multiplexing"
    )
    parser.add_argument("-o", "--output", help="Output directory")
    args = parser.parse_args()

    script_directory_path = Path(__file__).parent.absolute()

    if not args.multiplex:
        pmu_detect_path = script_directory_path.joinpath("pmu_available_per_core")
        # Compile PMU detection program, if executable not found.
        if not pmu_detect_path.exists():
            try:
                run(["make", "-C", script_directory_path], check=True)
            except CalledProcessError:
                print(
                    'Could not run "make" to compile PMU detection program',
                    file=sys.stderr,
                )
                sys.exit(1)
        pmus_per_core: List[Optional[int]] = []
        try:
            proc = run(pmu_detect_path, stdout=PIPE, check=True, text=True)
        except CalledProcessError:
            print(
                'Could not run "pmu_available_per_core" to detect number of PMUs',
                file=sys.stderr,
            )
            sys.exit(1)
        for line in proc.stdout.splitlines():
            pmu_count = line.split()[2]
            pmus_per_core.append(int(pmu_count) if pmu_count.isdigit() else None)

    topdowntool_path = (
        script_directory_path.parent.joinpath("topdown_tool")
        .joinpath("topdown-tool")
        .absolute()
    )
    ustress_path = script_directory_path.parent.joinpath("ustress").absolute()

    if args.output is not None:
        dataset_path = Path(args.output)
    else:
        dataset_path = Path()

    # For each workload collect data with topdown-tool and save it to CSV
    for workload in args.workload:
        for n in range(args.run):
            cmd: List[Union[Path, str]] = [
                topdowntool_path,
                "-v",
                "--csv",
                dataset_path.joinpath(f"dataset_{workload}_cpu{args.core}_run{n}.csv"),
            ]
            if args.cpu is not None:
                cmd.extend(["--cpu", args.cpu])
            if not args.multiplex and pmus_per_core[args.core] is not None:
                cmd.extend(["--max-events", str(pmus_per_core[args.core])])
            cmd.extend(
                [
                    "taskset",
                    format(1 << args.core, "X"),
                    ustress_path.joinpath(workload),
                ]
            )
            print(f'Running workload "{workload}" ({n+1} of {args.run})')
            try:
                proc = run(cmd, stdout=DEVNULL, stderr=PIPE, check=True, text=True)
            except CalledProcessError:
                print(f"Could not run {workload}", file=sys.stderr)
                sys.exit(1)
            for line in proc.stderr.splitlines():
                if 'Running "' in line:
                    print(line.replace("INFO:root:Running", "Completed"))
    remove("perf.stat.txt")

    # Process CSV metric data to generate charts
    results: Dict[str, Dict[str, Dict[str, Dict[str, List[float]]]]] = {}
    for workload in args.workload:
        for run_number in range(args.run):
            file = dataset_path.joinpath(
                f"dataset_{workload}_cpu{args.core}_run{run_number}.csv"
            )
            with open(file, encoding="utf-8") as csv_file:
                csv_reader = reader(csv_file)
                next(csv_reader)
                for row in csv_reader:
                    group = row[3]
                    unit = row[6]
                    metric = row[4]
                    results.setdefault(group, {}).setdefault(unit, {}).setdefault(
                        workload, {}
                    ).setdefault(metric, []).append(float(row[5]))

    # Generate charts from CSV data
    if args.output is not None:
        chart_path = Path(args.output)
    else:
        chart_path = Path()

    # Temporary
    stacked_metrics = {"Speculative Operation Mix", "Topdown Level 1"}

    def create_figure(
        data_series: List[PlotDataSeries],
        title: str,
        xticklabels: Iterable[str],
        ylabel: str,
        stacked_bar: bool = False,
        legend_width: float = 0,
    ) -> Tuple[Figure, Axes]:
        GROUP_WIDTH = 0.9
        bar_width = GROUP_WIDTH if stacked_bar else GROUP_WIDTH / len(data_series)

        # Prepare data for plotting
        plot_data_series: List[InternalPlotDataSeries] = []
        next_bottom = [0.0] * len(data_series[0]["values"])
        for data_serie_index, data_serie in enumerate(data_series):
            bottom = next_bottom
            y = data_serie["values"]
            if not stacked_bar:
                x = [
                    i + (bar_width - GROUP_WIDTH) / 2 + bar_width * data_serie_index
                    for i in range(len(y))
                ]
            else:
                x = list(range(len(y)))
                next_bottom = [bottom[i] + y[i] for i in range(len(y))]
            plot_data_series.append(
                {"x": x, "y": y, "label": data_serie["label"], "bottom": bottom}
            )

        # Create plot
        fig, ax = plt.subplots(figsize=(6.4 + legend_width, 4.8))
        for plot_data_serie in plot_data_series:
            ax.bar(
                plot_data_serie["x"],
                plot_data_serie["y"],
                bar_width,
                bottom=plot_data_serie["bottom"],
                label=plot_data_serie["label"],
                zorder=2,
            )
        ax.set_ylabel(ylabel)
        ax.set_title(title)
        ax.set_xticks(range(len(plot_data_series[0]["x"])))
        ax.set_xticklabels(xticklabels, rotation=45, ha="right")
        ax.grid(axis="y", zorder=1)
        ax.legend(bbox_to_anchor=(1, 1), loc="upper left")
        return fig, ax

    for group_key, unit_data_for_group in results.items():
        title = group_key

        # Temporary
        stacked_bar = group_key in stacked_metrics

        for unit_key, workload_data_for_unit in unit_data_for_group.items():
            xticklabels = workload_data_for_unit.keys()
            ylabel = unit_key
            plot_data_series: List[PlotDataSeries] = []
            for workload_index, metric_data_for_workload in enumerate(
                workload_data_for_unit.values()
            ):
                for metric_index, (metric_key, metric_value) in enumerate(
                    metric_data_for_workload.items()
                ):
                    if workload_index == 0:
                        plot_data_series.append({"values": [], "label": metric_key})
                    plot_data_series[metric_index]["values"].append(mean(metric_value))
            # Temporary chart to calculate space for legend
            fig, ax = create_figure(
                plot_data_series, title, xticklabels, ylabel, stacked_bar
            )
            fig.canvas.draw()
            legend_width = ax.get_legend().get_window_extent().width / fig.dpi
            plt.close(fig)
            # Real chart with space for legend
            fig, _ = create_figure(
                plot_data_series, title, xticklabels, ylabel, stacked_bar, legend_width
            )
            fig.tight_layout()
            chart_name = (
                group_key
                if len(unit_data_for_group) == 1
                else f"{group_key}_{unit_key}"
            )
            chart_filename = chart_name.lower().replace(" ", "_")
            plt.savefig(chart_path.joinpath(f"{chart_filename}_cpu{args.core}.png"))
            plt.close()


if __name__ == "__main__":
    main()
