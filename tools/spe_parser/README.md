# spe-parser

The spe-parser tool parses SPE raw data and generates a Parquet or CSV file for further processing and analysis.

## Installation

The following prerequisites are required to install and run the spe-parser tool:

pip >= 18.0.0 # run `pip install --upgrade pip` to upgrade pip
python >= 3.7.1

To install spe-parser, run the following command:

```bash
pip install .
```

If you want to install spe-parser in development mode, make sure your pip version is >= 21.3
and run the following command:

```bash
pip install -e .
```

## Usage

```bash
# Record SPE performance data
perf record -e arm_spe_0/branch_filter=1,ts_enable=1,pct_enable=1,pa_enable=1,load_filter=1,jitter=1,store_filter=1,min_latency=0/ -- test_program

# Parse the perf binary data to generate output in Parquet format
spe-parser perf.data

# To get output in CSV format, run the following command
spe-parser perf.data -t csv

# run spe-parser --help to get more information on the available options
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
