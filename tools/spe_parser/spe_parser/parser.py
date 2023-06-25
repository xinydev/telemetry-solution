# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

import argparse
import gc
import logging
import multiprocessing
import os
import re
import shutil
import sys
from io import BytesIO
from typing import Tuple

import pyarrow.csv as pc
import pyarrow.parquet as pq
import spe_parser
import spe_parser.errors as err
import spe_parser.payload as payload
from pandas import DataFrame
from spe_parser.perf_decoder import get_spe_records_regions
from spe_parser.schema import BRANCH_COLS, LDST_COLS, get_schema_renderer
from spe_parser.spe_decoder import get_packets

RE_CPU = re.compile(r"\bcpu:\s+(\d+)")


def args_init():
    parser = argparse.ArgumentParser(
        description="Parse SPE metrics from perf records.",
        add_help=False,
    )

    parser.add_argument(
        "file",
        nargs="?",
        help="perf.data file from 'perf record'",
    )
    parser.add_argument(
        "-p",
        "--prefix",
        dest="prefix",
        default="spe",
        help="file prefix for output parquet file",
    )
    parser.add_argument(
        "-t",
        "--type",
        dest="output_type",
        choices=["csv", "parquet"],
        default="parquet",
        help="output file format type, default to parquet",
    )
    parser.add_argument(
        "-d",
        "--debug",
        dest="debug",
        action="store_true",
        default=False,
        help="enable debug output",
    )
    parser.add_argument(
        "--noldst",
        dest="parse_ldst",
        action="store_false",
        default=True,
        help="disable LDST instructions parsing",
    )
    parser.add_argument(
        "--nobr",
        dest="parse_br",
        action="store_false",
        default=True,
        help="disable Branch instructions parsing",
    )
    cpu_cnt = multiprocessing.cpu_count()
    parser.add_argument(
        "-c",
        "--concurrency",
        dest="concurrency",
        default=cpu_cnt,
        type=int,
        help=f"number of threads used, defaults to {cpu_cnt}",
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version="%(prog)s " + spe_parser.__version__,
    )
    parser.add_argument(
        "-h",
        "--help",
        dest="help",
        action="store_true",
        default=False,
        help="Show this help message and output file schema.",
    )

    try:
        args = parser.parse_args()

        if args.help:
            renderer = get_schema_renderer()
            renderer.render(parser.format_help())
            sys.exit(0)
        if args.file is None:
            logging.error("perf.data file is not specified")
            parser.print_help()
            sys.exit(1)
        return args
    except BaseException:
        sys.exit(1)


def parse_single_region(task: Tuple) -> None:
    branch_recs = []
    ldst_recs = []
    file_path, parse_br, parse_ldst, region, idx, temp_folder = task
    with open(file_path, "rb") as f:
        f.seek(region["offset"])
        spe_f = BytesIO(f.read(region["size"]))
        cpu = region["cpu"]
        rec = payload.RecordPayload()
        for pkt in get_packets(spe_f):
            tokens = pkt.split(" ")
            if tokens[0] == "LAT":
                if len(tokens) != 3:
                    logging.warning(f"invalid LAT packet: {tokens}")
                else:
                    rec.add_data(tokens[2], [tokens[1]])
                continue
            rec.add_data(tokens[0], list(tokens[1:]))
            if tokens[0] == "TS":
                # Each SPE record is terminated by a TS packet, so reaching
                # this point indicates that all packets of a complete
                # record have been obtained.
                if rec.get_type() == payload.RecordType.UNKNOWN:
                    logging.error(f"invalid auxtrace record: {rec}")
                    continue
                elif rec.get_type() == payload.RecordType.BRANCH:
                    # branch
                    if parse_br:
                        branch_recs.append(rec.to_branch(cpu))
                else:
                    if parse_ldst:
                        ldst_recs.append(rec.to_load_store(cpu))
                rec = payload.RecordPayload()
        logging.debug(f"record: {rec}")
        logging.debug(
            f"extracted {len(branch_recs)}(branch)+{len(ldst_recs)}(ldst) records from cpu:{region['cpu']}"
        )

    if parse_ldst and len(ldst_recs) > 0:
        ldst_df = DataFrame.from_records(
            ldst_recs,
            columns=LDST_COLS,
        )
        ldst_df.to_parquet(
            os.path.join(temp_folder, f"{idx}-ldst.parquet"),
            index=False,
            engine="pyarrow",
        )

    if parse_br and len(branch_recs) > 0:
        br_df = DataFrame.from_records(
            branch_recs,
            columns=BRANCH_COLS,
        )
        br_df.to_parquet(
            os.path.join(temp_folder, f"{idx}-br.parquet"),
            index=False,
            engine="pyarrow",
        )


def parse(
    file_path: str, parse_br: bool, parse_ldst: bool, concurrency: int
) -> Tuple[int, str]:
    """
    parse() function is used to parse the perf.data and generate a lots
    of parquet files, each of which contains the formatted SPE records

    Args:
        file_path (str): perf.data file path
        parse_br (bool): whether to parse branch instructions
        parse_ldst (bool): whether to parse load/store instructions
        concurrency (int): number of threads used

    Returns:
        Tuple[int, str]: number of SPE records and temporary folder path
        for intermediate files(parquet)
    """
    # pre-creating a pool reduces the amount of memory that needs to be
    # copied from the parent process in the child processes
    pool = multiprocessing.Pool(concurrency)

    # SPE records are scattered across different locations in the
    # perf.data file, so each region can be processed in parallel.
    regions = get_spe_records_regions(file_path)
    # force garbage collection as we can be sure that a lot of
    # temporary memory was used in get_spe_records_regions(), there
    # will definitely be a significant amount of memory to be reclaimed here.
    gc.collect()

    logging.debug(f"SPE regions: {len(regions)}")
    # temporary folder used for storing intermediate files
    # remove the folder if the directory exists, as it might contain
    # files from a previous run (potentially due to a crash)
    inter_files_dir = f".spe-parser-temp-output-{os.getpid()}"
    if os.path.exists(inter_files_dir):
        shutil.rmtree(inter_files_dir)
    os.makedirs(inter_files_dir)

    # The results can be processed in a random order because the files
    # generated by the child processes contain the order of the regions.
    # The subsequent merge_write() will merge them in order
    # In fact, processing them in a random order can be more efficient
    # and reduce the overhead needed to ensure the order.
    try:
        pool.imap_unordered(
            parse_single_region,
            [
                (file_path, parse_br, parse_ldst, region, idx, inter_files_dir)
                for idx, region in enumerate(regions)
            ],
            chunksize=int(len(regions) / concurrency) + 1,
        )
        pool.close()
        pool.join()
    except Exception as ex:
        shutil.rmtree(inter_files_dir)
        logging.error(f"failed to parse SPE trace file: {ex}")
        raise err.ParseRegionError(ex)

    return len(regions), inter_files_dir


def merge_write(
    region_cnt: int,
    inter_files_dir: str,
    ouput_prefix: str,
    format: str,
    parse_br: bool,
    parse_ldst: bool,
):
    def write_file(file_type, writer_func):
        source_files = [
            os.path.join(inter_files_dir, f"{idx}-{file_type}.parquet")
            for idx in range(region_cnt)
        ]
        if not os.path.exists(source_files[0]):
            logging.warning(f"no {file_type} records found")
            return

        output_file_name = f"{ouput_prefix}-{file_type}.{format}"
        logging.info(f"Generating {format} file: {output_file_name}")
        with writer_func(
            output_file_name, schema=pq.ParquetFile(source_files[0]).schema_arrow
        ) as writer:
            for src in source_files:
                if not os.path.exists(src):
                    continue
                writer.write_table(pq.read_table(src))

    def writer_parquet(file_name, schema):
        return pq.ParquetWriter(file_name, schema=schema, compression="gzip")

    def writer_csv(file_name, schema):
        return pc.CSVWriter(
            file_name,
            schema=schema,
            write_options=pc.WriteOptions(quoting_style="none"),
        )

    writer_func = writer_parquet if format == "parquet" else writer_csv

    if parse_br:
        write_file("br", writer_func)
    if parse_ldst:
        write_file("ldst", writer_func)


def init_logging(debug: bool):
    level = logging.INFO
    if debug:
        level = logging.DEBUG
    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )


def main():
    args = args_init()
    init_logging(args.debug)
    logging.info(f"Processing SPE trace file: {args.file}")

    region_cnt, inter_files_dir = parse(
        args.file, args.parse_br, args.parse_ldst, args.concurrency
    )
    logging.info("SPE trace file processing is completed")

    try:
        merge_write(
            region_cnt,
            inter_files_dir,
            args.prefix,
            args.output_type,
            args.parse_br,
            args.parse_ldst,
        )
    finally:
        shutil.rmtree(inter_files_dir)
    logging.info("SPE trace files created successfully")
