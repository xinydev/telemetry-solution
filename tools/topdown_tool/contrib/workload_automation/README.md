# Workload Automation Plugin

This directory provides an optional Workload Automation (WA) instrument that wraps
`topdown-tool` so it can be launched as part of WA runs.

## Contents

- `plugins/instruments/topdown.py` – WA instrument implementation. The
  instrument launches `topdown-tool` in a background thread, captures CSV
  output, and registers the generated files as WA artifacts.

## Installation

1. Ensure `topdown-tool` is installed in the same Python environment as
   Workload Automation so `import topdown_tool` works inside WA.

2. Copy the instrument into one of WA's plugin search locations.
   The simplest option is the per-user plugin directory:

   ```bash
   mkdir -p ~/.workload_automation/plugins/instruments
   cp topdown_tool/contrib/workload_automation/plugins/instruments/topdown.py \
      ~/.workload_automation/plugins/instruments/
   ```

   Alternatively, you can drop the instrument into an existing WA source tree
   under `wa/instruments/` if you manage WA from source control.

3. Verify the instrument is visible to WA:

   ```bash
   wa list instruments | grep topdown
   ```

   The listing should show `topdown` with the description
   "Runs topdown-tool with structured CPU/perf configuration and CSV export."

## Usage

Declare the instrument in your WA agenda and provide the structured CPU/perf
configuration you would otherwise pass on the CLI. Example snippet:

```yaml
config:
  instruments:
    - topdown

  topdown:
    cpu_config:
      spec_overrides:
        - "/path/to/metrics/neoverse-n1.json"
      metric_group: ["frontend"]
      stages: [1, 2]
      generate_csv: ["metrics"]
      # Leave generate_csv/dump_events empty if you want CLI summaries only.
    perf_config:
      perf_path: "/usr/bin/perf"

workloads:
  - name: dhrystone
```

When the run completes WA will place the generated CSV files under the job's
output directory (e.g. `wa_output/<job-name>/topdown_output/`) whenever
`generate_csv` is specified. Leave both `generate_csv` and `dump_events`
unset to retain CLI summaries in WA logs; otherwise CSV/event dumping takes
priority. Additional options such as `metric_group`, `stages`, `collect_by`,
and `dump_events` can be set under `cpu_config`.

### Configuration reference

- `cpu_config` accepts the following keys:
  - `spec_overrides`: list of telemetry JSON files to use instead of auto-detect.
  - `sme_overrides`: list of `{path, cores}` mappings (or `(path, cores)` tuples) for SME specs.
  - `core_filter`: list of core indices to capture.
  - `dump_events`: truthy value to dump raw events alongside metrics (suppresses CLI output once set).
  - `generate_csv`: list containing any subset of `["metrics", "events"]` to enable CSV generation.
  - `collect_by`: one of `none`, `metric`, or `group`.
  - `metric_group`: list of metric-group names to restrict capture.
  - `stages`: list containing any combination of `1` and `2`.
- `perf_config` supports `perf_path` and `perf_args` for overriding WA's perf binary/arguments.

When neither `generate_csv` nor `dump_events` is specified, the plugin leaves CLI rendering enabled
so WA logs show the topdown tables. Once either option is present, CSV/event dumping takes priority
and the CLI tables are suppressed, mirroring the standalone tool behavior.

If you need to tweak the instrument behaviour, edit the file inside the plugin
directory and rerun your WA agenda.
