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
from dataclasses import dataclass
from io import BytesIO
from typing import Tuple

import pyarrow.csv as pc
import pyarrow.parquet as pq
import spe_parser
import spe_parser.errors as err
import spe_parser.payload as payload
from pandas import DataFrame
from spe_parser.perf_decoder import get_spe_records_regions
from spe_parser.schema import BRANCH_COLS, LDST_COLS, OTHER_COLS, get_schema_renderer
from spe_parser.spe_decoder import get_packets
from spe_parser.symbols import init_search_symbols, search_symbols_by_addr_batch

RE_CPU = re.compile(r"\bcpu:\s+(\d+)")


def args_init():
    parser = argparse.ArgumentParser(
        description="Parse SPE metrics from perf records",
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
    parser.add_argument(
        "--noother",
        dest="parse_other",
        action="store_false",
        default=True,
        help="disable Other instructions parsing",
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
        "-s",
        "--symbols",
        dest="parse_symbols",
        action="store_true",
        default=False,
        help="add symbol information to the output",
    )
    parser.add_argument(
        "-r",
        "--raw-buffer",
        dest="parse_raw",
        action="store_true",
        default=False,
        help="parse input file in raw SPE fill buffer format",
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
        help="Show this help message and output file schema",
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


@dataclass
class RegionTaskParams:
    file_path: str
    parse_br: bool
    parse_ldst: bool
    parse_other: bool
    parse_symbols: bool
    parse_raw: bool
    region: dict
    idx: int
    temp_folder: str
    concurrency: int


def parse_single_region(task: RegionTaskParams) -> None:
    branch_recs = []
    ldst_recs = []
    other_recs = []

    with open(task.file_path, "rb") as f:
        f.seek(task.region["offset"])
        spe_f = BytesIO(f.read(task.region["size"]))
        cpu = task.region["cpu"]
        unknown_rec = None
        unknown_rec_cnt = 0
        record_dict = {}
        for pkt in get_packets(spe_f):
            pkt_type, *pkt_value = pkt.split(" ")
            if pkt_type == "LAT":
                # two examples of LAT packets:
                # LAT 1 XLAT
                # LAT 627 ISSUE
                if len(pkt_value) != 2:
                    logging.warning(f"invalid LAT packet: {pkt}")
                else:
                    lat_type, lat_cnt = pkt_value[1], pkt_value[0]
                    record_dict[lat_type] = [lat_cnt]
                continue
            record_dict[pkt_type] = pkt_value
            if pkt_type == "TS" or pkt_type == "END":
                # Each SPE record is terminated by a TS or END packet, so reaching
                # this point indicates that all packets of a complete
                # record have been obtained.
                rec = payload.create_record(record_dict, cpu)
                record_dict = {}
                if rec.type == payload.RecordType.BRANCH:
                    # branch
                    if task.parse_br:
                        branch_recs.append(rec.to_dict())
                elif (
                    rec.type == payload.RecordType.LOAD
                    or rec.type == payload.RecordType.STORE
                ):
                    # ldst
                    if task.parse_ldst:
                        ldst_recs.append(rec.to_dict())
                elif rec.type == payload.RecordType.OTHER:
                    # other
                    if task.parse_other:
                        other_recs.append(rec.to_dict())
                else:
                    # unknown(packet due to parsing error)
                    unknown_rec = rec
                    unknown_rec_cnt += 1

        if unknown_rec:
            logging.debug(
                f"unknown record count: {unknown_rec_cnt}, last unknown record: {unknown_rec}"
            )
        logging.debug(
            f"extracted {len(branch_recs)}(branch)+{len(ldst_recs)}(ldst) records from cpu:{task.region['cpu']}"
        )

    def store_records(parse_records, records, default_cols, name):
        if not parse_records:
            logging.info(f"skip {name} records")
            return
        if not records:
            logging.info(f"no {name} records found")
            return

        df = DataFrame.from_records(
            records,
            columns=default_cols,
        )
        if task.parse_symbols:
            df["symbol"] = search_symbols_by_addr_batch(
                task.file_path,
                df["pc"].apply(int, base=16).tolist(),
                task.concurrency,
            )
        df.to_parquet(
            os.path.join(task.temp_folder, f"{task.idx}-{name}.parquet"),
            index=False,
            engine="pyarrow",
        )

    store_records(task.parse_ldst, ldst_recs, LDST_COLS, "ldst")
    store_records(task.parse_br, branch_recs, BRANCH_COLS, "br")
    store_records(task.parse_other, other_recs, OTHER_COLS, "other")


def parse(
    file_path: str,
    parse_br: bool,
    parse_ldst: bool,
    parse_other: bool,
    parse_symbols: bool,
    parse_raw: bool,
    concurrency: int,
) -> Tuple[int, str]:
    """
    parse() function is used to parse the perf.data and generate a lots
    of parquet files, each of which contains the formatted SPE records

    Args:
        file_path (str): perf.data file path
        parse_br (bool): whether to parse branch instructions
        parse_ldst (bool): whether to parse load/store instructions
        parse_other (bool): whether to parse other instructions
        parse_symbols (bool): whether to add symbol information to the output
        parse_raw (bool): whether to parse raw SPE fill buffer input
        concurrency (int): number of threads used

    Returns:
        Tuple[int, str]: number of SPE records and temporary folder path
        for intermediate files(parquet)
    """
    if parse_symbols:
        # cache all the data structures used for symbol search in main process
        # for better performance
        init_search_symbols(file_path, concurrency)

    # pre-creating a pool reduces the amount of memory that needs to be
    # copied from the parent process in the child processes
    pool = multiprocessing.Pool(concurrency)

    regions = []
    if parse_raw:
        # Load raw buffer from file where whole file content is the buffer.
        # File payload contains only SPE fill buffer records composed of packets.
        regions.append({"offset": 0, "size": os.stat(file_path).st_size, "cpu": 0})
    else:
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
        for _ in pool.imap_unordered(
            parse_single_region,
            [
                RegionTaskParams(
                    file_path=file_path,
                    parse_br=parse_br,
                    parse_ldst=parse_ldst,
                    parse_other=parse_other,
                    parse_symbols=parse_symbols,
                    parse_raw=parse_raw,
                    region=region,
                    idx=idx,
                    temp_folder=inter_files_dir,
                    concurrency=concurrency,
                )
                for idx, region in enumerate(regions)
            ],
            chunksize=int(len(regions) / concurrency) + 1,
        ):
            # try to get the exceptions of child processes if any
            pass
    except Exception as ex:
        logging.error(f"failed to parse SPE trace file: {ex}")
        shutil.rmtree(inter_files_dir)
        raise err.ParseRegionError(ex)
    finally:
        pool.close()
        pool.join()
    return len(regions), inter_files_dir


def merge_write(
    region_cnt: int,
    inter_files_dir: str,
    ouput_prefix: str,
    format: str,
    parse_br: bool,
    parse_ldst: bool,
    parse_other: bool,
):
    def write_file(file_type, writer_func):
        source_files = [
            os.path.join(inter_files_dir, f"{idx}-{file_type}.parquet")
            for idx in range(region_cnt)
        ]
        valid_files = [f for f in source_files if os.path.exists(f)]
        if not valid_files:
            logging.warning(f"No {file_type} records found")
            logging.warning(
                "Please check if related SPE events are enabled in perf record"
            )
            return
        logging.debug(f"Total records files count: {len(valid_files)}")

        output_file_name = f"{ouput_prefix}-{file_type}.{format}"
        logging.info(f"Generating {format} file: {output_file_name}")
        with writer_func(
            output_file_name, schema=pq.ParquetFile(valid_files[0]).schema_arrow
        ) as writer:
            for src in valid_files:
                writer.write_table(pq.read_table(src))
        logging.info(f"SPE {file_type} trace files created successfully")

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
    if parse_other:
        write_file("other", writer_func)


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
        args.file,
        args.parse_br,
        args.parse_ldst,
        args.parse_other,
        args.parse_symbols,
        args.parse_raw,
        args.concurrency,
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
            args.parse_other,
        )
    finally:
        shutil.rmtree(inter_files_dir)
