# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

from topdown_tool.common import range_encode


def test_empty_list():
    assert range_encode([]) is None


def test_single_value():
    assert range_encode([5]) == "5"


def test_all_consecutive():
    assert range_encode([0, 1, 2, 3]) == "0-3"


def test_non_consecutive_start():
    assert range_encode([0, 2, 3, 4]) == "0,2-4"


def test_multiple_ranges():
    assert range_encode([0, 2, 3, 4, 6, 7, 9]) == "0,2-4,6-7,9"


def test_unsorted_input():
    # Function sorts the list automatically.
    assert range_encode([3, 2, 1, 0, 5, 4]) == "0-5"
