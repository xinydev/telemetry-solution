# Arm Telemetry Solution

Arm Telemetry Solution provides a standardized framework for system-level performance analysis across Arm platforms, including CPUs and interconnects such as CMN. It includes telemetry specifications, a data framework, a top-down performance analysis methodology, command-line tools, and validation workloads.

The solution leverages telemetry data from Arm IP to identify performance bottlenecks and improve execution efficiency across the full system stack.

This repository is organized into the following components:

- **data** Contains the telemetry specification JSON for all supported Arm products, including CPU and CMN interconnect.
- **tools** Contains telemetry tools and utilities for telemetry data collection, analysis, and visualization.
- **benchmarks** Contains validation test suites such as `ustress` and `systress`.

## How It Fits Together

The Arm Telemetry Solution enables a unified performance analysis workflow:

1. **Telemetry Specifications (JSON)** define PMU events, metrics, and methodology for CPU and CMN  
2. **Topdown Tool** consumes these specifications to collect telemetry data, compute metrics, and apply the Topdown methodology
3. **Benchmarks (UStress / SysTress)** validate telemetry metrics and stress specific system components  

This enables consistent, methodology-driven analysis across compute and system components.

## Content

- [Arm Telemetry Solution](#arm-telemetry-solution)
  - [Content](#content)
  - [Arm Topdown Methodology](#arm-topdown-methodology)
  - [Arm CPU Telemetry Solution](#arm-cpu-telemetry-solution)
  - [Arm CMN Telemetry Solution](#arm-cmn-telemetry-solution)
  - [Arm Telemetry Framework](#arm-telemetry-framework)
  - [Telemetry Specifications \& JSON Schema](#telemetry-specifications--json-schema)
    - [CPU JSON Schema](#cpu-json-schema)
      - [Event Field Definitions](#event-field-definitions)
      - [Metric Field Definitions](#metric-field-definitions)
      - [Topdown Methodology Field Definitions](#topdown-methodology-field-definitions)
    - [CMN JSON Schema](#cmn-json-schema)
  - [Tools](#tools)
  - [Benchmarks](#benchmarks)
  - [Support](#support)
  - [License](#license)


## Arm Topdown Methodology

Arm Topdown Methodology specifies a set of metrics and performance analysis methodology using hardware PMU events, to help identify processor & system bottlenecks during workload execution. The methodology applies across compute and system components, enabling hierarchical analysis from CPU pipeline inefficiencies to interconnect and memory subsystem bottlenecks.

Arm Topdown methodology can be conducted in two stages:

- **Stage 1: Topdown Analysis** Topdown hot spot analysis stage using stall-related metrics to locate the pipeline bottlenecks.

- **Stage 2: Micro-architecture Exploration** Deeper analysis stage to further analyze bottlenecked resources, using per micro-architecture resource effectiveness metric groups and metrics.

With support for both CPU and CMN telemetry, the solution enables cross-component analysis, correlating CPU behavior with interconnect and memory system activity.

## Arm CPU Telemetry Solution

The Arm CPU Telemetry Solution enables collection, analysis, and representation of CPU telemetry data on Arm platforms.

- Each supported CPU provides a Telemetry Specification defining PMU events and a metric-driven hierarchical decision tree for hotspot detection. This decision tree is Arm’s implementation of the Topdown Methodology for performance analysis.

- Telemetry data is structured in the Arm Telemetry Framework, which standardizes events/metrics into machine-readable JSON (MRS). This supports large-scale data collection, processing, and integration with profiling tools.
- The solution includes the Arm Top-Down tool, a simple CLI for profiling applications. It parses the MRS to collect telemetry data and deliver performance insights. The tool is supported on Linux and Windows.

For more information about Arm CPU Telemetry Solution, see Arm® Telemetry on Arm Developer, see [Arm CPU Telemetry Solution Topdown Methodology Specification](https://developer.arm.com/documentation/109542/latest/). 

Key chapters from this solution architecture specification are as below:

| Chapter                                          | Content     |
| -------------------------------------------------| ----------- |
| Arm Topdown Methodology                          | Topdown methodology and stages for performance analysis (Stage 1 and Stage 2).            |
| Arm Telemetry Framework for CPUs                 | Arm telemetry framework and data model standardization.                                   |
| Arm Telemetry Specification and Profiling Tools  | Details on how telemetry specification is enabled for Linux and Windows perf tools.       |
| Arm Top-Down tool Example                        | Arm Top-Down tool data collection example.                                                 |
| Linux perf data collection                       | Linux perf tool data collection example.                                                  |
| Windows perf data collection                     | Windows perf tool data collection example.                                                |


Refer to [Arm Neoverse V1 Performance Analysis Methodology whitepaper](https://armkeil.blob.core.windows.net/developer/Files/pdf/white-paper/neoverse-v1-core-performance-analysis.pdf) for an example Arm Topdown methodology supported by the Neoverse V1 processor, with example case studies.

Key chapters from this whitepaper are as below:

| Chapter     | Content     |
| ----------- | ----------- |
| 2           | PMU event and metric cheat sheets for performance analysis |
| 3           | Arm topdown performance analysis methodology (Neoverse V1). This chapter describes the methodology in detail with all metrics. |
| 4           | An example case study to demonstrate how to use our methodology for code tuning exercise. |
| Appendix B  | Telemetry Specification: PMU events with concise descriptions |
| Appendix C  | Telemetry Specification: Metrics and metric groups for performance analysis derives using PMU events |


**Note:** 

The Arm CPU Telemetry Solution is supported across all Neoverse and Lumex CPUs, with PMU events, metrics, and methodology defined and upstreamed in Linux perf. Support for additional Arm CPUs will be available soon.

## Arm CMN Telemetry Solution

The Arm CMN Telemetry Solution extends the telemetry framework to Arm Coherent Mesh Network (CMN) interconnects, enabling system-level performance analysis beyond CPU cores.

- CMN telemetry specifications define PMU events and derived metrics for key interconnect components such as RN-F, HN-F, SN-F, and mesh links.
- These metrics enable visibility into bandwidth utilization, congestion, latency, and traffic distribution across the mesh.
- CMN telemetry integrates with the Arm Topdown methodology and tooling, enabling correlated CPU + interconnect analysis.
- CMN specifications follow the same JSON-based telemetry schema, enabling seamless integration with existing tools and workflows.

This support enables users to:
- Identify system bottlenecks caused by memory and interconnect pressure
- Correlate CPU stalls with fabric-level behavior
- Perform end-to-end performance analysis across compute and data movement

## Arm Telemetry Framework

The building blocks of the Telemetry Framework are as follows. 

- **Events** are hardware PMU events that count micro-architectural activity.

- **Metrics** specify mathematical relations between events that help with the correlation of events for analyzing the system.

- **Metric Groups** specify a group of metrics that can be analysed together for a use case. Metric Groups can be components of methodology.

- **Methodology** specifies different performance analysis approaches common among software consumers or performance analysts.


## Telemetry Specifications & JSON Schema

Arm provides a standardized JSON schema to describe PMU events, derived metrics, and methodology for supported IP blocks (e.g., CPU and CMN) in a single file, enabling seamless integration with tooling.

### CPU JSON Schema

High level schema structure is as follows:

{
  "events": {},        // PMU events supported by the CPU
  "metrics": {},       // Derived metrics supported by the CPU
  "groups": {          // Grouping of events and metrics
    "function": {},    // Event groups by CPU function
    "metrics": {}      // Metric groups for analysis/methodology
  },
  "methodologies": {
    "topdown_methodology": {}  // Stages and decision tree for Topdown analysis
  }
}

#### Event Field Definitions

| Field                 | Definition |
|-----------------------|------------|
| `code`                | Event register code for counting |
| `title`               | Title of the event |
| `description`         | Description of what is being counted for the event |
| `accesses`            | Access interface – PMU/ETM |
| `architecture_defined` | Architecturally defined event, included in Arm Architecture Reference Manual |
| `product_defined`     | Micro-architecture implementation specific event, specified by the product architecture |


#### Metric Field Definitions

| Field                |   Definition                                                        |
|----------------------|---------------------------------------------------------------------|
| `title`              |   Title of the Metrics                                              |
| `formula`            |   Formula to compute the metrics                                    |
| `description`        |   Description of the metrics                                        |
| `units`              |   Metrics unit                                                      |
| `events`             |   Events needed to calculate the metrics                            |
| `sample_events`      |   Events for sampling if a bottleneck is detected with this metric  |


#### Topdown Methodology Field Definitions

| Field             | Definition  |
|-------------------|-------------|
| `title`           | Title       |
| `description`     | Description |
| `metric_grouping` | Metric groups used for each stage of the methodology added as lists  |
| decision tree     | Stage 1 topdown analysis tree with root_nodes and child metrics. Each metric has the following fields:<br><ul><li>**`name`:** metric name<br><li>**`group`:** metric groups the metric belong to<li>**`next_items`:** leaves of the node<li>**`sample_events`:** Events for sampling if the bottleneck is detected at this specific metric node</ul> |

### CMN JSON Schema

CMN telemetry specifications use the same JSON schema structure as CPU, with component-specific definitions for events, metrics, and methodology. This ensures consistent tooling support and enables unified analysis across compute and interconnect domains.

## Tools

The tooling stack enables collection, parsing, and analysis of telemetry data. The Arm Top-Down tool serves as the primary entry point for methodology-driven performance analysis across CPU and CMN telemetry.

| Name                | Description | Folder |
|---------------------|-------------|--------|
| Arm Top-Down tool | Primary CLI tool implementing the Arm Topdown methodology across CPU and CMN telemetry. It consumes telemetry specifications (JSON) to collect PMU & hardware telemetry data, compute metrics, and apply the Topdown methodology for quick analysis| [tools/topdown_tool](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/tools/topdown_tool) |
| Perf JSON Generator | Tool to generate JSON files for Linux perf tool which enable and document Arm PMU events and metrics. | [tools/perf_json_generator](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/tools/perf_json_generator) |
| SPE Parser          | Tool to parse SPE raw data and generate a Parquet or CSV file for further processing and analysis. | [tools/spe_parser](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/tools/spe_parser) |
| UStress Charts      | Visualization tooling for metrics generated from the ustress suite workloads. | [tools/ustress_charts](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/tools/ustress_charts) |


## Benchmarks

The benchmarks folder contains validation test suites used to stress CPU and system resources (including interconnect and memory subsystem) and validate the telemetry solution.

| Name           | Description | Folder |
|----------------|-------------|--------|
| Ustress Suite  | Validation workload suite to stress test major CPU resources. | [benchmarks/ustress](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/benchmarks/ustress) |
| Systress Suite  | System-level stress and validation suite targeting CMN and memory subsystem behavior. | [benchmarks/systress](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/benchmarks/systress) |
| Matrix Multiplication Kernels | Dense matmul variants (naïve, loop-reordered, blocked) for locality and cache reuse studies. | [benchmarks/matmul](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/benchmarks/matmul) |
| Random Pointer Access | Pointer-chasing microbenchmark with optional software prefetch tuning. | [benchmarks/random_pointer_access](https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/tree/main/benchmarks/random_pointer_access) |



## Support

For feedback, collaboration or support, contact <telemetry-solution@arm.com>.


## License

This project is licensed as Apache-2.0. See LICENSE.md for more details.
