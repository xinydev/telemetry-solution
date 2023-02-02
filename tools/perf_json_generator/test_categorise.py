# SPDX-License-Identifier: Apache-2.0
# Copyright 2022 Arm Limited

import categorise

grouper = categorise.ArmDataEventGrouper()


def test_redundant():
    for mnemonic, group in grouper.mnemonic_matches.items():
        regex_group = grouper.regex_group(mnemonic)

        # Mnemonic matches are redundant if they are matched by a regex
        assert regex_group is None
