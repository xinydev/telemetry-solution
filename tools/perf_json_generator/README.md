# Perf json file generator

This directory contains a script that generates json files for Perf
which enable and document Arm PMU events and metrics. It requires Python
3.8, and there are no additional pip dependencies to run the script.

The json files are generated in place in the Linux repo. As in, new
events will be added inline with, or replace, existing ones. For this
reason the path to the Perf subfolder in the Linux repo should be given
as the first argument. In this readme it is denoted as `<perf-folder>`,
and as an example it could be:

  /work/linux/tools/perf

## Usage for telemetry-solution input format

Download your input json files from the telemetry-solution repo
(https://gitlab.arm.com/telemetry-solution/telemetry-solution) and plug
them into the script along with the path to Perf:

```
./generate.py <perf-folder> --telemetry-files neoverse_n1_pmu_specification.json
```

## Usage for arm-data input format

Clone the arm-data repo https://github.com/ARM-software/data/ and give
the script the path to Perf, the path to the arm-data repo, and the name
of the CPUs that should be generated:

```
./generate.py <perf-folder> --arm-data-path <arm-data-folder> --arm-data-cpus neoverse-n2
```

## Categorization

In Arm-data mode only, events are categorized by pattern matching in
categories.json. This may need to be updated to support new events. In
telemetry-solution mode, events are already categorized in the input
json.

## Renaming output files

Both input modes support the optional `:` syntax to give a different
name to the output. Otherwise the product name is used by default. In
this example the N1 events already exist as 'cortex-a76-n1' but in other
cases it may not be required:

```
./generate.py <perf-folder> --telemetry-files neoverse_n1_pmu_specification.json:cortex-a76-n1
```

# Contributing

Any contributions are welcome through Gitlab by raising a merge request
or an issue via:

  https://gitlab.arm.com/telemetry-solution/telemetry-solution

To install the dev requirements (not required to only run the script)
run:

```
pip install -r requirements-dev.txt
pre-commit install
```

There is a pre-commit hook that will run the following things
automatically on commit, but if you would like to run them manually:

## Coding style

Flake8 defaults, but with 100 character line lengths (as set by
.flake8). This can be checked by running the following command in this
folder:

```
flake8
```

## Tests

Pytest is used with its defaults, and can be run with the
following commands in this folder:

```
pytest
```

## Static type checking

Mypy is also used with its defaults, and can be run with the
following commands in this folder:

```
mypy .
```

## Maintainers

For questions and help with this tool:

James Clark <james.clark@arm.com>
Nick Forrington <nick.forrington@arm.com>
