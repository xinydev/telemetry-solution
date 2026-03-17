# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Arm Limited

"""Runtime version helpers backed by stamped build metadata."""

import importlib
import logging
import os
from dataclasses import asdict, dataclass, replace
from functools import lru_cache
from pathlib import Path
from subprocess import CalledProcessError, check_output
from types import SimpleNamespace
from typing import Any, Dict, Optional, TypedDict

from topdown_tool.build_info import _find_repo_root

_IGNORED_BRANCHES = {"main", "master"}
_LOGGER = logging.getLogger(__name__)

_STAMPED_VERSION: Any
try:
    _STAMPED_VERSION = importlib.import_module("topdown_tool._version")
except ImportError:  # pragma: no cover - dev installs may not stamp _version
    _LOGGER.error(
        "Missing topdown_tool._version metadata. Install topdown-tool from a built package."
    )
    _STAMPED_VERSION = None


@dataclass(frozen=True)
class BuildInfo:
    """Parsed package version details derived from stamped _version metadata."""

    version: str
    base_version: str
    local_version: Optional[str]
    branch: Optional[str]
    git_sha: Optional[str]
    git_sha_short: Optional[str]
    dirty: bool
    editable: bool
    build_date: Optional[str]

    @property
    def build_identifier(self) -> str:
        """User-friendly identifier (base version + optional branch/SHA)."""

        return _format_identifier(self)

    @property
    def pretty_version(self) -> str:
        """Alias for build_identifier used by CLI/metadata emitters."""

        return self.build_identifier


class GitMetadata(TypedDict):
    branch: Optional[str]
    sha: Optional[str]
    sha_short: Optional[str]
    dirty: bool
    editable: bool


class ProducerMetadata(TypedDict):
    name: str
    version: str
    base_version: str
    pretty_version: str
    git: GitMetadata
    build_date: Optional[str]


def _load_version_data() -> Any:
    if _STAMPED_VERSION is not None:
        return _STAMPED_VERSION
    return SimpleNamespace(
        BASE_VERSION="0.0.0",
        VERSION="0.0.0",
        BRANCH=None,
        GIT_SHA=None,
        GIT_SHA_SHORT=None,
        DIRTY=False,
        EDITABLE=False,
        BUILD_DATE=None,
    )


def _git_output(args: list[str], root: Path) -> Optional[str]:
    try:
        return check_output(
            ["git", *args],
            cwd=root,
            text=True,
            stderr=open(os.devnull, "w", encoding="utf-8"),
        ).strip()
    except (FileNotFoundError, CalledProcessError):
        return None


def _augment_with_git(info: BuildInfo) -> BuildInfo:
    """Fill missing branch/SHA from git when available."""

    if info.git_sha and info.branch:
        return info
    repo_root = _find_repo_root()
    if not repo_root:
        return info

    git_sha = info.git_sha or _git_output(["rev-parse", "HEAD"], repo_root)
    branch = info.branch or _git_output(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    if branch == "HEAD":
        branch = None
    git_sha_short = info.git_sha_short or (git_sha[:7] if git_sha else None)
    dirty_output = _git_output(["status", "--porcelain", "--untracked-files=normal"], repo_root)
    dirty = bool(dirty_output.strip()) if dirty_output else False

    return replace(
        info,
        branch=branch or info.branch,
        git_sha=git_sha or info.git_sha,
        git_sha_short=git_sha_short or info.git_sha_short,
        dirty=dirty or info.dirty,
    )


@lru_cache(maxsize=1)
def get_build_info() -> BuildInfo:
    """Return parsed version metadata derived from stamped _version data."""

    stamped = _load_version_data()
    base_version = getattr(stamped, "BASE_VERSION", "0.0.0")
    version = getattr(stamped, "VERSION", base_version)
    branch = getattr(stamped, "BRANCH", None)
    git_sha = getattr(stamped, "GIT_SHA", None)
    git_sha_short = getattr(stamped, "GIT_SHA_SHORT", None) or (
        git_sha[:7] if git_sha else None
    )

    info = BuildInfo(
        version=version,
        base_version=base_version,
        local_version=None,
        branch=branch,
        git_sha=git_sha,
        git_sha_short=git_sha_short,
        dirty=bool(getattr(stamped, "DIRTY", False)),
        editable=bool(getattr(stamped, "EDITABLE", False)),
        build_date=getattr(stamped, "BUILD_DATE", None),
    )
    if info.branch is None and info.git_sha is None and os.environ.get(
        "TOPDOWN_TOOL_SKIP_GIT_FALLBACK"
    ) != "1":
        info = _augment_with_git(info)
    return info


def _format_identifier(info: BuildInfo) -> str:
    if info.git_sha_short:
        if info.branch and info.branch not in _IGNORED_BRANCHES:
            return f"{info.base_version} ({info.branch}) - {info.git_sha_short}"
        return f"{info.base_version} - {info.git_sha_short}"
    return info.base_version


def get_producer_metadata() -> ProducerMetadata:
    """Return structured metadata for verbose CLI output or JSON emitters."""

    info = get_build_info()
    pretty = info.build_identifier
    return ProducerMetadata(
        name="topdown-tool",
        version=info.version,
        base_version=info.base_version,
        pretty_version=pretty,
        git=GitMetadata(
            branch=info.branch,
            sha=info.git_sha,
            sha_short=info.git_sha_short,
            dirty=info.dirty,
            editable=info.editable,
        ),
        build_date=info.build_date,
    )


def as_dict(info: Optional[BuildInfo] = None) -> Dict[str, Any]:
    """Expose the parsed metadata as a dictionary (used by --version --verbose)."""

    info = info or get_build_info()
    payload = asdict(info)
    payload["pretty_version"] = info.build_identifier
    return payload


__version__ = get_build_info().version

__all__ = [
    "__version__",
    "BuildInfo",
    "GitMetadata",
    "ProducerMetadata",
    "as_dict",
    "get_build_info",
    "get_producer_metadata",
]
