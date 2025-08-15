#!/usr/bin/env python3

# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2024-2025 Arm Ltdimited

import shutil
import sys
from argparse import ArgumentParser
from csv import reader
from glob import glob
from os import remove, rename
from os.path import basename, join
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
        "--no-multiplex", action="store_true", help="Disallow PMU multiplexing"
    )
    parser.add_argument(
        "--stages", type=str, help='Control which stages to display, separated by a comma. e.g. "topdown,uarch" or "1,2" or "all"'
    )
    parser.add_argument(
        "--collect-by", type=str, help='When multiplexing, collect events grouped by "none", "metric" (default), or "group". This can avoid comparing data collected during different time periods.'
    )
    
    parser.add_argument("-o", "--output", help="Output directory")
    args = parser.parse_args()

    script_directory_path = Path(__file__).parent.absolute()
    ustress_path = script_directory_path.parent.joinpath("ustress").absolute()

    if args.output is not None:
        dataset_path = Path(args.output)
    else:
        dataset_path = Path()

    # For each workload collect data with topdown-tool and save it to CSV
    cpu_name = None
    for workload in args.workload:
        for n in range(args.run):
            cmd: List[Union[Path, str]] = [
                "topdown-tool",
                "-v",
                "--core",
                str(args.core),
                "--cpu-csv",
                dataset_path,
            ]
            if args.cpu is not None:
                cmd.extend(["--cpu", args.cpu])
            if args.no_multiplex:
                cmd.append("--cpu-no-multiplex")
            if args.stages:
                cmd.extend(["--cpu-stages", args.stages])
            if args.collect_by:
                cmd.extend(["--cpu-collect-by", args.collect_by])
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
                print(f"Could not run {workload} with {cmd}", file=sys.stderr)
                sys.exit(1)
            for line in proc.stderr.splitlines():
                if 'Running "' in line:
                    print(line.replace("INFO:root:Running", "Completed"))
            if cpu_name is None:
                cpu_name = basename(
                    glob("perf.stat.cpu.*.*.txt-*")[0]
                ).split(".")[3]
            for perf_file in glob("perf.stat.cpu.*.*.txt-*"):
                slice = basename(perf_file).split("-")[-1]
                shutil.copyfile(
                    perf_file,
                    join(dataset_path, f"perf_{workload}_cpu{args.core}_run{n}.{slice}.txt")
                )
            rename(
                join(dataset_path, f"core_{args.core}.csv"),
                join(dataset_path, f"dataset_{workload}_cpu{args.core}_run{n}.csv"),
            )

    for perf_file in glob("perf.stat.cpu.*.*.txt-*"):
        remove(perf_file)
    for perf_cli_file in glob("perf-cli-*"):
        remove(perf_cli_file)

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
                    group = row[1]
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
