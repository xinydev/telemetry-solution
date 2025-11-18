# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""Shared typing helpers for optional devlib dependencies."""

from typing import Any


try:
    from devlib.target import Target  # type: ignore  # pylint: disable=import-error
except Exception:  # pylint: disable=broad-exception-caught
    Target = Any  # type: ignore[assignment]


__all__ = ["Target"]
