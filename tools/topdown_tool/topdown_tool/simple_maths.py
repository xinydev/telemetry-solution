#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2024 Arm Limited

import ast
import logging
import math
import operator
from typing import Union

# Simple arithmetic parser that works by parsing the input as a python expression and only evaluates an allowed list of operations

# Map AST operators to execution operators. (Also restricts available operations)
operators = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
}


class ForbiddenExpressionException(Exception):
    pass


class InvalidExpressionException(Exception):
    pass


def eval_node(node: ast.expr):
    def get_op_function(op: Union[ast.unaryop, ast.operator]):
        op_type = type(op)
        if op_type not in operators:
            raise ForbiddenExpressionException(f'Operator "{op_type.__name__}" is not allowed.')
        return operators[op_type]

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op = get_op_function(node.op)
        return op(eval_node(node.left), eval_node(node.right))  # type: ignore
    if isinstance(node, ast.UnaryOp):
        op = get_op_function(node.op)
        return op(eval_node(node.operand))  # type: ignore
    raise ForbiddenExpressionException(f'"{type(node).__name__}" is not allowed.')


def evaluate(expression: str):
    try:
        return eval_node(ast.parse(expression, mode="eval").body)
    except (SyntaxError, ForbiddenExpressionException) as e:
        raise InvalidExpressionException(f'Invalid expression "{expression}"') from e
    except ZeroDivisionError:
        logging.debug('Divide by zero when evaluating "%s".', expression)
        return math.nan


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 2:
        print(evaluate(" ".join(sys.argv[1:])))
