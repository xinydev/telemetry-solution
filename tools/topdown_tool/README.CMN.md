# CMN Probe Quickstart

This guide focuses on CMN-specific workflows. For installation details, permissions, and the full list of shared tool options see [README.md](./README.md). For complete CLI syntax run:

```sh
topdown-tool --probe CMN --help
```

## What the CMN probe does

The CMN probe collects PMU-based metrics according to Arm’s Top-down CMN performance analysis or user chosen metrics/groups defined in a CMN telemetry specification (JSON). It can:
- List devices, groups, metrics, and events referenced by the specification.
- Capture and print metrics in a table layout.
- Export metrics and/or raw events as CSV.

By default, the CPU probe is selected. Add `--probe CMN` (or `--probe CPU,CMN`) to include the CMN probe explicitly.

## Requirements

### CMN mesh layout and permissions

CMN topology discovery happens through `cmn_discover.py` from [cmn-tools](https://github.com/ArmDeveloperEcosystem/cmn-tools) (or `wperf cmninfo` on Windows). On Linux, generating the mesh layout typically requires root. The probe discovers the topology on the fly unless you provide a saved layout file.

Generate a layout once as root:

```sh
sudo topdown-tool --probe CMN --cmn-mesh-layout-output cmn_mesh.json --cmn-list
```

Reuse the saved layout with only perf permissions:

```sh
topdown-tool --probe CMN --cmn-mesh-layout-input cmn_mesh.json -- ./a.out
```

Notes:
- Mesh layout files are machine-specific and cannot be shared across cloud instances.
- Regenerate the layout after every boot or hardware change.

## Inspect what's available

- List detected CMN versions and indices:
  ```sh
  topdown-tool --probe CMN --cmn-list
  ```

- List CMN devices:
  ```sh
  topdown-tool --probe CMN --cmn-list-devices
  ```

- List metric groups; add descriptions:
  ```sh
  topdown-tool --probe CMN --cmn-list-groups --cmn-print-descriptions
  ```

- List metric groups for specific devices only (dash "-" and underscore "_" are ignored when choosing device, in this and subsequent options, HNI = HN-I = HN_I):
  ```sh
  topdown-tool --probe CMN --cmn-list-groups HNI,RNI
  ```

- List metrics (add descriptions and sample events):
  ```sh
  topdown-tool --probe CMN --cmn-list-metrics --cmn-print-descriptions --cmn-show-sample-events
  ```

- List metrics for specific devices only:
  ```sh
  topdown-tool --probe CMN --cmn-list-metrics HNI,RNI
  ```

- List PMU events and watchpoints referenced by the spec:
  ```sh
  topdown-tool --probe CMN --cmn-list-events
  ```

- List PMU events and watchpoints referenced by the spec for specific devices only:
  ```sh
  topdown-tool --probe CMN --cmn-list-events HNI,RNI
  ```

## Running workloads

Use `--` to separate tool options from your command.

- Run a command:
  ```sh
  topdown-tool --probe CMN -- ./a.out
  ```

By default, metrics from the Arm topdown performance analysis methodology will be selected, and grouped by stage:

```sh
$ topdown-tool --probe CMN -- ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CMN-700 at index 0
  CMN Requestor Target Characterization Level 1
            (Requestor Type Dominance)
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                 ┃ Value                 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ CCG Request Proportion │ 0.0032495658265046857 │
│ RND Request Proportion │ 0.004658816312488861  │
│ RNF Request Proportion │ 0.8001890053592966    │
│ RNI Request Proportion │ 0.19190261250170973   │
└────────────────────────┴───────────────────────┘
   CMN Requestor Target Characterization Level 2 (Local vs.
                   Remote Traffic Affinity)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                                ┃ Value              ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━┩
│ Local Destination Request Proportion  │ 0.4542998622524106 │
│ Remote Destination Request Proportion │ 0.5457001377475894 │
└───────────────────────────────────────┴────────────────────┘
 CMN Requestor Target Characterization Level 3
            (Target Type Dominance)
┏━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┓
┃ Metric                ┃ Value                ┃
┡━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━┩
│ CCG Target Proportion │ 0.4244330231626358   │
│ IO Target Proportion  │ 0.008894092940680718 │
│ SLC Target Proportion │ 0.4874515574161192   │
│ SNF Target Proportion │ 0.07922132648056425  │
└───────────────────────┴──────────────────────┘
...
```

- Collect specific metrics (comma-separated, case-insensitive):
  ```sh
  topdown-tool --probe CMN --cmn-metrics cmn_hnf_atomic_data_fwd_rate -- ./a.out
  ```

- Collect specific metric groups (comma-separated, case-insensitive):
  ```sh
  topdown-tool --probe CMN --cmn-metric-groups HNF_Ingress_Traffic -- ./a.out
  ```

- Restrict counting to specific CMNs:
  ```sh
  topdown-tool --probe CMN --cmn-indices 0 --cmn-metric-groups HNF_Ingress_Traffic -- ./a.out
  ```

## Accuracy and detail

- Control multiplexing grouping (default: metric):
  ```sh
  topdown-tool --probe CMN --cmn-collect-by none --cmn-metric-groups HNF_Ingress_Traffic -- ./a.out
  ```
  `--cmn-collect-by` accepts `none` or `metric`. Grouping by metric keeps related events in the same time slice when multiplexing.
- Collect per-node/device metrics:
  ```sh
  topdown-tool --probe CMN --cmn-capture-per-device-id --cmn-metric-groups HNF_Ingress_Traffic -- ./a.out
  ```

## CSV output

CSV is the easiest way to post-process both metrics and raw events.

- Specify where to write:
  ```sh
  --csv-output-path <directory>
  ```
  A timestamped subdirectory (YYYY_MM_DD_HH_MM_SS) is created automatically.
- Enable CSV for metrics and/or events:
  ```sh
  --cmn-generate-csv metrics[,events]
  ```

Examples:
- Metrics:
  ```sh
  topdown-tool --probe CMN --csv-output-path out --cmn-generate-csv metrics sleep 5
  ```
- Events:
  ```sh
  topdown-tool --probe CMN --csv-output-path out --cmn-generate-csv events sleep 5
  ```
- Both metrics and events:
  ```sh
  topdown-tool --probe CMN --csv-output-path out --cmn-generate-csv metrics,events sleep 5
  ```

### CSV output organization

Files are written under:
```sh
<csv-output-path>/<YYYY_MM_DD_HH_MM_SS>/cmn/
```

You’ll see product-level files:
```
<product>_events.csv
<product>_<index>_metrics.csv
experimental_topdown_<product>_<index>_metrics.csv
```

Notes:
- <product> is the CMN product name lowercased with spaces and dashes replaced by underscores.
- <index> is CMN index within the system.

### Base Metrics CSV format

Each metrics CSV has:
- run: always 1 for CMN
- time: empty and unused by the CMN probe
- level: empty and unused by the CMN probe
- stage: empty and unused by the CMN probe
- group: CMN group name
- metric: CMN metric name
- node: text location or "Global"
- nodeid: numeric (decimal) Node ID or empty (for Global)
- value: Numeric value for that metric; blank when not available
- interrupted: empty and unused by the CMN probe
- units: Units string from the specification (e.g., “%”, “per cycle”, “misses per 1,000 instructions”).

Example:
```
run,time,level,stage,group,metric,node,nodeid,value,interrupted,units
1,,,,HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,Global,,1.0426987692465326,,Allocations per 1K clks
1,,,,HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,XP 0x088 Port #0 Node 0x088,136,0.009516968475741103,,Allocations per 1K clks
1,,,,HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,XP 0x088 Port #0 Node 0x089,137,0.010375875830157328,,Allocations per 1K clks
```

### Topdown Metrics CSV format

Each metrics CSV has:
- group: CMN topdown group name
- metric: CMN topdown metric name
- value: Numeric value for that metric; blank when not available

Example:
```
Group,Metric,Value
CMN_Requestor_Target_Characterization_level_one,requestor_proportion_CCG,0.0005051780752715332
CMN_Requestor_Target_Characterization_level_one,requestor_proportion_RND,0.0
CMN_Requestor_Target_Characterization_level_one,requestor_proportion_RNF,0.9989138671381662
CMN_Requestor_Target_Characterization_level_one,requestor_proportion_RNI,0.0005809547865622631
CMN_Requestor_Target_Characterization_level_two,requestor_destination_proportion_local,0.12973829918129762
CMN_Requestor_Target_Characterization_level_two,requestor_destination_proportion_remote,0.8702617008187025
```

### Events CSV format

Each events CSV has:
- Group: CMN group name
- Metric: CMN metric name
- Event: CMN event name
- CMN: CMN index
- X: X coordinate (decimal); blank if "Global"
- Y: Y coordinate (decimal); blank if "Global"
- Port: Port number; blank if "Global"
- Node: Node ID (decimal); blank if "Global"
- Value: Numeric value; blank for “not counted/unsupported”.

Example (no interval; blank time):
```
Group,Metric,Event,CMN,X,Y,Port,Node,Value
HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,SYS_CMN_CYCLES,0,,,,,20846567935.80794
HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,PMU_HNS_SLC_FILL_INVALID_WAY,0,,,,,21736690.72968117
HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,SYS_CMN_CYCLES,0,1,1,0,136,20846567935.80794
HNS_SLC_Effectiveness,cmn_hns_slc_alloc_invalid_way_rate,PMU_HNS_SLC_FILL_INVALID_WAY,0,1,1,0,136,198396.12987247945
```

## Custom specs and validation

- Override the packaged spec:
  ```sh
  topdown-tool --probe CMN --cmn-specification /path/to/custom_cmn.json -- ./a.out
  ```
  (The option can be repeated to supply multiple CMN specs.)
- Validate a spec:
  ```sh
  validate-cmn-spec --file /path/to/custom_cmn.json --schema-dir topdown_tool/cmn_probe/schemas
  ```
- Or validate a directory:
  ```sh
  validate-cmn-spec --spec-dir topdown_tool/cmn_probe/metrics --schema-dir topdown_tool/cmn_probe/schemas
  ```

## Key flags (CMN)

### Common selection

- `--cmn-metric-groups`: Comma-separated metric groups to collect (default: all).
- `--cmn-metrics`: Comma-separated metric names to collect (default: all).
- `--cmn-indices`: Comma-separated CMN indices to collect.
- `--cmn-generate-csv`: `metrics`, `events`, or both (requires `--csv-output-path`).
- `--cmn-list`, `--cmn-list-devices`, `--cmn-list-groups`, `--cmn-list-metrics`, `--cmn-list-events`: Listing/inspection mode (skips capture).
- `--cmn-print-descriptions`, `--cmn-show-sample-events`: Expand listing output with descriptions and sample events.

### Accuracy and detail

- `--cmn-collect-by`: Event grouping when multiplexing (`none` or `metric`, default: `metric`).
- `--cmn-capture-per-device-id`: Collect per-node/device metrics (large tables; not meaningful for topdown metrics).

### Mesh layout and permissions

- `--cmn-mesh-layout-output`: Save generated mesh layout JSON.
- `--cmn-mesh-layout-input`: Reuse a saved mesh layout JSON.

### Custom specs and diagnostics

- `--cmn-specification` / `--cmn`: Override the packaged CMN spec JSON.
- `--cmn-debug-path`: Write perf command/output artifacts for debugging.
