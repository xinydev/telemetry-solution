# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023


import logging
import multiprocessing
import os
from bisect import bisect_right
from functools import lru_cache
from typing import Dict, List, Tuple

from elftools.elf.elffile import ELFFile
from spe_parser.perf_decoder import get_mmap_records


def __decode_elf_symbols(
    binary_path: str, base_addr: int
) -> Dict[int, Tuple[str, int]]:
    symbols = {}
    with open(binary_path, "rb") as file:
        elffile = ELFFile(file)
        # Some libraries will have .symtab deleted, so when it cannot be obtained
        # try to read .dynsym instead
        tab = elffile.get_section_by_name(".symtab") or elffile.get_section_by_name(
            ".dynsym"
        )
        if not tab:
            logging.warning(f"symbols: no symbol table found in file {binary_path}")
            return {}
        object = binary_path.split("/")[-1]
        for symbol in tab.iter_symbols():
            # skip non-function and zero-size symbols
            if symbol["st_info"]["type"] != "STT_FUNC":
                continue
            if symbol["st_size"] == 0:
                continue
            # {start_addr: (symbol_name, end_addr), ...}
            # some symbols may have alias, so we need to remove the duplicates
            symbols[base_addr + symbol["st_value"]] = (
                f"[{object}] {symbol.name}",
                base_addr + symbol["st_size"] + symbol["st_value"],
            )

    return symbols


def get_mmap_loaded_symbols(
    perf_path: str, concurrency: int
) -> Dict[int, Tuple[str, int]]:
    records = []
    # filter out the records that we are not interested in
    for rec in get_mmap_records(perf_path):
        binary_path, _ = rec
        if not binary_path.startswith("/"):
            continue
        if binary_path.endswith(".ko"):
            continue
        if not os.path.exists(binary_path):
            logging.debug(f"symbols: {binary_path} file not found")
            continue
        records.append(rec)

    logging.debug(f"symbols: mmap records total counts: {len(records)}")

    pool = multiprocessing.Pool(concurrency)
    symbols = {}
    for s in pool.starmap_async(__decode_elf_symbols, records).get():
        symbols.update(s)
    pool.close()
    pool.join()
    logging.debug(f"symbols: mmap loaded symbols total counts: {len(symbols)}")
    return symbols


def get_kernel_symbols() -> Dict[int, Tuple[str, int]]:
    kallsyms_lines = []
    try:
        with open("/proc/kallsyms") as f:
            kallsyms_lines = f.read().splitlines()
            # if open /proc/kallsyms as non-root user, all the address will be 0
            if kallsyms_lines and kallsyms_lines[-1].startswith("0000000000000000"):
                raise
    except Exception:
        # There are multiple reasons why reading /proc/kallsyms may fail, such as
        # permission issues or kernel compilation options.
        # It is difficult to check each of them individually
        # If an error occurs, users are encouraged to troubleshoot and resolve it on their own
        logging.warning(
            "symbols: failed to read /proc/kallsyms. skip kernel symbols parsing. try to re-run as root or check kernel compilation options"
        )
        return {}

    # some symbols have alias, remove duplicated symbols with same start address
    symbols_dict = {
        int(addr, 16): (sym, typ)
        for addr, typ, sym in (s.split(None, 2) for s in kallsyms_lines)
    }

    # sort by start address
    start_addr_list = sorted(symbols_dict.keys())

    all_symbols = {}
    func_type = {"t", "T", "w", "W"}
    # The symbols in the kernel are contiguous, and the end_addr of the
    # current symbol is one less than the start_addr of the next symbol
    for i in range(len(start_addr_list) - 1):
        start_addr = start_addr_list[i]
        end_addr = start_addr_list[i + 1] - 1
        func_name, typ = symbols_dict[start_addr]

        if typ not in func_type:
            continue
        # {start_addr: (symbol_name, end_addr), ...}
        all_symbols[start_addr] = (
            f"[kernel.kallsyms] {func_name}",
            end_addr,
        )

    logging.debug(f"symbols: kernel symbols total counts: {len(all_symbols)}")

    return all_symbols


@lru_cache(maxsize=2)
def init_search_symbols(
    perf_path: str, concurrency: int
) -> Tuple[List[int], Dict[int, Tuple[str, int]]]:
    # prepare and cache all the data structures used for bisect for better performance
    logging.info("symbols: start to parse all symbols")
    # {start_addr: (symbol_name, end_addr), ...}
    symbols = get_mmap_loaded_symbols(perf_path, concurrency)
    kernel_symbols = get_kernel_symbols()
    if kernel_symbols:
        symbols.update(kernel_symbols)
    logging.info(f"symbols: total counts: {len(symbols)}")
    return (
        sorted(symbols.keys()),
        symbols,
    )


def search_symbols_by_addr_batch(
    perf_path: str, addr_list: List[int], concurrency: int
) -> List[str]:
    start_addr_list, all_symbols = init_search_symbols(perf_path, concurrency)

    addr_symbol_map = {}
    for addr in set(addr_list):
        # binary search to find the closest start address
        tgt = bisect_right(start_addr_list, addr)
        if tgt == 0:
            continue
        # bisect_right returns the rightmost index to insert, so we need to minus 1
        start_addr = start_addr_list[tgt - 1]
        symbol_name, end_addr = all_symbols[start_addr]
        if start_addr <= addr <= end_addr:
            addr_symbol_map[addr] = symbol_name

    symbols = []
    for addr in addr_list:
        symbols.append(addr_symbol_map.get(addr, ""))
    return symbols
