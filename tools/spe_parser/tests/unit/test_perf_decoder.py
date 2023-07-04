# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023


import json
from unittest import TestCase

from spe_parser.perf_decoder import get_mmap_records, get_spe_records_regions
from spe_parser.testutils import TESTDATA, cd, download_file


def get_spe_regions_from_raw(path: str) -> int:
    # In the perf.raw file, each segment of SPE records begins
    # with "ARM SPE data". Therefore, the number of occurrences
    # of this string is used to determine the quantity of
    # SPE record region
    with open(path) as f:
        return f.read().count("ARM SPE data")


class TestPerfDecoder(TestCase):
    def setUp(self) -> None:
        # download test data
        with cd(TESTDATA):
            with open("data.json") as f:
                file_metas = json.load(f)
                for meta in file_metas:
                    download_file(meta["url"], meta["name"], meta["md5"])

        return super().setUp()

    def tearDown(self) -> None:
        return super().tearDown()

    def test_decode_regions(self) -> None:
        # Test whether the number of SPE record regions extracted from
        # the perf.data binary file using the construct library
        # is the same as the one parsed directly from perf.raw.
        with cd(TESTDATA):
            region_from_perfdata = len(get_spe_records_regions("perf.data"))
            region_from_perfraw = get_spe_regions_from_raw("perf.raw")
            self.assertEqual(region_from_perfdata, region_from_perfraw)

    def test_decode_mmap(self) -> None:
        with cd(TESTDATA):
            mmap_records = get_mmap_records("perf.data")
            self.assertEqual(118, len(mmap_records))
