# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

import pytest
from .helpers import wp
from topdown_tool.cmn_probe.common import Watchpoint
from topdown_tool.cmn_probe.scheduler import event_from_key


def test_watchpoint_key_roundtrip():
    w1 = wp(xp_id=3, port=11, direction="UP", value=777, mask=0x33, chn_sel=2, grp=1)
    k = w1.key()
    # Key is canonical and encodes all construction fields
    assert k.startswith("WP777:M51:UP:CHN2:GRP1@I0:XP3:P11")
    w2 = event_from_key(k)
    assert isinstance(w2, Watchpoint)
    assert w2 == w1
    # All fields identical
    assert (
        w2.xp_id,
        w2.port,
        w2.mesh_flit_dir,
        w2.wp_val,
        w2.wp_mask,
        w2.wp_chn_sel,
        w2.wp_grp,
    ) == (
        3,
        11,
        0,
        777,
        0x33,
        2,
        1,
    )


def test_watchpoint_duplicate_elimination():
    # Two identical WPs differ in object but not value/hash
    w1 = wp(xp_id=0, port=1, direction="DOWN")
    w2 = wp(xp_id=0, port=1, direction="DOWN")
    s = {w1, w2}
    assert len(s) == 1
    assert list(s)[0] == w1


def test_watchpoint_validation_errors():
    # Negative integer fields should raise
    with pytest.raises(TypeError):
        wp(xp_id=-1, port=2)
    with pytest.raises(TypeError):
        wp(xp_id=1, port=-2)
    with pytest.raises(TypeError):
        wp(xp_id=1, port=2, chn_sel=-1)
    with pytest.raises(TypeError):
        wp(xp_id=1, port=2, grp=-2)
    # Bad direction string
    with pytest.raises(ValueError):
        wp(xp_id=1, port=2, direction="down")
    with pytest.raises(ValueError):
        wp(xp_id=1, port=2, direction="LEFT")
