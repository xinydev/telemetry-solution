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

```sh
topdown-tool --core 0,1 myapp
```
- If you want the monitored application itself to be scheduled on specific cores, combine topdown-tool with `taskset`, like so:
```sh
topdown-tool --core 0,1 taskset -c 0,1 myapp
```
This runs your application (`myapp`) on cores 0 and 1, and ensures measurements are also limited to those cores.


### Launch and monitor an application

```sh
topdown-tool -- ./a.out
```

Note that you can use "--" to separate topdown-tool options from the command you want to run.


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

## Probe selection

Topdown-tool is extensible and can load one or more "probes" that monitor your system's subsystems. The CPU probe is selected by default.

- List available probes:
```sh
topdown-tool --probe-list
```

- Select probes explicitly (comma-separated or repeatable):
```sh
topdown-tool --probe CPU -- ./a.out
```

Each probe has its own quickstart. For the CPU probe, see [README.CPU.md](./README.CPU.md).

## CSV output overview

You can export results to CSV for post-processing:
- Provide an output directory with `--csv-output-path`.
- Enable CSV in the probe (for CPU: `--cpu-generate-csv metrics[,events]`).
- Optionally sample periodically with `-I` (interval in ms).

Example:
```sh
topdown-tool --cpu-generate-csv metrics --csv-output-path out -I 1000 -- sleep 10
```

See [README.CPU.md](./README.CPU.md) for CSV file names, folder layout, and column details.

First, install and configure [Linux Perf](https://perf.wiki.kernel.org/index.php/Main_Page) or [WindowsPerf](https://gitlab.com/Linaro/WindowsPerf/windowsperf).

## Choosing which metrics to measure (per probe)

Metric selection is probe-specific. For the CPU probe, see [README.CPU.md](./README.CPU.md) for:
- Listing available metric groups and metrics
- Topdown stages (1, 2) and the combined view
- Selecting metric groups, stages, and methodology nodes
- Examples with output and CSV guidance


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
   ```sh
   pytest --regen-reference=dryrun --tb=short
   ```
- To overwrite reference files with new outputs:
   ```sh
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
