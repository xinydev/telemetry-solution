# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

import io
import json
import logging
from typing import List
from unittest import TestCase

from spe_parser.perf_decoder import get_spe_records_regions
from spe_parser.spe_decoder import gen_mask, get_packets
from spe_parser.testutils import TESTDATA, cd, download_file


def decode_by_py_decoder(file_path: str) -> List[str]:
    # Parse a binary perf.data directly.
    regions = get_spe_records_regions(file_path)
    pkts = []
    with open(file_path, "rb") as f:
        for region in regions:
            f.seek(region["offset"])
            spe_f = io.BytesIO(f.read(region["size"]))
            pkts.extend(list(get_packets(spe_f)))
    return pkts


def decode_by_perf_raw(file_path: str) -> List[str]:
    # Parse a perf.raw file, which can be converted
    # from a perf.data file.
    has_spe_auxtrace = False
    pkts = []
    with open(file_path) as f:
        for line in f:
            if not has_spe_auxtrace and "ARM SPE data" not in line:
                # skips the lines before ARM SPE data line
                continue
            if not has_spe_auxtrace:
                has_spe_auxtrace = True
                continue
            if has_spe_auxtrace and len(line) <= 1:
                # Last line of current ARM SPE session. It is an empty line with \n
                # Turn off HasSpeAuxtrace flag to search for next session
                has_spe_auxtrace = False
            # In the AUXTRACE record of the perf.raw file, the decoded content starts
            # after the 62nd character of each line. We only need to process this part
            pkt = line[62:].strip()
            if not pkt:
                continue
            if pkt == "PAD":
                continue
            pkts.append(pkt)
    return pkts


class TestSPEDecoder(TestCase):
    def setUp(self) -> None:
        # download files
        with cd(TESTDATA):
            with open("data.json") as f:
                file_metas = json.load(f)
                for meta in file_metas:
                    download_file(meta["url"], meta["name"], meta["md5"])
        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_spe_decode(self) -> None:
        # Test whether the results of parsing a perf.raw file
        # and parsing a binary perf.data file are consistent,
        # to ensure the Python script works correctly.
        with cd(TESTDATA):
            pkts_decoder = decode_by_py_decoder("perf.data")
            pkts_raw = decode_by_perf_raw("perf.raw")
            self.assertEqual(len(pkts_decoder), len(pkts_raw))
            self.assertEqual(pkts_decoder, pkts_raw)


def check_single_packet(inputs, outputs):
    for input, output in zip(inputs, outputs):
        fh = io.BytesIO(hex_string_to_bytes(input))
        pkt = next(get_packets(fh))
        if pkt != output:
            logging.error(
                f"get_packets() error, input:{input}, expected:{output}, got:{pkt}"
            )
            return False
    return True


def check_packets(inputs, outputs):
    for input, output in zip(inputs, outputs):
        fh = io.BytesIO(hex_string_to_bytes(input))
        pkts = list(get_packets(fh))
        if pkts != output:
            logging.error(
                f"get_packets() error, input:{input}, expected:{output}, got:{pkts}"
            )
            return False
    return True


def hex_string_to_bytes(hex_str):
    hex_str = hex_str.replace(" ", "").lower()
    return bytes.fromhex(hex_str)


# The SPE records definition can be found here,
# https://developer.arm.com/documentation/ddi0487/latest/
class TestGetPackets(TestCase):
    def test_get_addr(self):
        inputs = [
            "b0 5c 8b 8c 86 c2 c0 ff c0",
            "b3 e8 09 8a d8 0b 08 00 80",
            "b2 c0 26 c2 07 20 fc ff 00",
            "b1 e0 89 8c 86 c2 c0 ff c0",
        ]
        outputs = [
            "PC 0xffc0c2868c8b5c el2 ns=1",
            "PA 0x80bd88a09e8 ns=1 ch=0 pat=0",
            "VA 0xfffc2007c226c0",
            "TGT 0xffc0c2868c89e0 el2 ns=1",
        ]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_counter(self):
        inputs = ["99 07 00", "98 0b 00", "9a 01 00"]
        outputs = ["LAT 7 ISSUE", "LAT 11 TOT", "LAT 1 XLAT"]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_ts(self):
        inputs = ["71 6c f8 a5 83 00 0c 00 00"]
        outputs = ["TS 13196348225644"]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_op(self):
        inputs = ["49 00", "4a 01", "49 01", "4a 02", "49 16", "49 05"]
        outputs = [
            "LD GP-REG",
            "B COND",
            "ST GP-REG",
            "B IND",
            "LD AT AR",
            "ST SIMD-FP",
        ]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_data_source(self):
        inputs = [
            "43 00",
        ]
        outputs = [
            "DATA-SOURCE 0",
        ]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_data_events(self):
        inputs = ["52 16 00", "52 02 00", "52 42 00", "52 1e 03"]
        outputs = [
            "EV RETIRED L1D-ACCESS TLB-ACCESS",
            "EV RETIRED",
            "EV RETIRED NOT-TAKEN",
            "EV RETIRED L1D-ACCESS L1D-REFILL TLB-ACCESS LLC-ACCESS LLC-REFILL",
        ]
        self.assertTrue(check_single_packet(inputs, outputs))

    def test_get_frame(self):
        inputs = [
            "71 af f9 04 81 00 0c 00 00 b0 00 b6 a9 e4 aa aa 00 80 49 00 52 16 00 99 04 00 98 08 00 b2 43 da 5d e6 aa aa 00 00 9a 01 00 b3 43 5a 95 2c 03 08 00 80 43 00"
        ]
        outputs = [
            [
                "TS 13196304120239",
                "PC 0xaaaae4a9b600 el0 ns=1",
                "LD GP-REG",
                "EV RETIRED L1D-ACCESS TLB-ACCESS",
                "LAT 4 ISSUE",
                "LAT 8 TOT",
                "VA 0xaaaae65dda43",
                "LAT 1 XLAT",
                "PA 0x8032c955a43 ns=1 ch=0 pat=0",
                "DATA-SOURCE 0",
            ]
        ]
        self.assertTrue(check_packets(inputs, outputs))


class TestSPEHelperFunc(TestCase):
    def test_gen_mask(self):
        self.assertEqual(gen_mask(1, 0), 3)
        self.assertEqual(gen_mask(5, 4), 48)
        self.assertEqual(gen_mask(7, 6), 192)
        # 0x00ff ffff ffff ffff
        self.assertEqual(gen_mask(55, 0), 72057594037927935)
