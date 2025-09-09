# CPU Probe Quickstart

This quickstart shows how to explore CPU metrics, run captures, and export CSV data using the default CPU probe.

It is not a complete reference. For full options, run:
```sh
topdown-tool --help
```

For install, permissions, and general usage, see the top-level [README.md](./README.md).

## What the CPU probe does

The CPU probe collects PMU-based metrics according to Arm’s Top-down performance analysis methodology and other micro-architectural metric groups defined in a CPU telemetry specification (JSON). It can:
- List metric groups, metrics, and the PMU events referenced by the specification.
- Capture and print metrics in a compact tree/table layout.
- Export metrics and/or raw events as CSV.

By default, the CPU probe is selected. You can explicitly select probes with `--probe` (see the main [README.md](./README.md)).

## Inspect what’s available

- List metric groups:
```sh
topdown-tool --cpu-list-groups
```

- List metrics (add descriptions and sample events):
```sh
topdown-tool --cpu-list-metrics -d --cpu-show-sample-events
```

- List PMU events referenced by the spec:
```sh
topdown-tool --cpu-list-events
```

- List detected CPU models and the cores where they are present:
```sh
topdown-tool --cpu-list-cores
```

## Running workloads

Use “--” to separate tool options from your command.

- Run a command:
```sh
topdown-tool ./a.out
```

By default, metrics from the Arm topdown performance analysis methodology will be selected, and grouped by stage:

```sh
$ topdown-tool -- ./a.out
Monitoring command: stress-ng. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 metrics
├── Stage 1 (Topdown metrics)
│   └── Topdown Level 1 (Topdown_L1)
│       └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┓
│           ┃ Metric          ┃ Value ┃ Unit ┃
│           ┡━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━┩
│           │ Backend Bound   │ 39.07 │ %    │
│           │ Bad Speculation │ 8.63  │ %    │
│           │ Frontend Bound  │ 12.42 │ %    │
│           │ Retiring        │ 40.01 │ %    │
│           └─────────────────┴───────┴──────┘
└── Stage 2 (uarch metrics)
    ├── Cycle Accounting (Cycle_Accounting)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┓
    │       ┃ Metric                  ┃ Value ┃ Unit ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━┩
    │       │ Backend Stalled Cycles  │ 36.08 │ %    │
    │       │ Frontend Stalled Cycles │ 3.54  │ %    │
    │       └─────────────────────────┴───────┴──────┘
...
```

- Restrict to specific stage(s):
  - Stage selection by number
```sh
$ topdown-tool -s 1 -- ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 metrics
└── Stage 1 (Topdown metrics)
    └── Topdown Level 1 (Topdown_L1)
        └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┓
            ┃ Metric          ┃ Value ┃ Unit ┃
            ┡━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━┩
            │ Backend Bound   │ 38.80 │ %    │
            │ Bad Speculation │ 8.78  │ %    │
            │ Frontend Bound  │ 12.55 │ %    │
            │ Retiring        │ 39.99 │ %    │
            └─────────────────┴───────┴──────┘
```

  - Stage selection by name
```sh
$ topdown-tool -s uarch -- ./a.out
Monitoring command: sleep. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 metrics
└── Stage 2 (uarch metrics)
    ├── Cycle Accounting (Cycle_Accounting)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┓
    │       ┃ Metric                  ┃ Value ┃ Unit ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━┩
    │       │ Backend Stalled Cycles  │ 36.71 │ %    │
    │       │ Frontend Stalled Cycles │ 3.46  │ %    │
    │       └─────────────────────────┴───────┴──────┘
    ├── General (General)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━┓
    │       ┃ Metric                 ┃ Value ┃ Unit      ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━┩
    │       │ Instructions Per Cycle │ 3.258 │ per cycle │
    │       └────────────────────────┴───────┴───────────┘
```

  - Combined hierarchy
```sh
$ topdown-tool -s combined -- ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 topdown
├── Topdown metrics
│   ├── Frontend Bound (frontend_bound)
│   │   ├── ┏━━━━━━━┳━━━━━━┓
│   │   │   ┃ Value ┃ Unit ┃
│   │   │   ┡━━━━━━━╇━━━━━━┩
│   │   │   │ 12.09 │ %    │
│   │   │   └───────┴──────┘
│   │   ├── Branch Effectiveness (Branch_Effectiveness)
│   │   │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│   │   │       ┃ Metric                     ┃ Value ┃ Unit                          ┃
│   │   │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│   │   │       │ Branch Misprediction Ratio │ 0.011 │ per branch                    │
│   │   │       │ Branch MPKI                │ 1.821 │ misses per 1,000 instructions │
│   │   │       └────────────────────────────┴───────┴───────────────────────────────┘
...
```


- Collect a branch of the combined hierarchy:
```sh
$ topdown-tool --cpu-node frontend_bound -- ./a.out
Monitoring command: sleep. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 topdown
├── Topdown metrics
│   ├── Frontend Bound (frontend_bound)
│   │   ├── ┏━━━━━━━┳━━━━━━┓
│   │   │   ┃ Value ┃ Unit ┃
│   │   │   ┡━━━━━━━╇━━━━━━┩
│   │   │   │ 12.09 │ %    │
│   │   │   └───────┴──────┘
│   │   ├── Branch Effectiveness (Branch_Effectiveness)
│   │   │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
│   │   │       ┃ Metric                     ┃ Value ┃ Unit                          ┃
│   │   │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│   │   │       │ Branch Misprediction Ratio │ 0.011 │ per branch                    │
│   │   │       │ Branch MPKI                │ 1.821 │ misses per 1,000 instructions │
│   │   │       └────────────────────────────┴───────┴───────────────────────────────┘
...
```

- Collect specific metric groups (comma-separated, case-insensitive):
```sh
$ topdown-tool --cpu-metric-group MPKI,Miss_Ratio -- ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 metrics
└── Stage 2 (uarch metrics)
    ├── Misses Per Kilo Instructions (MPKI)
    │   └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
    │       ┃ Metric                  ┃ Value ┃ Unit                          ┃
    │       ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
    │       │ Branch MPKI             │ 1.679 │ misses per 1,000 instructions │
    │       │ DTLB MPKI               │ 0.002 │ misses per 1,000 instructions │
    │       │ ITLB MPKI               │ 0.000 │ misses per 1,000 instructions │
    │       │ L1D Cache MPKI          │ 1.173 │ misses per 1,000 instructions │
    │       │ L1 Data TLB MPKI        │ 0.792 │ misses per 1,000 instructions │
    │       │ L1I Cache MPKI          │ 0.319 │ misses per 1,000 instructions │
    │       │ L1 Instruction TLB MPKI │ 0.003 │ misses per 1,000 instructions │
    │       │ L2 Cache MPKI           │ 0.025 │ misses per 1,000 instructions │
    │       │ L2 Unified TLB MPKI     │ 0.002 │ misses per 1,000 instructions │
    │       │ LL Cache Read MPKI      │ 0.035 │ misses per 1,000 instructions │
    │       └─────────────────────────┴───────┴───────────────────────────────┘
    └── Miss Ratio (Miss_Ratio)
        └── ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
            ┃ Metric                        ┃ Value ┃ Unit             ┃
            ┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
            │ Branch Misprediction Ratio    │ 0.010 │ per branch       │
            │ DTLB Walk Ratio               │ 0.000 │ per TLB access   │
```

- Restrict counting to specific cores (and pin your workload with taskset):
      topdown-tool -C 0,2-3 -- taskset -c 0,2-3 ./a.out

```sh
$ topdown-tool -C 0,2-3 -- taskset -c 0,2-3 ./a.out
Monitoring command: a.out. Hit Ctrl-C to stop.
Run 1
CPU Neoverse V2 metrics
├── Stage 1 (Topdown metrics)
│   └── Topdown Level 1 (Topdown_L1)
│       └── ┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━┓
│           ┃ Metric          ┃ Aggregated (0,2-3) ┃ #0    ┃ #2    ┃ #3    ┃ Unit ┃
│           ┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━┩
│           │ Backend Bound   │ 73.28              │ 71.97 │ 75.68 │ 73.29 │ %    │
│           │ Bad Speculation │ 0.97               │ 0.84  │ 1.07  │ 1.08  │ %    │
│           │ Frontend Bound  │ 17.45              │ 19.26 │ 14.93 │ 16.59 │ %    │
│           │ Retiring        │ 8.75               │ 8.97  │ 8.57  │ 8.59  │ %    │
│           └─────────────────┴────────────────────┴───────┴───────┴───────┴──────┘
└── Stage 2 (uarch metrics)
```

Core selection behavior when `-C`/`--core` is not specified:
- On homogeneous systems, metrics are attributed to the cores executing your workload/PIDs excepts if core are specified or if capture happens system-wide.
- On heterogeneous systems (mixed core types), capture occurs across all individual cores present (to include all core types).

## SME (Scalable Matrix Extension) metrics

SME metrics are not automatically included. If you want to analyze SME on specific cores, provide an SME telemetry spec and the list of target cores:
```sh
topdown-tool --sme sme.json:0,2-3 -- ./a.out
```

Format:
```sh
--sme <sme-spec.json>:<core-list>
```
Where <core-list> accepts integers and ranges (e.g., `0,2-3`).

You can combine SME with other CPU probe options (metric groups, stages, CSV, etc.).

## CSV output

CSV is the easiest way to post-process both metrics and raw events.

- Specify where to write:
```sh
--csv-output-path <directory>
```
  A timestamped subdirectory (YYYY_MM_DD_HH_MM_SS) is created automatically.
- Enable CSV for metrics and/or events:
```sh
--cpu-generate-csv metrics[,events]
```
- Optional: sample periodically with:
```sh
-I 1000
```
  (The “time” column in CSV is populated only when an interval is used.)

Examples:
- Metrics every 1000 ms:
```sh
topdown-tool --cpu-generate-csv metrics --csv-output-path out -I 1000 -- sleep 5
```
- Events (one-shot, no interval):
```sh
topdown-tool --cpu-generate-csv events --csv-output-path out -- sleep 2
```
- Both metrics and events:
```sh
topdown-tool --cpu-generate-csv metrics,events --csv-output-path out -- ./a.out
```

### CSV output organization

Files are written under:
```sh
<csv-output-path>/<YYYY_MM_DD_HH_MM_SS>/cpu/
```

When no specific cores are selected (`-C` not used), you’ll see product-level files:
```
<product>_metrics.csv
<product>_events.csv
```

When specific cores are selected (`-C` used), you’ll see per-core files and an aggregate over the selected cores. For example:
```
<product>_core_0_metrics.csv
<product>_core_0_events.csv
<product>_core_1_metrics.csv
<product>_core_1_events.csv
<product>_core_2_metrics.csv
<product>_core_2_events.csv
<product>_core_3_metrics.csv
<product>_core_3_events.csv
<product>_core_4_metrics.csv
<product>_core_4_events.csv
<product>_core_aggregate_(0-4)_metrics.csv
<product>_core_aggregate_(0-4)_events.csv
```

Notes:
- <product> is the CPU product name lowercased with spaces and dashes replaced by underscores.
- Aggregate files use ranges like “(0-4)”.

### Metrics CSV format

Each metrics CSV has:
- time: Timestamp in seconds when `-I`/`--interval` is used; blank otherwise.
- group: Name of the metric group.
- stage: “1” (Topdown) or “2” (uarch).
- level: For stage 1, the 1-based depth in the top-down tree for the metric; blank otherwise.
- metric: Metric name.
- value: Numeric value for that metric; blank when not available.
- units: Units string from the specification (e.g., “%”, “per cycle”, “misses per 1,000 instructions”).

Example (interval mode; times shown):
```
time,group,stage,level,metric,value,units
0.10,Topdown_L1,1,1,Retiring,17.02,%
0.10,Topdown_L1,1,1,Frontend Bound,37.11,%
```

### Events CSV format

Each events CSV has:
- run: The sequential run index when events are captured across multiple runs (e.g., when multiplexing is disabled and multiple runs are required). If a single run suffices, the value is 1.
- time: Timestamp in seconds when `-I`/`--interval` is used; blank otherwise.
- event: PMU event name (as requested).
- value: Numeric value; blank for “not counted/unsupported”.

Example (no interval; blank time):
```
run,time,event,value
1,,instructions,800440
1,,branch-misses,1234
```
