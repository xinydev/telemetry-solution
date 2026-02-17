# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from pathlib import Path

import pytest

from topdown_tool.cmn_probe.common import Event, Watchpoint
from topdown_tool.cmn_probe.windows_perf_parser import WindowsPerfParser
from topdown_tool.perf.perf import Uncore


def _make_cycle_event(cmn_index: int = 0) -> Event:
    return Event(
        name="SYS_CMN_CYCLES",
        title="cycles",
        description="cycles",
        cmn_index=cmn_index,
        type=3,
        eventid=None,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )


def _make_event(name: str, cmn_index: int = 0, eventid: int = 1) -> Event:
    return Event(
        name=name,
        title=name,
        description=name,
        cmn_index=cmn_index,
        type=0,
        eventid=eventid,
        occupid=None,
        nodeid=None,
        xp_id=None,
    )


def _make_watchpoint(name: str, cmn_index: int = 0, xp_id: int = 1, port: int = 2) -> Watchpoint:
    return Watchpoint(
        name=name,
        title=name,
        description=name,
        cmn_index=cmn_index,
        mesh_flit_dir=0,
        wp_chn_sel=1,
        wp_grp=2,
        wp_mask=0,
        wp_val=0,
        xp_id=xp_id,
        port=port,
        device=None,
    )


def test_prepare_command_line_skips_cycles(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A")
    watchpoint = _make_watchpoint("WP1")

    parser = WindowsPerfParser(
        [(cycles, event_a), (watchpoint,), (cycles,)], perf_instance=object()
    )

    assert parser.before_capture() == ((event_a,), (watchpoint,), ())
    assert parser.cmn_cycles_positions == {0: [{"event_index": 0, "cmn_index": 0}], 2: [{"event_index": 0, "cmn_index": 0}]}

    cmdfile, extra_args = parser.prepare_perf_command_line("run1")

    assert extra_args == ("--enable-dpc-overflow",)
    assert cmdfile is not None
    assert cmdfile.name == "wperf-run1-cmn.cmdline"

    content = Path(cmdfile).read_text(encoding="utf-8")
    assert content == f"{event_a.perf_name()},{watchpoint.perf_name()}"


def test_parse_perf_data_inserts_cycles() -> None:
    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A", eventid=10)

    parser = WindowsPerfParser([(cycles, event_a)], perf_instance=object())

    data = {
        "counting": [
            {
                "mesh": 0,
                "CMN_DTC": [
                    {"event": "cycles", "DTC_domain": 0, "value": 1000},
                    {"event": "EVENT_A", "DTC_domain": 0, "value": 50},
                ],
                "CMN": [
                    {"event": "EVENT_A", "scaled_value": 5},
                    {"event": "EVENT_A", "scaled_value": 5},
                ],
            }
        ]
    }

    records = parser.parse_perf_data(data)

    results = records[Uncore()][None][(cycles, event_a)]
    assert results == (1000.0, 5.0)


def test_parse_perf_data_requires_value() -> None:
    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A", eventid=10)

    parser = WindowsPerfParser([(cycles, event_a)], perf_instance=object())

    data = {
        "counting": [
            {
                "mesh": 0,
                "CMN_DTC": [
                    {"event": "cycles", "DTC_domain": 0, "value": 1000},
                    {"event": "EVENT_A", "DTC_domain": 0, "value": 50},
                ],
                "CMN": [
                    {"event": "EVENT_A", "scaled_value": None},
                    {"event": "EVENT_A", "scaled_value": None},
                ],
            }
        ]
    }

    with pytest.raises(ValueError):
        parser.parse_perf_data(data)


def test_parse_perf_data_uses_value_fallback() -> None:
    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A", eventid=10)

    parser = WindowsPerfParser([(cycles, event_a)], perf_instance=object())

    data = {
        "counting": [
            {
                "mesh": 0,
                "CMN_DTC": [
                    {"event": "cycles", "DTC_domain": 0, "value": 1000},
                    {"event": "EVENT_A", "DTC_domain": 0, "value": 50},
                ],
                "CMN": [
                    {"event": "EVENT_A", "value": 7},
                    {"event": "EVENT_A", "value": 7},
                ],
            }
        ]
    }

    records = parser.parse_perf_data(data)

    results = records[Uncore()][None][(cycles, event_a)]
    assert results == (1000.0, 7.0)


def test_parse_perf_data_timeline_path() -> None:
    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A", eventid=10)

    parser = WindowsPerfParser([(cycles, event_a)], perf_instance=object())

    data = {
        "timeline": [
            [
                {
                    "mesh": 0,
                    "CMN_DTC": [
                        {"event": "cycles", "DTC_domain": 0, "value": 1000},
                        {"event": "EVENT_A", "DTC_domain": 0, "value": 50},
                    ],
                    "CMN": [
                        {"event": "EVENT_A", "scaled_value": 5},
                        {"event": "EVENT_A", "scaled_value": 5},
                    ],
                }
            ]
        ]
    }

    records = parser.parse_perf_data(data)

    results = records[Uncore()][None][(cycles, event_a)]
    assert results == (1000.0, 5.0)


def test_parse_perf_data_missing_mesh_returns_empty() -> None:
    cycles = _make_cycle_event()
    event_a = _make_event("EVENT_A", eventid=10)
    parser = WindowsPerfParser([(cycles, event_a)], perf_instance=object())

    records = parser.parse_perf_data({"counting": [{"CMN": []}]})

    assert records == {}
