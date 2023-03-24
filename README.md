# Telemetry Solution

## Contents

- [Arm Topdown Methodology](#arm-topdown-methodology)
- [PMU Events](#pmu-events)
- [Arm Telemetry Framework](#arm-telemetry-framework)
- [JSON Schema](#json-schema)
    - [Event Field Definitions](#event-field-definitions)
    - [Metric Field Definitions](#metric-field-definitions)
    - [Topdown Methodology Field Definitions](#topdown-methodology-field-definitions)
- [Tools](#tools)
- [Support](#support)
- [License](#license)


## Arm Topdown Methodology

Arm Topdown Methodology specifies a set of metrics and methodology using hardware PMU events that can be used for identifying processor bottlenecks during the code execution.

Arm Topdown methodology for micro-architectural analysis can be conducted in two stages:

- **Stage 1:** Topdown Analysis stage for hot spot analysis, with stall-related metrics for identifying the pipeline bottleneck.

- **Stage 2:** Micro-architecture Exploration stage to conduct further analysis of bottlenecking CPU resources, with CPU resource effectiveness metric groups and metrics.


Refer to [Arm Neoverse V1 Performance Analysis Methodology whitepaper](https://armkeil.blob.core.windows.net/developer/Files/pdf/white-paper/neoverse-v1-core-performance-analysis.pdf) for an introduction to the Arm Topdown methodology supported by the Neoverse V1 processor.

Key chapters from this whitepaper are as below:

| Chapter     | Content     |
| ----------- | ----------- |
| 2           | PMU event and metric cheat sheets for performance analysis |
| 3           | Arm topdown performance analysis methodology (Neoverse V1). This chapter describes the methodology in detail with all metrics. |
| 4           | An example case study to demonstrate how to use our methodology for code tuning exercise. |
| Appendix B  | Telemetry Specification: PMU events with concise descriptions |
| Appendix C  | Telemetry Specification: Metrics and metric groups for performance analysis derives using PMU events |


**Note:** We support this solution for Neoverse CPUs at the moment with PMU events, metrics and methodology specified and upstreamed on Linux perf for Neoverse N1 and V1 CPUs. More Arm CPU support for the telemetry solution is coming soon.

For beginners who are not familiar with the Linux perf tool or looking for a quick primer on how to collect PMU events for performance analysis, refer to Chapter 4 of a previous whitepaper on this topic [Arm Neoverse N1 Performance Analysis Methodology whitepaper](https://armkeil.blob.core.windows.net/developer/Files/pdf/white-paper/neoverse-n1-core-performance-v2.pdf).


## PMU Events

Arm CPUs support PMU events that are architected and specified by Arm architecture. In this repository, we add all the PMU events supported by the Arm CPUs in a standardized machine-readable (JSON) format for tooling. The JSON files published for the Arm CPUs that support telemetry-solution follow the Arm Telemetry Framework and JSON Schema discussed below.

Please subscribe to release notifications on this [GitLab](https://gitlab.arm.com/telemetry-solution/telemetry-solution) project to follow the new CPUs that support the solution.

| Content                      | Description                                                                           | Folder |
|------------------------------|---------------------------------------------------------------------------------------|--------|
| Core Telemetry Specification | Telemetry specification of the CPU PMU events, metrics and methodology as JSON files. | [data/pmu/cpu](https://gitlab.oss.arm.com/engineering/valetudo/telemetry-solution/-/tree/main/data/pmu/cpu) |

For all the CPUs, key references for PMU events are as below:

| Document type                      | Reference Links |
|------------------------------------|-----------------|
| Arm Architecture PMU Specification | [Arm Architecture Reference Manual for A-profile architecture](https://developer.arm.com/documentation/ddi0487/latest/) |
| CPU PMU Specification              | Check the product TRM   (Eg: [Arm Neoverse V1 Technical Reference Manual](https://developer.arm.com/documentation/101427/latest/)) |
| CPU PMU Guides                     | For Neoverse CPUs, Arm publishes PMU Guides per product to provide more clarification on the PMU event implementation on the specific product.   (Eg: [Arm Neoverse V1 PMU Guide](https://developer.arm.com/documentation/PJDOC-1063724031-605393/2-0/?lang=en)) |


## Arm Telemetry Framework

The building blocks of the Telemetry Framework are as follows.

- **Events** are hardware PMU events that count micro-architectural activity.

- **Metrics** specify mathematical relations between events that help with the correlation of events for analyzing the system.

- **Metric Groups** specify a group of metrics that can be analysed together for a use case. Metric Groups can be components of methodology.

- **Methodology** specifies different performance analysis approaches common among software consumers or performance analysts.


## JSON Schema

Arm has developed a standardized JSON schema for PMU events, metrics and methodology tree for a CPU in a single JSON file for tooling.

High level schema of the first release of JSON file is as below:

    "events": {}  #PMU events supported by the CPU
    "metrics": {} #Derived metrics supported by the CPU
    "groups": {   #Groups of events and metrics
        "function": {} #Event groups based on CPU function
        "metrics": {}  #Metric groups for analysis and methodology
    }
    "methodologies": {
        "topdown_methodology": {} #Topdown methodology stages and decision tree
    }


### Event Field Definitions

| Field           | Definition |
|-----------------|------------|
| `code`          | Event register code for counting |
| `title`         | Title of the event |
| `description`   | Description of what is being counted for the event |
| `common`        | Common architectural event that should be common across Arm micro-architectures |
| `accesses`      | Access interface – PMU/ETM |
| `architectural` | Architecturally specified event, included in Arm Architecture Reference Manual |
| `impdef`        | Micro-architecture implementation specific event, not specified by the architecture |


### Metric Field Definitions

| Field         |   Definition                              |
|---------------|-------------------------------------------|
| `title`       |   Title of the Metrics                    |
| `formula`     |   Formula to compute the metrics          |
| `description` |   Description of the metrics              |
| `units`       |   Metrics unit                            |
| `events`      |   Events needed to calculate the metrics  |


### Topdown Methodology Field Definitions

| Field             | Definition  |
|-------------------|-------------|
| `title`           | Title       |
| `description`     | Description |
| `metric_grouping` | Metric groups used for each stage of the methodology added as lists  |
| decision tree     | Stage 1 topdown analysis tree with root_nodes and child metrics. Each metric has the following fields:<br><ul><li>**`name`:** metric name<br><li>**`group`:** metric groups the metric belong to<li>**`next_items`:** leaves of the node<li>**`sample_events`:** Events for sampling if the bottleneck is detected at this specific metric node</ul> |


## Tools

The tools folder contains a collection of tools used for performance analysis on Arm-based platforms. There are tools to perform topdown analysis (topdown_tool), stress microarchitectural CPU features (ustress), and others to convert data into more easily consumable formats (perf_json_generator, spe_parser).

| Name                | Description | Folder |
|---------------------|-------------|--------|
| Perf JSON Generator | Tool to generate JSON files for Linux perf tool which enable and document Arm PMU events and metrics. | [tools/perf_json_generator](https://gitlab.oss.arm.com/engineering/valetudo/telemetry-solution/-/tree/main/tools/perf_json_generator) |
| SPE Parser          | Tool to parse SPE raw data and generate a Parquet or CSV file for further processing and analysis. | [tools/spe_parser](https://gitlab.oss.arm.com/engineering/valetudo/telemetry-solution/-/tree/main/tools/spe_parser) |
| Topdown Tool        | Tool to support the Arm topdown methodology by collecting derived metrics based on Performance Monitoring Unit (PMU) events. | [tools/topdown_tool](https://gitlab.oss.arm.com/engineering/valetudo/telemetry-solution/-/tree/main/tools/topdown_tool) |
| UStress workload    | Validation workload suite to stress test major CPU resources. | [tools/ustress](https://gitlab.oss.arm.com/engineering/valetudo/telemetry-solution/-/tree/main/tools/ustress) |


## Support

For feedback, collaboration or support, contact <telemetry-solution@arm.com>.


## License

This project is licensed as Apache-2.0. See LICENSE.md for more details.
