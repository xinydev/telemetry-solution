# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited
"""Shared manager for optional devlib remote targets.

The configuration is managed through a singleton instance so that different
subsystems (perf, probes, workloads) can access the same target state without
holding on to separate global variables.
"""

import argparse
import json
import os
from typing import Any, Dict, Optional, Tuple

from topdown_tool.common.devlib_types import Target


class RemoteTargetManager:
    """Manage CLI wiring and runtime state for an optional devlib remote target."""

    def __init__(self) -> None:
        self._target: Optional["Target"] = None
        self._target_type: Optional[str] = None
        self._target_os: Optional[str] = None
        self._current_settings: Optional[Tuple[Optional[str], Optional[str]]] = None

    # Public API ---------------------------------------------------------
    def add_cli_arguments(self, parser: argparse.ArgumentParser) -> None:
        """Register CLI options used to configure a remote devlib target."""

        group = parser.add_argument_group("Remote Target Options (devlib)")
        group.add_argument(
            "--target-type",
            choices=["adb", "ssh"],
            help="Connect to a remote device via devlib (Android via 'adb', Linux via 'ssh').",
        )
        group.add_argument(
            "--target-config",
            dest="target_config",
            type=str,
            help=(
                "JSON string or path to JSON file with devlib connection settings. "
                "For adb targets specify the Android device identifier (e.g. "
                "{'device': '<adb id>'}); for ssh targets provide the host name/IP, "
                "username, and authentication details (e.g. {'host': 'example', "
                "'username': 'arm', 'password': '...'} or specify 'keyfile')."
            ),
        )

    def configure_from_args(self, args: argparse.Namespace) -> None:
        """Create or update the remote target configuration from CLI arguments."""

        try:
            target_type = args.target_type
            target_config = args.target_config
        except AttributeError as exc:
            raise RuntimeError(
                "Remote target configuration arguments are unavailable; "
                "ensure add_cli_arguments() is invoked before parsing."
            ) from exc

        # TODO: allow configuring remote targets via an environment variable when
        # CLI flags are omitted, so users do not have to repeat long JSON blobs.

        settings = (target_type, target_config)

        if settings == self._current_settings:
            return

        self._clear_target()

        if not target_type:
            self._current_settings = settings
            return
        if not target_config:
            raise RuntimeError("--target-config must be provided when using --target-type.")

        conn_settings = self._load_target_config(target_config)
        target = self._create_target(target_type, conn_settings)
        self._set_target(target, target_type)
        self._current_settings = settings

    def get_target(self) -> Optional["Target"]:
        """Return the currently configured devlib remote target, if any."""

        return self._target

    def get_target_type(self) -> Optional[str]:
        """Return the configured remote target type ("adb" or "ssh")."""

        return self._target_type

    def get_target_os(self) -> Optional[str]:
        """Return the remote OS as reported by devlib (e.g. "linux", "android")."""

        return self._target_os

    def is_target_linuxlike(self) -> bool:
        """True if the configured target runs Linux/Android or is marked as such."""

        if self._target_os is not None:
            if isinstance(self._target_os, str):
                return self._target_os.lower() in ("linux", "android")
            return False

        return self._target_type in ("adb", "ssh")

    def set_target(
        self,
        target: "Target",
        target_type: Optional[str] = None,
        target_os: Optional[str] = None,
    ) -> None:
        """Inject an already constructed target without going through CLI parsing."""

        self._target = target
        self._target_type = target_type
        if isinstance(target_os, str):
            # Caller supplied an OS string explicitly; just normalise it.
            self._target_os = self._normalize_os_name(target_os)
        elif target_os is not None:
            # Non-string sentinel provided; treat as unknown to trigger fallback logic.
            self._target_os = None
        else:
            # Derive OS from the injected target when possible, but don't explode if
            # the stub lacks the attribute (common in tests/custom callers).
            try:
                os_name = getattr(target, "os", None)
                self._target_os = (
                    self._normalize_os_name(os_name) if isinstance(os_name, str) else None
                )
            except Exception:  # pylint: disable=broad-exception-caught
                self._target_os = None
        self._current_settings = None

    # Internal helpers ---------------------------------------------------
    def _load_target_config(self, configuration: str) -> Dict[str, Any]:
        expanded = os.path.expanduser(os.path.expandvars(configuration))
        if os.path.exists(expanded):
            with open(expanded, "r", encoding="utf-8") as handle:
                return json.load(handle)
        return json.loads(configuration)

    def _create_target(self, target_type: str, conn_settings: Dict[str, Any]) -> "Target":
        try:
            # pylint: disable=import-outside-toplevel, import-error
            from devlib.target import AndroidTarget, LinuxTarget  # type: ignore
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise RuntimeError(
                "devlib is required for --target-type/--target-config but is not available. "
                'Install the remote extra (python3 -m pip install -e ".[remote]") or fetch it from: '
                "https://gitlab.arm.com/tooling/workload-automation/devlib"
            ) from exc

        target: Target
        if target_type == "adb":
            target = AndroidTarget(connection_settings=conn_settings)
        else:
            target = LinuxTarget(connection_settings=conn_settings)

        try:
            target.connect()
        except Exception:  # pylint: disable=broad-exception-caught
            try:
                target.disconnect()
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            raise

        return target

    def _set_target(self, target: "Target", target_type: str) -> None:
        self._target = target
        self._target_type = target_type
        try:
            os_name = getattr(target, "os", None)
            self._target_os = (
                self._normalize_os_name(os_name) if isinstance(os_name, str) else None
            )
        except Exception:  # pylint: disable=broad-exception-caught
            self._target_os = None

    def _clear_target(self) -> None:
        previous: Optional["Target"] = self._target
        self._target = None
        self._target_type = None
        self._target_os = None
        self._current_settings = None
        if previous is None:
            return
        try:
            previous.disconnect()
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    @staticmethod
    def _normalize_os_name(os_value: Optional[str]) -> Optional[str]:
        """Normalise OS identifiers reported by devlib or supplied explicitly."""

        return os_value.strip().lower() if os_value is not None else None


REMOTE_TARGET_MANAGER = RemoteTargetManager()


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    REMOTE_TARGET_MANAGER.add_cli_arguments(parser)


def configure_from_args(args: argparse.Namespace) -> None:
    REMOTE_TARGET_MANAGER.configure_from_args(args)


def get_remote_target() -> Optional["Target"]:
    return REMOTE_TARGET_MANAGER._target  # pylint: disable=protected-access


def has_remote_target() -> bool:
    return get_remote_target() is not None


def get_target_type() -> Optional[str]:
    return REMOTE_TARGET_MANAGER.get_target_type()


def get_target_os() -> Optional[str]:
    return REMOTE_TARGET_MANAGER.get_target_os()


def is_target_linuxlike() -> bool:
    return REMOTE_TARGET_MANAGER.is_target_linuxlike()


def set_remote_target(
    target: "Target",
    target_type: Optional[str] = None,
    target_os: Optional[str] = None,
) -> None:
    REMOTE_TARGET_MANAGER.set_target(target, target_type, target_os)


__all__ = [
    "REMOTE_TARGET_MANAGER",
    "RemoteTargetManager",
    "add_cli_arguments",
    "configure_from_args",
    "get_remote_target",
    "has_remote_target",
    "get_target_os",
    "get_target_type",
    "is_target_linuxlike",
    "set_remote_target",
]
