# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import os
import difflib


def get_fixture_path(*paths):
    """
    Compute the absolute path to a fixture file, relative to the cpu_probe/fixtures directory.
    """
    # The fixtures directory is cpu_probe/fixtures relative to this helper.py file
    helper_dir = os.path.abspath(os.path.dirname(__file__))
    cpu_probe_dir = os.path.dirname(helper_dir)
    fixtures_dir = os.path.join(cpu_probe_dir, "fixtures")
    return os.path.join(fixtures_dir, *paths)


def compare_reference(actual: str, reference_path: str, regen_reference_mode: str = "off"):
    """
    Compare actual output against reference file, or update the reference if requested.
    Supports three modes:
      'off'    : compare only, fail and show message if different
      'write'  : overwrite reference file
      'dryrun' : show unified diff if output differs, but do not update file

    Files are stored in the fixtures/ subdirectory to keep test directory clean.
    """
    if regen_reference_mode not in ("off", "write", "dryrun"):
        raise ValueError(f"Invalid regen_reference_mode: {regen_reference_mode}")
    if regen_reference_mode == "write":
        os.makedirs(os.path.dirname(reference_path), exist_ok=True)
        with open(reference_path, "w", encoding="utf-8") as f:
            f.write(actual)
        return
    if not os.path.exists(reference_path):
        raise AssertionError(f"Reference file does not exist: {reference_path}")
    with open(reference_path, encoding="utf-8") as f:
        expected = f.read()
    if actual == expected:
        return
    if regen_reference_mode == "dryrun":
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile="reference",
                tofile="actual",
                lineterm="",
            )
        )

        raise AssertionError(
            f"\nOutput differs from reference ({reference_path}) [dryrun]:\n"
            f"{diff}\n"
            "To update, run pytest with --regen-reference=write\n"
        )
    # mode == off: compare and fail with standard message
    assert actual == expected, (
        f"\nOutput did not match reference file:\n  {reference_path}\n"
        "To update, run pytest with --regen-reference=write\n"
        "To preview changes, run pytest with --regen-reference=dryrun\n"
    )
