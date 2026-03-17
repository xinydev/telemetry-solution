# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Arm Limited

from types import SimpleNamespace

import pytest

from topdown_tool import version


@pytest.fixture(autouse=True)
def _clear_version_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TOPDOWN_TOOL_SKIP_GIT_FALLBACK", "1")
    version.get_build_info.cache_clear()
    yield
    version.get_build_info.cache_clear()


def test_base_version_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="1.2.3",
            VERSION="1.2.3",
            BRANCH=None,
            GIT_SHA=None,
            GIT_SHA_SHORT=None,
            DIRTY=False,
            EDITABLE=False,
            BUILD_DATE=None,
        ),
    )

    info = version.get_build_info()

    assert info.version == "1.2.3"
    assert info.base_version == "1.2.3"
    assert info.local_version is None
    assert info.branch is None
    assert info.build_identifier == "1.2.3"
    assert info.dirty is False
    assert info.editable is False


def test_unstamped_uses_unknown_version(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(version, "_STAMPED_VERSION", None)

    info = version.get_build_info()

    assert info.version == "0.0.0"
    assert info.base_version == "0.0.0"
    assert info.build_identifier == "0.0.0"


def test_feature_branch_formatting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="1.12.1.dev3",
            VERSION="1.12.1.dev3",
            BRANCH="feature.topic",
            GIT_SHA="abc123456",
            GIT_SHA_SHORT="abc1234",
            DIRTY=False,
            EDITABLE=False,
            BUILD_DATE=None,
        ),
    )

    info = version.get_build_info()

    assert info.branch == "feature.topic"
    assert info.git_sha == "abc123456"
    assert info.git_sha_short == "abc1234"
    assert info.build_identifier == "1.12.1.dev3 (feature.topic) - abc1234"


def test_main_branch_formatting(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="1.12.1.dev3",
            VERSION="1.12.1.dev3",
            BRANCH=None,
            GIT_SHA="abc1234d",
            GIT_SHA_SHORT=None,
            DIRTY=False,
            EDITABLE=False,
            BUILD_DATE=None,
        ),
    )

    info = version.get_build_info()

    assert info.branch is None
    assert info.git_sha_short == "abc1234"
    assert info.build_identifier == "1.12.1.dev3 - abc1234"


def test_as_dict_and_producer_metadata(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="0.0.1.dev1",
            VERSION="0.0.1.dev1",
            BRANCH="feature.cool",
            GIT_SHA="deadbeefcafebabe",
            GIT_SHA_SHORT="deadbee",
            DIRTY=False,
            EDITABLE=False,
            BUILD_DATE=None,
        ),
    )

    payload = version.as_dict()
    assert payload["base_version"] == "0.0.1.dev1"
    assert payload["pretty_version"] == "0.0.1.dev1 (feature.cool) - deadbee"
    assert payload["dirty"] is False
    assert payload["editable"] is False

    meta = version.get_producer_metadata()
    assert meta["pretty_version"] == payload["pretty_version"]
    assert meta["git"]["branch"] == "feature.cool"
    assert meta["git"]["sha_short"] == "deadbee"
    assert meta["git"]["dirty"] is False
    assert meta["git"]["editable"] is False


def test_stamped_metadata_preferred(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="3.0.0",
            VERSION="3.0.0",
            BRANCH="feature.topic",
            GIT_SHA="ffffffffffffffff",
            GIT_SHA_SHORT="fffffff",
            DIRTY=True,
            EDITABLE=True,
            BUILD_DATE="2025-01-01T00:00:00Z",
        ),
    )

    info = version.get_build_info()

    assert info.git_sha == "ffffffffffffffff"
    assert info.git_sha_short == "fffffff"
    assert info.dirty is True
    assert info.editable is True
    assert info.build_date == "2025-01-01T00:00:00Z"
    assert info.branch == "feature.topic"


def test_identifier_and_flags(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="1.2.3",
            VERSION="1.2.3",
            BRANCH=None,
            GIT_SHA="abcdef0123456789abcdef0123456789abcdef01",
            GIT_SHA_SHORT=None,
            DIRTY=True,
            EDITABLE=True,
            BUILD_DATE="2025-01-01T00:00:00Z",
        ),
    )

    info = version.get_build_info()

    assert info.build_identifier == "1.2.3 - abcdef0"
    assert info.dirty is True
    assert info.editable is True


def test_producer_metadata_shape(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        version,
        "_STAMPED_VERSION",
        SimpleNamespace(
            BASE_VERSION="2.0.0",
            VERSION="2.0.0",
            BRANCH=None,
            GIT_SHA="1234567890abcdef",
            GIT_SHA_SHORT=None,
            DIRTY=False,
            EDITABLE=False,
            BUILD_DATE="2025-01-02T03:04:05Z",
        ),
    )

    info = version.get_build_info()
    meta = version.get_producer_metadata()

    assert info.version == "2.0.0"
    assert info.git_sha == "1234567890abcdef"
    assert info.git_sha_short == "1234567"
    assert info.dirty is False
    assert info.editable is False
    assert meta["version"] == info.version
    assert meta["base_version"] == info.base_version
    assert meta["pretty_version"] == info.build_identifier
    assert meta["git"]["sha"] == "1234567890abcdef"
    assert meta["git"]["sha_short"] == "1234567"
    assert meta["git"]["dirty"] is False
    assert meta["git"]["editable"] is False
    assert meta["build_date"] == "2025-01-02T03:04:05Z"
