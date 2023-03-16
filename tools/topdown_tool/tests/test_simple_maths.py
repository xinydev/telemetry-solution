# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2023 Arm Limited

import math

import pytest

from topdown_tool.simple_maths import InvalidExpressionException, evaluate


def test_simple():
    assert evaluate("1 + 2") == 3
    assert evaluate("5 - 2") == 3
    assert evaluate("2 * 2") == 4
    assert evaluate("10 / 2") == 5


def test_floating_point():
    assert evaluate("5 / 2") == 2.5


def test_divide_by_zero():
    assert math.isnan(evaluate("1 / 0"))


def test_prescedence():
    assert evaluate("2 * 3 + 4") == evaluate("(2 * 3) + 4") == 10
    assert evaluate("2 * (3 + 4)") == 14


def test_restricted():
    with pytest.raises(InvalidExpressionException):
        evaluate("3 ** 2")

    with pytest.raises(InvalidExpressionException):
        evaluate('print("Hello World")')


def test_maformed():
    with pytest.raises(InvalidExpressionException):
        evaluate("1+")
