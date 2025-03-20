# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023
#
# This module is mainly used to parse Arm's architected
# SPE (Statistical Profiling Extension) format, is independent of Linux.
# For detailed information about SPE, please refer to:
#   https://developer.arm.com/documentation/ddi0487/latest/

import logging
from functools import lru_cache
from typing import BinaryIO, Generator, Optional

import spe_parser.errors as err


@lru_cache(maxsize=32)
def gen_mask(high: int, low: int) -> int:
    # generate a bitmask with bits set from low to high
    # e.g., get_mask(4, 2) = 0001_1100
    #   high_mask = 4 -> 0001_1111
    #   low_mask = 2 -> 0000_0011
    #   mask = 0001_1111 & ~0000_0011 = 0001_1100
    high_mask = (1 << (high + 1)) - 1
    low_mask = (1 << low) - 1
    return high_mask & ~low_mask


def lshift(nr: int) -> int:
    return 1 << (nr)


def bytes_to_int(b: bytes) -> int:
    # TODO: Determine big-endian or little-endian format
    # based on perf.data header.
    return int.from_bytes(b, "little")


HEADER_SHORT_PAD = bytes_to_int(b"\x00")
HEADER_SHORT_END = bytes_to_int(b"\x01")
HEADER_SHORT_TIMESTAMP = bytes_to_int(b"\x71")

HEADER_SHORT_MASK1 = gen_mask(7, 6) | gen_mask(3, 0)
HEADER_SHORT_EVENTS = bytes_to_int(b"\x42")
HEADER_SHORT_SOURCE = bytes_to_int(b"\x43")

HEADER_SHORT_MASK2 = gen_mask(7, 2)
HEADER_SHORT_CONTEXT = bytes_to_int(b"\x64")
HEADER_SHORT_OP_TYPE = bytes_to_int(b"\x48")

HEADER_EXTENDED = bytes_to_int(b"\x20")
HEADER_EXTENDED_ALIGNMENT = bytes_to_int(b"\x00")

# The address and counter packets do not require differentiation
# between short and long formats
HEADER_MASK3 = gen_mask(7, 3)
HEADER_ADDRESS = bytes_to_int(b"\xb0")
HEADER_COUNTER = bytes_to_int(b"\x98")


def get_short_header_index(h: int) -> int:
    # 7 == gen_mask(2, 0)
    return h & 7


def get_extended_header_index(h0: int, h1: int) -> int:
    return (h0 & gen_mask(1, 0)) << 3 | get_short_header_index(h1)


# Address packet
PKT_ADDRESS_INDEX_INS = bytes_to_int(b"\x00")
PKT_ADDRESS_INDEX_BRANCH = bytes_to_int(b"\x01")
PKT_ADDRESS_INDEX_DATA_VIRT = bytes_to_int(b"\x02")
PKT_ADDRESS_INDEX_DATA_PHYS = bytes_to_int(b"\x03")
PKT_ADDRESS_INDEX_PREV_BRANCH = bytes_to_int(b"\x04")
PKT_ADDRESS_NAME = {
    PKT_ADDRESS_INDEX_INS: "PC",
    PKT_ADDRESS_INDEX_BRANCH: "TGT",
    PKT_ADDRESS_INDEX_DATA_VIRT: "VA",
    PKT_ADDRESS_INDEX_DATA_PHYS: "PA",
    # Arm SPEv1.2 adds a new optional address packet type: previous branch
    # target. The recorded address is the target virtual address of the most
    # recently taken branch in program order
    PKT_ADDRESS_INDEX_PREV_BRANCH: "PBT",
}


def get_pkt_address_addr(v: int) -> int:
    return v & gen_mask(55, 0)


def get_pkt_address_ns(v: int) -> int:
    return int(bool((v & lshift(63)) >> 63))


def get_pkt_address_el(v: int) -> int:
    return (v & gen_mask(62, 61)) >> 61


def get_pkt_address_ch(v: int) -> int:
    return int(bool(v & lshift(62)) >> 62)


def get_pkt_address_pat(v: int) -> int:
    return (v & gen_mask(59, 56)) >> 56


# Context packet
def get_pkt_context_index(h: int) -> int:
    return h & gen_mask(1, 0)


# Counter packet
PKT_COUNTER_TYPE = {
    bytes_to_int(b"\x00"): "TOT",
    bytes_to_int(b"\x01"): "ISSUE",
    bytes_to_int(b"\x02"): "XLAT",
}

# Events Packet
PKT_EVENTS_TYPE = {
    lshift(0): "EXCEPTION-GEN",
    lshift(1): "RETIRED",
    lshift(2): "L1D-ACCESS",
    lshift(3): "L1D-REFILL",
    lshift(4): "TLB-ACCESS",
    lshift(5): "TLB-REFILL",  # EV_TLB_WALK
    lshift(6): "NOT-TAKEN",
    lshift(7): "MISPRED",
    lshift(8): "LLC-ACCESS",
    lshift(9): "LLC-REFILL",  # EV_LLC_MISS
    lshift(10): "REMOTE-ACCESS",
    lshift(11): "ALIGNMENT",
    lshift(12): "LATE-PREFETCH",
    lshift(17): "SVE-PARTIAL-PRED",  # EV_PARTIAL_PREDICATE
    lshift(18): "SVE-EMPTY-PRED",  # EV_EMPTY_PREDICATE
}


# Operation packet header
def get_pkt_operation_index(h: int) -> int:
    return h & gen_mask(1, 0)


PKT_OPERATION_CLASS_OTHER = bytes_to_int(b"\x00")
PKT_OPERATION_CLASS_LD_ST_ATOMIC = bytes_to_int(b"\x01")
PKT_OPERATION_CLASS_BR_ERET = bytes_to_int(b"\x02")


def pkt_operation_class_sve_other(v: int) -> int:
    # 0b0000000x: Other operation
    # 0b0xxx1xx0: SVE operation
    return (v & 0b10001001) == 0x8


PKT_OPERATION_COND = lshift(0)


def get_pkt_operation_ldst_subclass(v: int) -> int:
    return v & gen_mask(7, 1)


PKT_OPERATION_LDST_GP_REG = bytes_to_int(b"\x00")
PKT_OPERATION_LDST_SIMD_FP = bytes_to_int(b"\x04")
PKT_OPERATION_LDST_UNSPEC_REG = bytes_to_int(b"\x10")
PKT_OPERATION_LDST_NV_SYSREG = bytes_to_int(b"\x30")
PKT_OPERATION_LDST_MTE_TAG = bytes_to_int(b"\x14")
PKT_OPERATION_LDST_MEMCPY = bytes_to_int(b"\x20")
PKT_OPERATION_LDST_MEMSET = bytes_to_int(b"\x25")


def is_pkt_operation_ldst_atomic(v: int) -> bool:
    return (v & (gen_mask(7, 5) | lshift(1))) == 2


PKT_OPERATION_SG = lshift(7)  # Gather/scatter load/store
PKT_OPERATION_AR = lshift(4)  # Acquire/Release
PKT_OPERATION_EXCL = lshift(3)  # Exclusive
PKT_OPERATION_AT = lshift(2)  # Atomic load/store
PKT_OPERATION_ST = lshift(0)  # Store/Load


def is_pkt_operation_ldst_sve(v: int) -> bool:
    return (v & (lshift(3) | lshift(1))) == 8


def PKT_OPERATION_SVE_EVL(v: int) -> int:
    return 32 << ((v & gen_mask(6, 4)) >> 4)


PKT_OPERATION_SVE_PRED = lshift(2)
PKT_OPERATION_SVE_FP = lshift(1)


def is_pkt_operation_indirect_branch(v: int) -> int:
    return (v & gen_mask(7, 1)) == 2


def spe_get_end(hdr: int, fh: BinaryIO) -> str:
    """End packet characteristics are:
    - Defines the end of a record if a Timestamp packet is not present.
    - 8-bit packet (on payload).
    """
    return "END"


def spe_get_timestamp(hdr: int, fh: BinaryIO) -> str:
    payload = spe_get_payload(hdr, None, fh)
    return f"TS {payload}"


def spe_get_events(hdr: int, fh: BinaryIO) -> str:
    payload = spe_get_payload(hdr, None, fh)
    events = []
    for k in PKT_EVENTS_TYPE:
        if payload & k:
            events.append(PKT_EVENTS_TYPE[k])
    return f"EV {' '.join(events)}"


def spe_get_source(hdr: int, fh: BinaryIO) -> str:
    payload = spe_get_payload(hdr, None, fh)
    return f"DATA-SOURCE {payload}"


def spe_get_context(hdr: int, fh: BinaryIO) -> str:
    index = get_pkt_context_index(hdr)
    payload = spe_get_payload(hdr, None, fh)
    return f"CONTEXT {hex(payload)} el{index+1}"


def spe_get_op_type(hdr: int, fh: BinaryIO) -> str:
    index = get_pkt_operation_index(hdr)
    payload = spe_get_payload(hdr, None, fh)
    ops = []
    if index == PKT_OPERATION_CLASS_OTHER:
        if pkt_operation_class_sve_other(payload):
            ops.append("SVE-OTHER")
            # SVE effective vector length
            ops.append(f"EVLEN {PKT_OPERATION_SVE_EVL(payload)}")
            if payload & PKT_OPERATION_SVE_FP:
                ops.append("FP")
            if payload & PKT_OPERATION_SVE_PRED:
                ops.append("PRED")
        else:
            ops.append("OTHER")
            if payload & PKT_OPERATION_COND:
                ops.append("COND-SELECT")
            else:
                ops.append("INSN-OTHER")
    elif index == PKT_OPERATION_CLASS_LD_ST_ATOMIC:
        if payload & 1:
            ops.append("ST")
        else:
            ops.append("LD")
        if is_pkt_operation_ldst_atomic(payload):
            if payload & PKT_OPERATION_AT:
                ops.append("AT")
            if payload & PKT_OPERATION_EXCL:
                ops.append("EXCL")
            if payload & PKT_OPERATION_AR:
                ops.append("AR")
        subclass = get_pkt_operation_ldst_subclass(payload)
        if subclass == PKT_OPERATION_LDST_SIMD_FP:
            ops.append("SIMD-FP")
        elif subclass == PKT_OPERATION_LDST_GP_REG:
            ops.append("GP-REG")
        elif subclass == PKT_OPERATION_LDST_UNSPEC_REG:
            ops.append("UNSPEC-REG")
        elif subclass == PKT_OPERATION_LDST_NV_SYSREG:
            ops.append("NV-SYSREG")
        elif subclass == PKT_OPERATION_LDST_MTE_TAG:
            ops.append("MTE-TAG")
        elif subclass == PKT_OPERATION_LDST_MEMCPY:
            ops.append("MEMCPY")
        elif subclass == PKT_OPERATION_LDST_MEMSET:
            ops.append("MEMSET")
        if is_pkt_operation_ldst_sve(payload):
            # SVE effective vector length
            ops.append(f"EVLEN {PKT_OPERATION_SVE_EVL(payload)}")
            if payload & PKT_OPERATION_SVE_PRED:
                ops.append("PRED")
            if payload & PKT_OPERATION_SG:
                ops.append("SG")
    elif index == PKT_OPERATION_CLASS_BR_ERET:
        ops.append("B")
        if payload & PKT_OPERATION_COND:
            ops.append("COND")
        if is_pkt_operation_indirect_branch(payload):
            ops.append("IND")
    return f"{' '.join(ops)}"


def spe_get_addr(hdr: int, ext_hdr: Optional[int], fh: BinaryIO) -> str:
    if ext_hdr:
        index = get_extended_header_index(hdr, ext_hdr)
    else:
        index = get_short_header_index(hdr)
    payload = spe_get_payload(hdr, ext_hdr, fh)
    ns = get_pkt_address_ns(payload)
    el = get_pkt_address_el(payload)
    ch = get_pkt_address_ch(payload)
    pat = get_pkt_address_pat(payload)
    addr = get_pkt_address_addr(payload)
    if index in (
        PKT_ADDRESS_INDEX_INS,
        PKT_ADDRESS_INDEX_BRANCH,
        PKT_ADDRESS_INDEX_PREV_BRANCH,
    ):
        return f"{PKT_ADDRESS_NAME[index]} {hex(addr)} el{el} ns={ns}"
    elif index == PKT_ADDRESS_INDEX_DATA_VIRT:
        return f"VA {hex(payload)}"
    elif index == PKT_ADDRESS_INDEX_DATA_PHYS:
        return f"PA {hex(addr)} ns={ns} ch={ch} pat={pat}"
    raise err.InvalidAddrPacket()


def spe_get_counter(hdr: int, ext_hdr: Optional[int], fh: BinaryIO) -> str:
    if ext_hdr:
        index = get_extended_header_index(hdr, ext_hdr)
    else:
        index = get_short_header_index(hdr)
    payload = spe_get_payload(hdr, ext_hdr, fh)
    return f"LAT {payload} {PKT_COUNTER_TYPE[index]}"


def spe_get_payload(hdr: int, ext_hdr: Optional[int], fh: BinaryIO) -> int:
    if ext_hdr:
        payload_len = 1 << ((ext_hdr & 48) >> 4)
    else:
        payload_len = 1 << ((hdr & 48) >> 4)
    payload = fh.read(payload_len)
    if payload_len == 1:
        return ord(payload)
    else:
        return bytes_to_int(payload)


def get_packets(fh: BinaryIO) -> Generator:
    # Parse all SPE packets from the given file.
    while True:
        buf = fh.read(1)
        if not buf:
            break
        hdr = ord(buf)
        ext_hdr = None
        # We cannot use the if-else structure here because when dealing
        # with the extended header, we may need to enter two conditional statements
        if hdr == HEADER_SHORT_PAD:
            continue
        if hdr == HEADER_SHORT_END:
            yield spe_get_end(hdr, fh)
            continue
        if hdr == HEADER_SHORT_TIMESTAMP:
            yield spe_get_timestamp(hdr, fh)
            continue
        if (hdr & HEADER_SHORT_MASK1) == HEADER_SHORT_EVENTS:
            yield spe_get_events(hdr, fh)
            continue
        if (hdr & HEADER_SHORT_MASK1) == HEADER_SHORT_SOURCE:
            yield spe_get_source(hdr, fh)
            continue
        if (hdr & HEADER_SHORT_MASK2) == HEADER_SHORT_CONTEXT:
            yield spe_get_context(hdr, fh)
            continue
        if (hdr & HEADER_SHORT_MASK2) == HEADER_SHORT_OP_TYPE:
            yield spe_get_op_type(hdr, fh)
            continue
        if (hdr & HEADER_SHORT_MASK2) == HEADER_EXTENDED:
            # 16-bit extended format header
            # need to update the value of HDR and then determine whether
            # it belongs to an extended address or a counter packet.
            ext_hdr = 1
            hdr = ord(fh.read(1))
            if hdr == HEADER_EXTENDED_ALIGNMENT:
                logging.warning(
                    "alignment packet has been removed in Armv8.5. skip for now"
                )
                continue
        # The address and counter packets do not require differentiation
        # between short and long formats
        if (hdr & HEADER_MASK3) == HEADER_ADDRESS:
            yield spe_get_addr(hdr, ext_hdr, fh)
            continue
        if (hdr & HEADER_MASK3) == HEADER_COUNTER:
            yield spe_get_counter(hdr, ext_hdr, fh)
            continue
        raise err.SPEBadPacket()
