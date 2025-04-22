# Topdown Tool
Topdown tool runs top down performance analysis on supported Arm CPUs.

For each supported CPU Arm distributes a telemetry specification JSON file which describes topdown metrics to compute.

Topdown Tool captures PMU events for the required metrics using [`perf stat` on Linux](https://perf.wiki.kernel.org/index.php/Main_Page) on Linux and [WindowsPerf](https://gitlab.com/Linaro/WindowsPerf/windowsperf) on Microsoft Windows

# Requirements
* A working [Linux Perf](https://perf.wiki.kernel.org/index.php/Main_Page) or [WindowsPerf](https://gitlab.com/Linaro/WindowsPerf/windowsperf) (3.3.3 or later) setup.
* Python 3.9 or later.
* Under Linux: `inotifywait` from `inotify-tools` package

# Install
This tool must be installed as a python package by running the following from the project directory:

```sh
pip3 install .
```

or

```sh
pip3 install --user .
```


# Usage

First, install and configure [Linux Perf](https://perf.wiki.kernel.org/index.php/Main_Page) or [WindowsPerf](https://gitlab.com/Linaro/WindowsPerf/windowsperf).

## Permissions

topdown-tool needs to access performance monitoring counters (PMUs) in system-wide mode.
This requires elevated permissions. There are a few ways you can satisfy this requirement:

- **Changing `/proc/sys/kernel/perf_event_paranoid` to `-1` (recommended for most cases):** This is the quickest and most practical method, especially on single user machine or throwaway environment.

```sh
sudo sh -c 'echo -1 > /proc/sys/kernel/perf_event_paranoid'
```

- Granting your user the `CAP_PERFMON` capability.
- Running as root (not recommended unless in a throwaway environment or for quick experiments).

If you are on a system with SELinux enabled, you may find performance monitoring is blocked. In such cases you might also need to run:

```sh
sudo setenforce 0
```

to temporarily disable enforcement.

Once the permissions are set, you can run `topdown-tool` as your normal user.

If you encounter additional issues please read the [known issues section](#known-issues) below.

## Running from a package install
If installed as a python package, and pip's `<install>/bin` directory is in your PATH, you can execute the tool as follows:

```sh
topdown-tool --help
```

## Choosing what to monitor

topdown-tool lets you fine-tune what is being monitored:

- The `--core` or `-C` options let you restrict monitoring to specific CPU cores. For instance, to monitor only cores 0 and 1:

      topdown-tool --core 0,1 myapp

- If you want the monitored application itself to be scheduled on specific cores, combine topdown-tool with `taskset`, like so:

      topdown-tool --core 0,1 taskset -c 0,1 myapp

This runs your application (`myapp`) on cores 0 and 1, and ensures measurements are also limited to those cores.


### Launch and monitor an application
```sh
topdown-tool ./a.out
```

> :warning: On Windows you must explicitly specify core on which application will spawn.

```cmd
topdown-tool -C 0 ./a.out
```

### Monitor a running application
> :warning: This is not currently supported on Windows.

> :warning: On Linux due to limitations of perf command, collection happens system-wide for the duration of monitored processes. When last process terminates, collection stops.

You can specify one or more process IDs to monitor:
```sh
$ topdown-tool -p 289156
Monitoring PID: 289156. Hit Ctrl-C to stop.
...
```
```sh
$ topdown-tool --pid 289156,289153
Monitoring PIDs: 289153,289156. Hit Ctrl-C to stop.
...
```

### System-wide monitoring
If no application or process ID is specified, then system-wide monitoring will be performed (for all CPUs/cores)

```sh
$ topdown-tool
Starting system-wide profiling. Hit Ctrl-C to stop. (See --help for usage information.)
...
```

## Choosing which metrics to measure
### What is available?
The metrics (and metric groups) available will depend on the Arm CPU used.

Examples below were collected on a Neoverse N1 system.

To query the available metric groups:

```sh
$ topdown-tool --cpu-list-groups
CPU Neoverse V1 groups
┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Key                     ┃ Group                              ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ Topdown_L1              │ Topdown Level 1                    │
│ Cycle_Accounting        │ Cycle Accounting                   │
│ General                 │ General                            │
│ MPKI                    │ Misses Per Kilo Instructions       │
│ Miss_Ratio              │ Miss Ratio                         │
│ Branch_Effectiveness    │ Branch Effectiveness               │
│ ITLB_Effectiveness      │ Instruction TLB Effectiveness      │
│ DTLB_Effectiveness      │ Data TLB Effectiveness             │
│ L1I_Cache_Effectiveness │ L1 Instruction Cache Effectiveness │
│ L1D_Cache_Effectiveness │ L1 Data Cache Effectiveness        │
│ L2_Cache_Effectiveness  │ L2 Unified Cache Effectiveness     │
│ LL_Cache_Effectiveness  │ Last Level Cache Effectiveness     │
│ Operation_Mix           │ Speculative Operation Mix          │
└─────────────────────────┴────────────────────────────────────┘
```

To query metrics according to the Arm Topdown Performance Analysis Methodology:

```sh
$ topdown-tool --cpu-list-metrics
CPU Neoverse V1 metrics
├── Stage 1 (Topdown metrics)
│   └── Topdown Level 1 (Topdown_L1)
│       ├── This metric group contains the first set of metrics to begin topdown analysis of
│       │   application performance, which provide the percentage distribution of processor pipeline
│       │   utilization.
│       └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┓
│           ┃ Key             ┃ Metric          ┃
│           ┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━┩
│           │ backend_bound   │ Backend Bound   │
│           │ bad_speculation │ Bad Speculation │
│           │ frontend_bound  │ Frontend Bound  │
│           │ retiring        │ Retiring        │
│           └─────────────────┴─────────────────┘
└── Stage 2 (uarch metrics)
    ├── Cycle Accounting (Cycle_Accounting)
    │   ├── This metric group contains a set of metrics that measure the percentage of processor
    │   │   cycles stalled in either frontend or backend of the processor.
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
    │       ┃ Key                     ┃ Metric                  ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │       │ backend_stalled_cycles  │ Backend Stalled Cycles  │
    │       │ frontend_stalled_cycles │ Frontend Stalled Cycles │
    │       └─────────────────────────┴─────────────────────────┘
    ├── General (General)
    │   ├── This metric group contains general CPU metrics for performance analysis.
    │   └── ┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┓
    │       ┃ Key ┃ Metric                 ┃
    │       ┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━┩
    │       │ ipc │ Instructions Per Cycle │
    │       └─────┴────────────────────────┘
...
```

### Topdown metrics
By default, metrics from the Arm topdown performance analysis methodology will be selected, and grouped by stage:

```sh
$ topdown-tool ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 metrics
├── Stage 1 (Topdown metrics)
│   └── Topdown Level 1 (Topdown_L1)
│       └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
│           ┃ Metric          ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
│           ┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
│           │ Backend Bound   │ 43.76            │ 46.81 │ 42.21 │ %    │
│           │ Bad Speculation │ 1.59             │ 1.43  │ 1.75  │ %    │
│           │ Frontend Bound  │ 35.49            │ 36.27 │ 34.68 │ %    │
│           │ Retiring        │ 15.01            │ 14.73 │ 15.30 │ %    │
│           └─────────────────┴──────────────────┴───────┴───────┴──────┘
└── Stage 2 (uarch metrics)
    ├── Cycle Accounting (Cycle_Accounting)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
    │       ┃ Metric                  ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
    │       │ Backend Stalled Cycles  │ 42.43            │ 44.00 │ 41.08 │ %    │
    │       │ Frontend Stalled Cycles │ 26.16            │ 31.41 │ 18.99 │ %    │
    │       └─────────────────────────┴──────────────────┴───────┴───────┴──────┘
    ├── General (General)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
    │       ┃ Metric                 ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit      ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
    │       │ Instructions Per Cycle │ 1.113            │ 1.158 │ 1.075 │ per cycle │
    │       └────────────────────────┴──────────────────┴───────┴───────┴───────────┘
...
```

A specific stage can also be specified by number:

```sh
$ topdown-tool -s 1 ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 metrics
└── Stage 1 (Topdown metrics)
    └── Topdown Level 1 (Topdown_L1)
        └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
            ┃ Metric          ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
            ┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
            │ Backend Bound   │ 45.75            │ 45.00 │ 46.31 │ %    │
            │ Bad Speculation │ 2.74             │ 2.96  │ 2.40  │ %    │
            │ Frontend Bound  │ 37.11            │ 38.43 │ 36.15 │ %    │
            │ Retiring        │ 17.02            │ 18.71 │ 14.45 │ %    │
            └─────────────────┴──────────────────┴───────┴───────┴──────┘
```

or by name:

```sh
$ topdown-tool -s uarch ./a.out
Monitoring command: sleep. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 metrics
└── Stage 2 (uarch metrics)
    ├── Cycle Accounting (Cycle_Accounting)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
    │       ┃ Metric                  ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
    │       │ Backend Stalled Cycles  │ 39.95            │ 40.24 │ 39.75 │ %    │
    │       │ Frontend Stalled Cycles │ 17.62            │ 18.13 │ 17.09 │ %    │
    │       └─────────────────────────┴──────────────────┴───────┴───────┴──────┘
    ├── General (General)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
    │       ┃ Metric                 ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit      ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
    │       │ Instructions Per Cycle │ 0.877            │ 1.006 │ 0.784 │ per cycle │
    │       └────────────────────────┴──────────────────┴───────┴───────┴───────────┘
```

These metrics can also be combined into a single hierarchy:

```sh
$ topdown-tool -s combined ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 topdown
├── Topdown metrics
│   ├── Frontend Bound (frontend_bound)
│   │   ├── ┏━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
│   │   │   ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
│   │   │   ┡━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
│   │   │   │ 35.49            │ 36.39 │ 34.38 │ %    │
│   │   │   └──────────────────┴───────┴───────┴──────┘
│   │   ├── Branch Effectiveness (Branch_Effectiveness)
│   │   │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
│   │   │       ┃ Metric                ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit                  ┃
│   │   │       ┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│   │   │       │ Branch Misprediction  │ 0.008            │ 0.008 │ 0.007 │ per branch            │
│   │   │       │ Ratio                 │                  │       │       │                       │
│   │   │       │ Branch MPKI           │ 1.355            │ 1.055 │ 1.684 │ misses per 1,000      │
│   │   │       │                       │                  │       │       │ instructions          │
│   │   │       └───────────────────────┴──────────────────┴───────┴───────┴───────────────────────┘
...
```

### Collecting metric groups
It is also possible to collect specific metric groups (as show in `topdown-tool --cpu-list-groups`):

```sh
$ topdown-tool --cpu-metric-group MPKI,Miss_Ratio ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 metrics
└── Stage 2 (uarch metrics)
    ├── Misses Per Kilo Instructions (MPKI)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    │       ┃ Metric                  ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit                          ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │       │ Branch MPKI             │ 1.132            │ 1.047 │ 1.227 │ misses per 1,000 instructions │
    │       │ DTLB MPKI               │ 0.180            │ 0.103 │ 0.248 │ misses per 1,000 instructions │
    │       │ ITLB MPKI               │ 0.040            │ 0.077 │ 0.001 │ misses per 1,000 instructions │
    │       │ L1D Cache MPKI          │ 7.064            │ 7.898 │ 6.207 │ misses per 1,000 instructions │
    │       │ L1 Data TLB MPKI        │ 5.666            │ 4.653 │ 6.788 │ misses per 1,000 instructions │
    │       │ L1I Cache MPKI          │ 7.504            │ 8.629 │ 6.258 │ misses per 1,000 instructions │
    │       │ L1 Instruction TLB MPKI │ 0.076            │ 0.146 │ 0.004 │ misses per 1,000 instructions │
    │       │ L2 Cache MPKI           │ 5.984            │ 6.507 │ 5.404 │ misses per 1,000 instructions │
    │       │ L2 Unified TLB MPKI     │ 0.161            │ 0.295 │ 0.022 │ misses per 1,000 instructions │
    │       │ LL Cache Read MPKI      │ 3.684            │ 3.651 │ 3.722 │ misses per 1,000 instructions │
    │       └─────────────────────────┴──────────────────┴───────┴───────┴───────────────────────────────┘
    └── Miss Ratio (Miss_Ratio)
        └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
            ┃ Metric                        ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit             ┃
            ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
            │ Branch Misprediction Ratio    │ 0.007            │ 0.006 │ 0.009 │ per branch       │
            │ DTLB Walk Ratio               │ 0.002            │ 0.002 │ 0.000 │ per TLB access   │
            │ ITLB Walk Ratio               │ 0.000            │ 0.000 │ 0.000 │ per TLB access   │
            │ L1D Cache Miss Ratio          │ 0.022            │ 0.024 │ 0.019 │ per cache access │
            │ L1 Data TLB Miss Ratio        │ 0.018            │ 0.015 │ 0.022 │ per TLB access   │
            │ L1I Cache Miss Ratio          │ 0.021            │ 0.017 │ 0.030 │ per cache access │
            │ L1 Instruction TLB Miss Ratio │ 0.000            │ 0.000 │ 0.000 │ per TLB access   │
            │ L2 Cache Miss Ratio           │ 0.198            │ 0.194 │ 0.203 │ per cache access │
            │ L2 Unified TLB Miss Ratio     │ 0.034            │ 0.065 │ 0.000 │ per TLB access   │
            │ LL Cache Read Miss Ratio      │ 0.433            │ 0.409 │ 0.458 │ per cache access │
            └───────────────────────────────┴──────────────────┴───────┴───────┴──────────────────┘
```

Group names are case (and hyphen/underscore) insensitive, so the above is equivalent to:

```sh
topdown-tool --cpu-metric-group mpki,missratio ./a.out
```

## Collecting a branch of the hierarchy
It is also possible to collect a specific branch of the combined hierarchy:

```sh
$ topdown-tool --cpu-node frontend_bound ./a.out
Monitoring command: sleep. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V1 topdown
└── Topdown metrics
    └── Frontend Bound (frontend_bound)
        ├── ┏━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
        │   ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit ┃
        │   ┡━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
        │   │ 40.33            │ 40.74 │ 39.89 │ %    │
        │   └──────────────────┴───────┴───────┴──────┘
        ├── Branch Effectiveness (Branch_Effectiveness)
        │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
        │       ┃ Metric                     ┃ Aggregated (0-1) ┃ #0    ┃ #1    ┃ Unit                          ┃
        │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
        │       │ Branch Misprediction Ratio │ 0.005            │ 0.004 │ 0.005 │ per branch                    │
        │       │ Branch MPKI                │ 1.000            │ 0.639 │ 1.783 │ misses per 1,000 instructions │
        │       └────────────────────────────┴──────────────────┴───────┴───────┴───────────────────────────────┘
...
```


## Other options
See `topdown-tool --help` for full usage information.

# Known Issues
## Reduced PMU counter availability for non-metal AWS/EC2 instances
When running non-metal instances on Amazon's Elastic Compute Cloud, not all hardware event counters are available to the end user (even when reserving all cores on a node).

This results in fewer events being monitored simultaneously, which can increase negative effects associated with counter multiplexing.

In some cases, this can prevent all events within a single metric from being scheduled together, which will trigger an error.

### Possible workarounds:
* Use a metal instance.
* It is possible to schedule events within a metric independently by specifying `--cpu-collect-by=none`, although note that this can lead to unusual/invalid data for all but the most homogeneous workloads.


# Development

Whether you’re fixing a bug, exploring the code, or adding your own performance probe, this section will help you get started quickly and effectively.

## Requirements & Virtual Environment

We recommend working in a Python virtual environment:

```sh
python3 -m venv .venv
source .venv/bin/activate
```

Install the project in development (“editable”) mode with testing and linting dependencies:

```sh
pip install -e ".[test,lint]"
```

## Running from the Repository

With your environment set up, you can run `topdown-tool` directly from anywhere in your shell after activating your virtualenv.
If you make local changes to the source code, they’ll immediately be picked up.

Alternativelly on Linux, you can execute the `topdown-tool` script from the project directory:
```sh
./topdown-tool --help
```

On Windows, you can also run:
```cmd
python.exe .\topdown_tool
```


## Setting Up Pre-Commit and Code Quality Tools

For a smoother development experience, we recommend enabling pre-commit hooks and using our formatting/linting toolchain. Run:

```sh
pip install pre-commit
pre-commit install
```

This will ensure checks for formatting (black) and linting (flake8, pylint) run automatically before each commit.

## Running Common Tasks

- **Code formatting:**
   ```sh
   black .
   ```
- **Type checking:**
   ```sh
   mypy .
   ```
- **Unit tests:**
   ```sh
   pytest .
   ```

## Regenerating and Diffing Test Fixtures

We maintain golden (“reference”) outputs for some CLI and probe tests. If a test fails because output changed intentionally, you can update reference outputs:

- To view a diff of changes without overwriting:
   ```
   pytest --regen-reference=dryrun --tb=short
   ```
- To overwrite reference files with new outputs:
   ```
   pytest --regen-reference=write
   ```

See `conftest.py` for more details on this workflow.

## Architecture Overview

### Probes and Factories

The “Probe” and “ProbeFactory” pattern is at the heart of topdown-tool. Each probe is a self-contained measurement and reporting unit. Factories set up CLI options, handle user arguments, and instantiate their probe(s). Browse `probe/probe.py` for base class documentation.

- **To add a new probe:**
 Create a new factory/probe pair, and register your factory using Python’s entry point system.

### Extending Beyond This Repository

You can register new probes from another Python package by adding an appropriate entry to your own package’s `pyproject.toml` under `[project.entry-points."topdown_tool.probe_factories"]`.
This lets you create plug-in probes that don’t touch the core repository.

### Additional Internals of Interest

- `perf/`: All things performance event capture and event grouping.
- `layout/`: Pretty terminal and table output (Rich-powered).
- `common/`: Range handling, string normalization, argument helpers, and reusable exceptions.

If you’re building a new probe, refer to the existing CPU probe and the base classes as templates.
