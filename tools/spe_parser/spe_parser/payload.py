# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

import logging
from enum import Enum
from typing import Dict, List, Optional

import spe_parser.errors as err
from spe_parser.schema import get_branch_default_record, get_ldst_default_record


class RecordType(Enum):
    LOAD = 0
    STORE = 1
    BRANCH = 2
    UNKNOWN = 3


class RecordPayload:
    def __init__(self) -> None:
        self.__data: Dict[str, List[str]] = {}
        self.__record_type: Optional[RecordType] = None

    def add_data(self, name: str, tokens: List[str]) -> None:
        self.__data[name] = tokens

    def get_type(self) -> Optional[RecordType]:
        if self.__record_type is None:
            self.__update_type()

        return self.__record_type

    def __str__(self) -> str:
        return str(self.__data)

    def __update_type(self) -> None:
        for k in self.__data:
            if k == "LD":
                self.__record_type = RecordType.LOAD
                return
            elif k == "ST":
                self.__record_type = RecordType.STORE
                return
            elif k == "B":
                self.__record_type = RecordType.BRANCH
                return
        self.__record_type = RecordType.UNKNOWN

    def to_load_store(self, cpu: int) -> dict:
        if self.get_type() != RecordType.LOAD and self.get_type() != RecordType.STORE:
            raise err.InvalidRecordType(self.get_type())

        record = get_ldst_default_record()
        record["cpu"] = cpu

        for k, v in self.__data.items():
            if k == "DATA-SOURCE":
                record["data_source"] = translate_data_source(v)
            elif k == "EV":
                record["event"] = ":".join(v)
            elif k == "ISSUE":
                record["issue_lat"] = int(v[0])
            elif k == "TOT":
                record["total_lat"] = int(v[0])
            elif k == "XLAT":
                record["xlat_lat"] = int(v[0])
            elif k == "PA":
                record["paddr"] = v[0]
            elif k == "PC":
                # v : 0xffffab47fdb0 el0 ns=1
                record["pc"] = v[0]
                record["el"] = int(v[1][2:])
            elif k == "TS":
                record["ts"] = int(v[0])
            elif k == "VA":
                record["vaddr"] = v[0]
            elif k == "ST" or k == "LD":
                # tools/perf/util/arm-spe-decoder/arm-spe-pkt-decoder.c
                record["op"] = k
                record["ar"] = False
                record["atomic"] = False
                record["excl"] = False
                record["subclass"] = "GP-REG"

                vstr = " ".join(v)
                if "AT" in vstr:
                    record["atomic"] = True
                    record["subclass"] = ""
                if "EXCL" in vstr:
                    record["excl"] = True
                    record["subclass"] = ""
                if "AR" in vstr:
                    record["ar"] = True
                    record["subclass"] = ""
                if not record["atomic"] and not record["ar"] and not record["excl"]:
                    record["subclass"] = v[0]
            elif k == "CONTEXT":
                record["context"] = v[0]
            else:
                logging.error(f"invalid ldst: k={k} and packet={self.__data.items()}")
                raise err.InvalidLoadStorePacket()

        if record["el"] == 2:
            # The PC and Vaddr are missing the 0xff from the highest bits
            record["pc"] = record["pc"][:2] + "ff" + record["pc"][2:]
            record["vaddr"] = record["vaddr"][:2] + "ff" + record["vaddr"][2:]

        return record

    def to_branch(self, cpu: int) -> dict:
        if self.get_type() != RecordType.BRANCH:
            raise err.InvalidRecordType(self.get_type())
        record = get_branch_default_record()
        record["cpu"] = cpu

        for k, v in self.__data.items():
            if k == "B":
                record["op"] = k
                if len(v) == 0:
                    record["condition"] = False
                    record["indirect"] = False
                elif len(v) == 1:
                    if v[0] == "COND":
                        record["condition"] = True
                        record["indirect"] = False
                    elif v[0] == "IND":
                        record["condition"] = False
                        record["indirect"] = True
                    else:
                        raise err.InvalidBrOps(f"invalid br ops: {v[0]}")
                else:
                    raise err.InvalidBrOps(f'invalid other br ops: {" ".join(v)}')

            elif k == "EV":
                record["event"] = ":".join(v)
            elif k == "ISSUE":
                record["issue_lat"] = int(v[0])
            elif k == "TOT":
                record["total_lat"] = int(v[0])
            elif k == "PC":
                record["pc"] = v[0]
                record["el"] = int(v[1][2:])
            elif k == "TS":
                record["ts"] = int(v[0])
            elif k == "TGT":
                record["br_tgt"] = v[0]
                record["br_tgt_lvl"] = int(v[1][2:])
            elif k == "PBT":
                # SPEv1.2 previous branch address
                record["pbt"] = v[0]
                record["pbt_lvl"] = int(v[1][2:])
            elif k == "CONTEXT":
                record["context"] = v[0]
            else:
                logging.error(f"invalid br: k={k} and packet={self.__data.items()}")
                raise err.InvalidBranchPacket()

        if record["el"] == 2:
            record["pc"] = record["pc"][:2] + "ff" + record["pc"][2:]
        if record["br_tgt_lvl"] == 2:
            record["br_tgt"] = record["br_tgt"][:2] + "ff" + record["br_tgt"][2:]
        if record.get("pbt_lvl") == 2:
            record["pbt"] = record["pbt"][:2] + "ff" + record["pbt"][2:]

        return record


DATASOURCE_MAP = {
    "0": "L1D",
    "8": "L2D",
    "9": "PEER-CPU",
    "10": "LOCAL-CLUSTER",
    "11": "LL-CACHE",
    "12": "PEER-CLUSTER",
    "13": "REMOTE",
    "14": "DRAM",
}


def translate_data_source(values: List[str]) -> str:
    """translate data source id to string value

    Args:
        values (List[str]): id list
    Raises:
        InvalidDataSource: invalid data source
    Returns:
        str: data source value
    """
    if len(values) > 1:
        raise err.InvalidDataSource(f"invalid source packet: {values}")
    if values[0] not in DATASOURCE_MAP:
        raise err.InvalidDataSource(f"invalid sourve value: {values[0]}")
    return DATASOURCE_MAP[values[0]]
