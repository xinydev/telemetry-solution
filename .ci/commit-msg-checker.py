#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import re
import sys
from pathlib import Path

def load_valid_scopes(file_path: Path) -> list[str]:
    lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]

def read_msg(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return [ln for ln in lines if not ln.strip().startswith("#")]

def make_header_re(scopes: list[str]) -> re.Pattern:
    """Build a regex from the given header list."""
    pattern = r"^(" + "|".join(re.escape(h) for h in scopes) + r"): .+$"
    return re.compile(pattern)

def valid(lines: list[str], scopes: list[str]) -> bool:
    if not lines:
        return False

    HEADER_RE = make_header_re(scopes)

    header = lines[0]
    if not HEADER_RE.match(header):
        return False
    if header.endswith("."):
        return False
    if len(lines) > 1 and lines[1].strip() != "":
        return False
    return True


if __name__ == "__main__":
    path = Path(sys.argv[1])

    valid_scopes = load_valid_scopes(Path(".ci/commit-msg-scopes"))
    if not valid(read_msg(path), valid_scopes):
        print("Commit message validation failed.")
        print("Format to use:")
        print("<" + "|".join(valid_scopes) + ">: <summary>")
        print("<empty line>")
        print("<detailed explanation, possibly multiple lines>")
        sys.exit(1)

    sys.exit(0)
