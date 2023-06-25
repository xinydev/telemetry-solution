# SPDX-License-Identifier: Apache-2.0
#
# Copyright (C) Arm Ltd. 2023

import hashlib
import os
from contextlib import contextmanager
from subprocess import check_call
from typing import Generator

import requests

_dname = os.path.dirname

PARSER_ROOT = _dname(_dname(os.path.abspath(__file__)))
TESTDATA = os.path.join(PARSER_ROOT, "tests/testdata")


@contextmanager
def cd(path: str) -> Generator:
    # Change directory while inside context manager
    cwd = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(cwd)


def run(command: str) -> None:
    check_call(command, shell=True)


def __is_file_exist(path: str, md5: str) -> bool:
    if not os.path.exists(path):
        return False
    with open(path, "rb") as f:
        r = hashlib.md5(f.read())
        if r.hexdigest() == md5:
            return True
    return False


def download_file(url: str, path: str, md5: str) -> None:
    if __is_file_exist(path, md5):
        return

    resp = requests.get(url)
    with open(path, "wb") as f:
        f.write(resp.content)

    with open(path, "rb") as f:
        r = hashlib.md5(f.read())
        if r.hexdigest() == md5:
            return

    raise Exception(f"md5 mismatch,url:{url}")
