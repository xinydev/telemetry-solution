# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited
"""Public entry points for interacting with Topdown telemetry probes.

The package surface mirrors the canonical probe interfaces and exposes helpers
for working with optional devlib targets when embedding Topdown in other tools.

.. code-block:: python

    from topdown_tool import (
        load_probe_factories,
        ProbeFactory,
        Probe,
        set_remote_target,
        Target,
    )

The CLI entry point remains in ``topdown_tool.__main__``.
"""

from topdown_tool.probe import probe as _probe_module
from topdown_tool.common.devlib_types import Target
from topdown_tool.common.remote_target_manager import (
    get_remote_target,
    get_target_os,
    get_target_type,
    has_remote_target,
    is_target_linuxlike,
    set_remote_target,
)

# Re-export the canonical probe interfaces via explicit bindings so linters and type
# checkers see the public surface without relying on dynamic attribute injection.
Probe = _probe_module.Probe
ProbeFactory = _probe_module.ProbeFactory
ProbeFactoryCliConfigBuilder = _probe_module.ProbeFactoryCliConfigBuilder
load_probe_factories = _probe_module.load_probe_factories
PROBE_PUBLIC_EXPORTS = _probe_module.PROBE_PUBLIC_EXPORTS
del _probe_module

__all__ = PROBE_PUBLIC_EXPORTS + (  # type: ignore[assignment]
    "Target",
    "get_remote_target",
    "set_remote_target",
    "has_remote_target",
    "get_target_type",
    "get_target_os",
    "is_target_linuxlike",
)
