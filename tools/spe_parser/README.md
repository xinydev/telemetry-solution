# spe-parser

The `spe-parser` tool parses SPE (Statistical Profiling Extension) raw data and generates Parquet or CSV files for further processing and analysis.

For guidance on performance analysis with SPE, refer to the [Arm Statistical Profiling Extension: Performance Analysis Methodology White Paper](https://developer.arm.com/documentation/109429/latest/)

For an introduction to Statistical Profiling Extension, refer to the [Arm Architecture Reference Manual for A-profile architecture](https://developer.arm.com/documentation/ddi0487/latest/)

## Installation

Ensure you have the following prerequisites before installing the `spe-parser` tool:

- pip version 18.0.0 or higher (Use `pip install --upgrade pip` to update pip)
- python version 3.8 or higher

To install `spe-parser`:

```bash
pip install .
```

For development mode installation, your pip version should be v21.3 or higher:

```bash
pip install -e .
```

## Usage

To record SPE performance data:

```bash
perf record -e arm_spe_0/branch_filter=1,ts_enable=1,load_filter=1,jitter=1,store_filter=1,min_latency=0/ -- test_program
```

For more options and a detailed introduction, please refer to the  [Arm Statistical Profiling Extension: Performance Analysis Methodology White Paper](https://developer.arm.com/documentation/109429/latest/)

---
To parse the `perf` binary data and output in Parquet format:

```bash
spe-parser perf.data
```

---

To obtain output in CSV format:

```bash
spe-parser perf.data -t csv
```

---

To modify the output files prefix to `record1`:

```bash
spe-parser perf.data -t csv -p record1
```

This will result in the creation of three files: record1-ldst.csv, record1-br.csv, and record1-other.csv, corresponding respectively to Load/Store, Branch, and Other SPE packets.

---

The `spe-parser` will by default parse Load/Store, Branch, and Other SPE packets into three separate files. To disable parsing for a specific packet type, use the options below:

```bash
spe-parser perf.data --noldst --noother --nobr
```

---

To speed up parsing, increase concurrency; to use less resources, decrease it. By default, the system's core count is used. To change it:

```bash
spe-parser perf.data --concurrency 2
```

---

To include symbol information for corresponding instructions in the output files:

```bash
spe-parser perf.data --symbols
```

Please make sure your workload is compiled with debug information (e.g. -g).

---

To parse raw SPE fill buffer data (e.g. generated with [WindowsPerf](https://github.com/arm-developer-tools/windowsperf)), use the options below:

```bash
spe-parser spe.data --raw-buffer
```

Please note that raw SPE buffer doesn't contain additional meta-data perf.data file contains, e.g. no symbol name resolution is possible.

---

To check the `spe-parser` version, which is crucial as the file schema may change between versions, the updates between each version can be found in the [Changelog](CHANGELOG.md):

```bash
spe-parser --version
```

---

For detailed scheme descriptions of output files, column explanations, and possible values and meanings:

```bash
spe-parser --help
```

## Development

Run tests using the following command.

```bash
pip install tox
make test
```

For now, please refrain from using pre-commit to install the .pre-commit-config.yaml configuration, and instead run make lint to check.

```bash
make lint
```
