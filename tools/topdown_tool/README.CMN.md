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

## CMN Telemetry JSON Shape

This section describes the practical CMN shape used by CMN telemetry specifications JSON such as `cmn_700_r0p0_pmu.json`.

A JSON specification file gives static information for a CMN product revision: event definitions, watchpoint definitions, derived metrics, filters, and groups. It does not describe the topology of a specific machine. To count per-node or per-port events you still need discovered topology to know which CMN indices, XP IDs, node IDs, and ports actually exist on the target system.

### Root structure

```jsonc
{
  "$schema": "v1.2.schema.json",
  "document": { "...": "metadata" },
  "product_configuration": {
    "product_name": "CMN 700",
    "major_revision": "0",
    "minor_revision": "0"
  },
  "events": {
    "SYS_FREQUENCY": { "...": "global event" }
  },
  "metrics": {
    "cmn_bw_total": { "...": "top-level composite metric" }
  },
  "groups": {
    "metrics": {
      "CMN_Requestor_Bandwidth": { "...": "top-level metric group" }
    }
  },
  "components": {
    "HNF": { "...": "component definition" }
  }
}
```

- `product_configuration` identifies the CMN product version and revision for the file as a whole. On Linux, the matching hardware identifier is exposed through the CMN perf device `identifier` file.
- Root `events` are global/system helper inputs such as `SYS_FREQUENCY`.
- Root `metrics` are top-level composite metrics, usually built from component metrics and sometimes global events.
- Root `groups.metrics` groups those top-level metrics for selection and display.
- `components` contains the real node and port-device definitions.

### Component structure

```jsonc
"components": {
  "HNF": {
    "product_configuration": {
      "device_id": 5
    },
    "filter_specification": {
      "description": "...",
      "filters": {
        "occupancy_filter": { "...": "filter definition" }
      }
    },
    "events": {
      "PMU_HNF_CACHE_MISS": { "...": "component event" }
    },
    "watchpoints": {
      "CMN_HNF_WP_RXREQ_COUNT": { "...": "component watchpoint" }
    },
    "metrics": {
      "cmn_hnf_memreq_ratio": { "...": "component metric" }
    },
    "groups": {
      "function": {
        "HNF": { "...": "event group" }
      },
      "metrics": {
        "HNF_SLC_Effectiveness": { "...": "metric group" }
      }
    }
  }
}
```

- CMN internal device components are identified by `product_configuration.device_id`. In practice this is the starting point for the PMU `type=` value used to build CMN perf events for that device class, although Linux may apply a device-type fixup for some components.
- Port components such as `RNF`, `SNF`, and `CCG` are handled differently. In practice they are resolved from the component name and compared to the port device types defined for the system. These components are watchpoint-based.
- Not every component has every optional section. For example, some components have no `filter_specification`, no `watchpoints`, or no metric groups.

### Events

Events are the basic PMU counters used as inputs to metrics.

Important fields:
- `code`: the event selector value
- `title`: human-readable event name
- `description`: explains what the event counts

How to use them:
- This applies to CMN internal device components, the ones identified by `product_configuration.device_id`.
- To build a perf event, combine the component `device_id` with the event `code`.

- In the JSON, `code` is written as a hexadecimal string such as `0x000F`. For perf, use its numeric value as `eventid=`.
- For a CMN instance-wide event, the Linux perf shape is:
  ```text
  arm_cmn_<index>/type=<device_id>,eventid=<code>/
  ```
- To target a specific discovered node, add `bynodeid,nodeid=<node_id>`:
  ```text
  arm_cmn_<index>/type=<device_id>,eventid=<code>,bynodeid,nodeid=<node_id>/
  ```
- `title` and `description` are documentation fields to help users understand what they are counting.

Special cases:
- On Linux, `RNI` is identified as `RND` by the perf driver, so the runtime remaps the perf `type=` accordingly.
- `SYS_CMN_CYCLES` is a special case used by many metrics. It is not built from a normal event `code`; the runtime treats it as a cycle event with `type=3` and no `eventid`.

### Watchpoints

Watchpoints count protocol traffic patterns at the port level, identified by CHI flit direction, channel, group, and masked value matching. A port may contain multiple devices underneath it.

Important fields:
- `description`: explains what the watchpoint is intended to count
- `wp_val`: value programmed into the watchpoint match
- `wp_mask`: mask that selects which bits participate in the match
- `mesh_flit_dir`: CHI flit direction to monitor
- `wp_chn_sel`: CHI channel to monitor
- `wp_grp`: CMN watchpoint group to use
- `field_name` and `field_value`: decoded interpretation of the mask/value pair

How to use them:
- `mesh_flit_dir` selects upload vs download and maps to `watchpoint_up` or `watchpoint_down` in perf.
- `wp_chn_sel` selects the CHI channel and maps `REQ`/`RSP`/`SNP`/`DAT` to `0`/`1`/`2`/`3` in perf.
- `wp_grp` selects the watchpoint group and maps `Primary`/`Secondary`/`Tertiary`/`Quaternary` to `0`/`1`/`2`/`3` in perf.
- `wp_mask` and `wp_val` define the bit match.
- For a local watchpoint tied to a specific discovered XP and port, the Linux perf shape is:
  ```text
  arm_cmn_<index>/watchpoint_<up|down>,bynodeid,nodeid=<xp_id>,wp_dev_sel=<port>,wp_chn_sel=<channel>,wp_grp=<group>,wp_mask=<mask>,wp_val=<value>/
  ```
- Topology is required to know which XP/port pairs exist and where a given port component is present.

### Metrics

Metrics are the user-facing derived values reported by the tool. A metric combines one or more inputs, which can be raw PMU events, watchpoints, or other metrics.

Important fields:
- `title`: human-readable metric name
- `formula`: expression used to compute the metric
- `description`: explains what the metric means
- `units`: unit for the computed value
- `events`: raw PMU event inputs to collect
- `watchpoints`: watchpoint inputs to collect
- `metrics`: dependent metrics that must be resolved first
- `filters`: optional filter settings applied to specific metric events

How to use them:
- Resolve any referenced metrics in `metrics` first.
- If a metric has `filters`, use each entry to modify the referenced event before building the perf event:
  - `filter_name` selects a filter definition from the same component's `filter_specification`
  - `encodings` selects one of that filter's symbolic encoding names
  - the selected encoding resolves to a numeric value, which is applied to the event as the occupancy selector (`occupid` in the runtime/perf representation)
- Collect every dependency listed in `events` and `watchpoints`.
- Apply `formula` to produce the final metric value.

### Filters

`filter_specification` is the component-local catalog of filters that metrics can refer to:
- the `encodings` map is the key operational data
- `register` and `field` under `access` are useful metadata explaining where the filter comes from in the hardware view

### Groups

Groups are convenience bundles that help users and tools select a meaningful set of things to collect or display without naming every item individually:
- `groups.function` bundles related raw events for a functional area
- `groups.metrics` bundles derived metrics

Groups do not change perf encoding. They only describe how events and metrics should be organized and selected.

### Cross-reference rules

- In practice, internal CMN devices use `device_id`, while port components are resolved from the component name against discovered topology.
- Component metrics reference events and watchpoints defined in the same component.
- Component function groups reference local component events.
- Component metric groups reference local component metrics.
- Root metrics and root metric groups can reference root metrics plus component metrics.
- Event, watchpoint, metric, and group names are expected to be unique across components, with the existing special-case duplicate `SYS_CMN_CYCLES`.

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
