# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

from enum import Enum
from typing import Dict, List

import spe_parser.errors as err
from spe_parser.schema import (
    get_branch_default_record,
    get_ldst_default_record,
    get_other_default_record,
)


class RecordType(Enum):
    LOAD = 0
    STORE = 1
    BRANCH = 2
    OTHER = 3
    UNKNOWN = 4


class Record:
    def __init__(self, data: Dict[str, List[str]], cpu: int) -> None:
        self.data = data
        self.cpu = cpu

    def __str__(self) -> str:
        return str(self.data)

    def to_dict(self) -> dict:
        return self.data

    @property
    def type(self) -> RecordType:
        return RecordType.UNKNOWN


class BranchRecord(Record):
    def to_dict(self) -> dict:
        record = get_branch_default_record()
        record["cpu"] = self.cpu

        keys = self.data.keys()
        if "B" in keys:
            v = self.data["B"]
            record["op"] = "B"
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

        if "EV" in keys:
            record["event"] = ":".join(self.data["EV"])
        if "ISSUE" in keys:
            record["issue_lat"] = int(self.data["ISSUE"][0])
        if "TOT" in keys:
            record["total_lat"] = int(self.data["TOT"][0])
        if "PC" in keys:
            v = self.data["PC"]
            record["pc"] = v[0]
            record["el"] = int(v[1][2:])
        if "TS" in keys:
            record["ts"] = int(self.data["TS"][0])
        if "TGT" in keys:
            v = self.data["TGT"]
            record["br_tgt"] = v[0]
            record["br_tgt_lvl"] = int(v[1][2:])
        if "PBT" in keys:
            # SPEv1.2 previous branch address
            v = self.data["PBT"]
            record["pbt"] = v[0]
            record["pbt_lvl"] = int(v[1][2:])
        if "CONTEXT" in keys:
            record["context"] = self.data["CONTEXT"][0]

        if record["el"] == 2:
            record["pc"] = record["pc"][:2] + "ff" + record["pc"][2:]
        if record["br_tgt_lvl"] == 2:
            record["br_tgt"] = record["br_tgt"][:2] + "ff" + record["br_tgt"][2:]
        if record.get("pbt_lvl") == 2:
            record["pbt"] = record["pbt"][:2] + "ff" + record["pbt"][2:]

        return record

    @property
    def type(self) -> RecordType:
        return RecordType.BRANCH


class LoadRecord(Record):
    @property
    def type(self) -> RecordType:
        return RecordType.LOAD

    def to_dict(self) -> dict:
        record = get_ldst_default_record()
        record["cpu"] = self.cpu

        keys = self.data.keys()

        if "DATA-SOURCE" in keys:
            record["data_source"] = translate_data_source(self.data["DATA-SOURCE"])
        if "EV" in keys:
            record["event"] = ":".join(self.data["EV"])
        if "ISSUE" in keys:
            record["issue_lat"] = int(self.data["ISSUE"][0])
        if "TOT" in keys:
            record["total_lat"] = int(self.data["TOT"][0])
        if "XLAT" in keys:
            record["xlat_lat"] = int(self.data["XLAT"][0])
        if "PA" in keys:
            record["paddr"] = self.data["PA"][0]
        if "PC" in keys:
            v = self.data["PC"]
            # v : 0xffffab47fdb0 el0 ns=1
            record["pc"] = v[0]
            record["el"] = int(v[1][2:])
        if "TS" in keys:
            record["ts"] = int(self.data["TS"][0])
        if "VA" in keys:
            record["vaddr"] = self.data["VA"][0]
        if "ST" in keys or "LD" in keys:
            k = "ST" if "ST" in keys else "LD"
            v = self.data[k]
            record["op"] = k
            record["ar"] = False
            record["atomic"] = False
            record["excl"] = False
            record["subclass"] = "GP-REG"
            record["sve_evl"] = 0

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
            if "EVLEN" in vstr:
                # evl value is following EVLEN
                # ST EVLEN 128 PRED
                record["sve_evl"] = int(v[v.index("EVLEN") + 1])
                record["subclass"] = "SVE"
            if "PRED" in vstr:
                record["sve_pred"] = True
            if "SG" in vstr:
                record["sve_sg"] = True
            if (
                not record["atomic"]
                and not record["ar"]
                and not record["excl"]
                and not record["sve_evl"]
            ):
                record["subclass"] = v[0]
        if "CONTEXT" in keys:
            record["context"] = self.data["CONTEXT"][0]

        if record["el"] == 2:
            # The PC and Vaddr are missing the 0xff from the highest bits
            record["pc"] = record["pc"][:2] + "ff" + record["pc"][2:]
            record["vaddr"] = record["vaddr"][:2] + "ff" + record["vaddr"][2:]

        return record


class StoreRecord(LoadRecord):
    @property
    def type(self) -> RecordType:
        return RecordType.STORE


# The SPE record for a CAS sample might have some unexpected events set, we should exclude them
# https://developer.arm.com/documentation/SDEN885747  #1912195
events_should_not_in_other_record = {
    "REMOTE-ACCESS",
    "LLC-REFILL",
    "LLC-ACCESS",
    "TLB-REFILL",
    "TLB-ACCESS",
    "L1D-REFILL",
    "L1D-ACCESS",
}


class OtherRecord(Record):
    @property
    def type(self) -> RecordType:
        return RecordType.OTHER

    def to_dict(self) -> dict:
        record = get_other_default_record()
        record["cpu"] = self.cpu
        keys = self.data.keys()

        if "OTHER" in keys:
            vstr = " ".join(self.data["OTHER"])
            if "COND-SELECT" in vstr:
                record["condition"] = True
            record["subclass"] = "OTHER"
            record["op"] = "OTHER"
        if "SVE-OTHER" in keys:
            v = self.data["SVE-OTHER"]
            # evl value is following EVLEN
            # SVE-OTHER EVLEN 32 FP
            if len(v) > 1 and v[0] == "EVLEN":
                record["sve_evl"] = int(v[1])
            vstr = " ".join(v)
            if "FP" in vstr:
                record["sve_fp"] = True
            if "PRED" in vstr:
                record["sve_pred"] = True
            record["subclass"] = "SVE"
            record["op"] = "OTHER"
        if "EV" in keys:
            record["event"] = ":".join(
                sorted(set(self.data["EV"]) - events_should_not_in_other_record)
            )

        if "ISSUE" in keys:
            record["issue_lat"] = int(self.data["ISSUE"][0])
        if "TOT" in keys:
            record["total_lat"] = int(self.data["TOT"][0])
        if "PC" in keys:
            v = self.data["PC"]
            record["pc"] = v[0]
            record["el"] = int(v[1][2:])
        if "TS" in keys:
            record["ts"] = int(self.data["TS"][0])
        if "CONTEXT" in keys:
            record["context"] = self.data["CONTEXT"][0]

        if record["el"] == 2:
            record["pc"] = record["pc"][:2] + "ff" + record["pc"][2:]

        return record


def create_record(data: Dict[str, List[str]], cpu: int) -> Record:
    # an example of data:
    #   {"SVE-OTHER": ["EVLEN","32"], "TS": ["193890286826374"], "PC": ["0xaaaaab110e5c"], ...}
    # for different packets, there are special different keys
    # for example: "LD","ST" only in load/store packets
    # "B" only in branch packets, "OTHER" and "SVE-OTHER" only in other packets

    keys = list(data.keys())
    if "LD" in keys:
        return LoadRecord(data, cpu)
    elif "ST" in keys:
        return StoreRecord(data, cpu)
    elif "B" in keys:
        return BranchRecord(data, cpu)
    elif "OTHER" in keys or "SVE-OTHER" in keys:
        return OtherRecord(data, cpu)
    else:
        return Record(data, cpu)


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
