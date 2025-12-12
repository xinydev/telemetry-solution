# telemetry-solution/tools

This folder contains the tools for Telemetry solution and methodologies.


## topdown_tool

Tool to support the Arm topdown methodology by collecting derived metrics based on Performance Monitoring Unit (PMU) events. The CLI can operate on the host or on remote Linux/Android devices.

The `ustress` validation workload suite resides in `../benchmarks/ustress`.

## ustress_charts

Chart generation tool for metrics for workloads from the ustress suite.

## perf_json_generator

Tool to generate JSON files for Linux perf tool which enable and document Arm PMU events and metrics.

## spe_parser

Tool to parse SPE (Statistical Profiling Extension) raw data and generate a Parquet or CSV file for further processing and analysis.
