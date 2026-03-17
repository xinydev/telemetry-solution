# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Arm Limited

"""Setuptools command hooks to stamp build-time metadata into the package."""
# pylint: disable=duplicate-code

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Type, TYPE_CHECKING

if TYPE_CHECKING:
    from setuptools.command.build_py import build_py as BuildPyBase  # type: ignore[import]
else:  # pragma: no cover - runtime import handled below
    try:
        from setuptools.command import build_py as _build_py_module  # type: ignore[import]
        BuildPyBase = _build_py_module.build_py  # type: ignore[assignment]
    except ImportError:  # pragma: no cover - lint environments may miss setuptools
        class BuildPyBase:  # type: ignore[too-few-public-methods]
            def run(self) -> None:  # pragma: no cover - placeholder
                raise RuntimeError("setuptools is required to build topdown_tool")

try:  # Setuptools 68+ exposes editable_wheel for PEP 660 editable builds
    from setuptools.command.editable_wheel import editable_wheel  # type: ignore[import]
except ImportError:  # pragma: no cover - editable_wheel not present on older setuptools
    editable_wheel = None  # type: ignore[assignment]


@dataclass(frozen=True)
class GitStamp:
    branch: Optional[str]
    sha: Optional[str]
    sha_short: Optional[str]
    dirty: bool


@dataclass(frozen=True)
class BuildStamp:
    semver: str
    build_date: str
    git: GitStamp
    editable: bool


def _find_repo_root() -> Optional[Path]:
    """Locate the git repo root by walking parents of this file."""

    here = Path(__file__).resolve()
    for candidate in (here.parent, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _run_git(args: list[str], root: Path, check: bool = False) -> Optional[str]:
    """Run a git command, returning None when git is unavailable or fails."""

    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )
    except (FileNotFoundError, OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _collect_stamp(root: Path, semver: str, editable: bool) -> BuildStamp:
    git_sha = _run_git(["rev-parse", "HEAD"], root=root)
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], root=root)
    if branch == "HEAD":
        branch = None
    git_sha_short = git_sha[:7] if git_sha else None
    dirty_output = _run_git(
        ["status", "--porcelain", "--untracked-files=normal"], root=root, check=False
    )
    dirty = bool(dirty_output.strip()) if dirty_output is not None else False

    return BuildStamp(
        semver=semver,
        build_date=datetime.now(timezone.utc).isoformat(),
        git=GitStamp(
            branch=branch,
            sha=git_sha,
            sha_short=git_sha_short,
            dirty=dirty,
        ),
        editable=editable,
    )


def _render(stamp: BuildStamp) -> str:
    year = datetime.now(timezone.utc).year
    return f'''# SPDX-License-Identifier: Apache-2.0
# Copyright {year} Arm Limited

"""Auto-generated during build. Do not edit manually."""

SEMVER = {stamp.semver!r}
BUILD_DATE = {stamp.build_date!r}
BRANCH = {stamp.git.branch!r}
GIT_SHA = {stamp.git.sha!r}
GIT_SHA_SHORT = {stamp.git.sha_short!r}
DIRTY = {stamp.git.dirty}
EDITABLE = {stamp.editable}
'''


def _write_build_info(build_dir: Path, semver: str, editable: bool) -> None:
    target_dir = build_dir / "topdown_tool"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = _collect_stamp(root=_find_repo_root() or Path.cwd(), semver=semver, editable=editable)
    (target_dir / "_build_info.py").write_text(_render(stamp), encoding="utf-8")


def _render_version(stamp: BuildStamp) -> str:
    year = datetime.now(timezone.utc).year
    return f'''# SPDX-License-Identifier: Apache-2.0
# Copyright {year} Arm Limited

"""Auto-generated during build. Do not edit manually."""

BASE_VERSION = {stamp.semver!r}
VERSION = {stamp.semver!r}
BRANCH = {stamp.git.branch!r}
GIT_SHA = {stamp.git.sha!r}
GIT_SHA_SHORT = {stamp.git.sha_short!r}
DIRTY = {stamp.git.dirty}
EDITABLE = {stamp.editable}
BUILD_DATE = {stamp.build_date!r}
'''


def _write_version_file(build_dir: Path, semver: str, editable: bool) -> None:
    target_dir = build_dir / "topdown_tool"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = _collect_stamp(root=_find_repo_root() or Path.cwd(), semver=semver, editable=editable)
    (target_dir / "_version.py").write_text(_render_version(stamp), encoding="utf-8")


class BuildPyWithBuildInfo(BuildPyBase):
    """build_py that writes topdown_tool/_build_info.py and _version.py into build_lib."""

    def run(self) -> None:  # type: ignore[override]
        super().run()
        version = self.distribution.get_version()
        editable = bool(getattr(self.distribution, "editable_mode", False))
        _write_build_info(Path(self.build_lib), semver=version, editable=editable)
        _write_version_file(Path(self.build_lib), semver=version, editable=editable)


def _make_editable_wheel_hook() -> Optional[Type[Any]]:
    """Construct an editable_wheel subclass when available."""

    if not editable_wheel:
        return None

    class _EditableWheelWithBuildInfo(editable_wheel):  # type: ignore[misc]
        """editable_wheel hook that also stamps _build_info.py and _version.py."""

        def run(self) -> None:  # type: ignore[override]
            super().run()
            version = self.distribution.get_version()
            editable = True
            # editable_wheel writes into build_dir (wheel_directory)
            build_dir_attr = getattr(self, "build_dir", None)
            editable_dir_attr = getattr(self, "editable_wheel_dir", None)
            egg_info_dir = None
            if hasattr(self, "_find_egg_info_dir"):
                egg_info_path = self._find_egg_info_dir()  # type: ignore[attr-defined]
                egg_info_dir = Path(egg_info_path) if egg_info_path else None
            build_dir = Path(build_dir_attr or editable_dir_attr or egg_info_dir or Path.cwd())
            _write_build_info(build_dir, semver=version, editable=editable)
            _write_version_file(build_dir, semver=version, editable=editable)

    return _EditableWheelWithBuildInfo


EditableWheelWithBuildInfo: Optional[Type[Any]] = _make_editable_wheel_hook()
