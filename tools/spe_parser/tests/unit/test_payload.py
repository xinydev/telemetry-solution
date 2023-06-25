# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

from unittest import TestCase, main

import deepdiff
import spe_parser.payload as payload


class TestDataSource(TestCase):
    def test_datasource_translate(self):
        self.assertTrue(payload.translate_data_source(["0"]) == "L1D")
        self.assertTrue(payload.translate_data_source(["14"]) == "DRAM")


class TestBranch(TestCase):
    def test_to_branch(self):
        input = [
            {
                "B": ["COND"],
                "EV": ["RETIRED", "NOT-TAKEN"],
                "ISSUE": ["32"],
                "PC": ["0xffc0c28685447c", "el2", "ns=1"],
                "TGT": ["0xffc0c286854480", "el2", "ns=1"],
                "PBT": ["0xffc0c286854480", "el2", "ns=1"],
                "TOT": ["33"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
                "TS": ["13196304034575"],
            },
            {
                "B": [],
                "EV": ["RETIRED"],
                "ISSUE": ["4"],
                "PC": ["0xffc0c28694aae4", "el2", "ns=1"],
                "TGT": ["0xffc0c28694a86c", "el2", "ns=1"],
                "PBT": ["0xffc0c286854480", "el2", "ns=1"],
                "TOT": ["5"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
                "TS": ["13196304034753"],
            },
            {
                "B": ["COND"],
                "EV": ["RETIRED", "MISPRED"],
                "ISSUE": ["4"],
                "PC": ["0xffc0c2868d3d4c", "el2", "ns=1"],
                "TGT": ["0xffc0c2868d3e94", "el2", "ns=1"],
                "PBT": ["0xffc0c286854480", "el2", "ns=1"],
                "TOT": ["5"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
                "TS": ["13196304035123"],
            },
        ]

        output = [
            {
                "cpu": 54,
                "op": "B",
                "pc": "0xffffc0c28685447c",
                "el": 2,
                "condition": True,
                "indirect": False,
                "event": "RETIRED:NOT-TAKEN",
                "issue_lat": 32,
                "total_lat": 33,
                "br_tgt": "0xffffc0c286854480",
                "br_tgt_lvl": 2,
                "pbt": "0xffffc0c286854480",
                "pbt_lvl": 2,
                "context": "0xffc0c286854480",
                "ts": 13196304034575,
            },
            {
                "cpu": 54,
                "op": "B",
                "pc": "0xffffc0c28694aae4",
                "el": 2,
                "condition": False,
                "indirect": False,
                "event": "RETIRED",
                "issue_lat": 4,
                "total_lat": 5,
                "br_tgt": "0xffffc0c28694a86c",
                "br_tgt_lvl": 2,
                "pbt": "0xffffc0c286854480",
                "pbt_lvl": 2,
                "context": "0xffc0c286854480",
                "ts": 13196304034753,
            },
            {
                "cpu": 54,
                "op": "B",
                "pc": "0xffffc0c2868d3d4c",
                "el": 2,
                "condition": True,
                "indirect": False,
                "event": "RETIRED:MISPRED",
                "issue_lat": 4,
                "total_lat": 5,
                "br_tgt": "0xffffc0c2868d3e94",
                "br_tgt_lvl": 2,
                "pbt": "0xffffc0c286854480",
                "pbt_lvl": 2,
                "context": "0xffc0c286854480",
                "ts": 13196304035123,
            },
        ]

        for i in range(len(input)):
            rec = payload.RecordPayload()
            for ik in input[i]:
                rec.add_data(ik, input[i][ik])
            df = deepdiff.DeepDiff(output[i], rec.to_branch(54), ignore_order=True)
            if len(df) != 0:
                print(df)
            self.assertTrue(len(df) == 0)


class TestLoadStore(TestCase):
    def test_to_loadstore(self):
        input = [
            {
                "DATA-SOURCE": ["0"],
                "EV": ["RETIRED", "L1D-ACCESS", "TLB-ACCESS"],
                "ISSUE": ["24"],
                "LD": ["GP-REG"],
                "PC": ["0xffbbf3da99a6a0", "el2", "ns=1"],
                "TOT": ["38"],
                "TS": ["20685196991554"],
                "VA": ["0xff083e7fccbca8"],
                "XLAT": ["1"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
            },
            {
                "DATA-SOURCE": ["0"],
                "EV": ["RETIRED", "L1D-ACCESS", "TLB-ACCESS"],
                "ISSUE": ["20"],
                "LD": ["GP-REG"],
                "PC": ["0xffbbf3da99a6a0", "el2", "ns=1"],
                "TOT": ["31"],
                "TS": ["20685196991576"],
                "VA": ["0xff083e7ff05ca8"],
                "XLAT": ["1"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
            },
            {
                "DATA-SOURCE": ["13"],
                "EV": [
                    "RETIRED",
                    "L1D-ACCESS",
                    "L1D-REFILL",
                    "TLB-ACCESS",
                    "LLC-ACCESS",
                    "LLC-REFILL",
                    "REMOTE-ACCESS",
                ],
                "ISSUE": ["13"],
                "LD": ["GP-REG"],
                "PC": ["0xffbbf3da99a6c4", "el2", "ns=1"],
                "TOT": ["1028"],
                "TS": ["20685196992003"],
                "VA": ["0xff403e40b63328"],
                "XLAT": ["4"],
                "CONTEXT": ["0xffc0c286854480", "el2"],
            },
        ]

        output = [
            {
                "cpu": 0,
                "op": "LD",
                "pc": "0xffffbbf3da99a6a0",
                "el": 2,
                "atomic": False,
                "excl": False,
                "ar": False,
                "subclass": "GP-REG",
                "event": "RETIRED:L1D-ACCESS:TLB-ACCESS",
                "issue_lat": 24,
                "total_lat": 38,
                "vaddr": "0xffff083e7fccbca8",
                "xlat_lat": 1,
                "paddr": "",
                "data_source": "L1D",
                "context": "0xffc0c286854480",
                "ts": 20685196991554,
            },
            {
                "cpu": 0,
                "op": "LD",
                "pc": "0xffffbbf3da99a6a0",
                "el": 2,
                "atomic": False,
                "excl": False,
                "ar": False,
                "subclass": "GP-REG",
                "event": "RETIRED:L1D-ACCESS:TLB-ACCESS",
                "issue_lat": 20,
                "total_lat": 31,
                "vaddr": "0xffff083e7ff05ca8",
                "xlat_lat": 1,
                "paddr": "",
                "data_source": "L1D",
                "context": "0xffc0c286854480",
                "ts": 20685196991576,
            },
            {
                "cpu": 0,
                "op": "LD",
                "pc": "0xffffbbf3da99a6c4",
                "el": 2,
                "atomic": False,
                "excl": False,
                "ar": False,
                "subclass": "GP-REG",
                "event": "RETIRED:L1D-ACCESS:L1D-REFILL:TLB-ACCESS:LLC-ACCESS:LLC-REFILL:REMOTE-ACCESS",
                "issue_lat": 13,
                "total_lat": 1028,
                "vaddr": "0xffff403e40b63328",
                "xlat_lat": 4,
                "paddr": "",
                "data_source": "REMOTE",
                "context": "0xffc0c286854480",
                "ts": 20685196992003,
            },
        ]

        for i in range(len(input)):
            rec = payload.RecordPayload()
            for ik in input[i]:
                rec.add_data(ik, input[i][ik])

            df = deepdiff.DeepDiff(output[i], rec.to_load_store(0), ignore_order=True)
            if len(df) != 0:
                print(df)
            self.assertTrue(len(df) == 0)


if __name__ == "__main__":
    main()
