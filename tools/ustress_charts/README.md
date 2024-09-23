# Chart generating script

`ustress-charts.py` uses topdown-tool to capture metric data for selected ustress suite workloads and then generates charts from collected data.

## Getting started

`ustress-charts.py` requires *matplotlib* to be installed.

```sh
# Setup virtualenv
python3 -m venv .venv

# Load python environment
source .venv/bin/activate

# Install matplotlib
pip install matplotlib
```

If PMU multiplexing is disabled, then a C compiler is required to compile and run a PMU detection program.

```sh
make all
```

To generate your first charts using `ustress-charts.py`, ensure that perf monitoring is possible (this can be achieved by setting `echo -1 >/proc/sys/kernel/perf_event_paranoid` as root) and compile executables required using make. Then use the following command:

```sh
./ustress-charts.py --multiplex --workload branch_direct_workload branch_indirect_workload
```

Collected data will be saved to `dataset_branch_direct_workload_cpu0_run0.csv` and `dataset_branch_indirect_workload_cpu0_run0.csv`.

Charts will be saved to `*.png` files for each metric group with the same measurement units.

## Going further

`ustress-charts.py` includes options to adjust the capture such as setting the CPU number on which a worload must run, allowing/disallowing PMU multiplexing, repeating running of worloads given number of times.

Invoke the script with the `--help` option to view additional options.


## Limitations

1. It is not possible to pass arguments to workloads
1. All selected workloads will run on the same single CPU
