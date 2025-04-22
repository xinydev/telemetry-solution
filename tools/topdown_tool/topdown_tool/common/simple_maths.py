#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

import ast
import logging
import math
import operator
from typing import Any, Callable, Dict, Type, Union

# Simple arithmetic parser that works by parsing the input as a python expression
# and only evaluates an allowed list of operations

UnaryOperation = Callable[[Any], Any]
BinaryOperation = Callable[[Any, Any], Any]
Operation = Union[UnaryOperation, BinaryOperation]

# Map AST operators to execution operators. (Also restricts available operations)
operators: Dict[Type[Union[ast.operator, ast.unaryop]], Operation] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}


class ForbiddenExpressionException(Exception):
    pass


class InvalidExpressionException(Exception):
    pass


def eval_node(node: ast.expr) -> Any:
    def get_op_function(op: Union[ast.unaryop, ast.operator]) -> Operation:
        op_type = type(op)
        if op_type not in operators:
            raise ForbiddenExpressionException(f'Operator "{op_type.__name__}" is not allowed.')
        return operators[op_type]

    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        op = get_op_function(node.op)
        if type(node.op) in (ast.LShift, ast.RShift):
            return op(int(eval_node(node.left)), eval_node(node.right))  # type: ignore
        return op(eval_node(node.left), eval_node(node.right))  # type: ignore
    if isinstance(node, ast.UnaryOp):
        op = get_op_function(node.op)
        return op(eval_node(node.operand))  # type: ignore
    raise ForbiddenExpressionException(f'"{type(node).__name__}" is not allowed.')


def evaluate(expression: str) -> float:
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
