# SPDX-License-Identifier: Apache-2.0
# Copyright 2025

import json
from pathlib import Path

import pytest
from typing import List, Optional

from topdown_tool.perf.windows_coordinator import WperfCoordinator
from topdown_tool.perf.perf import Cpu, Uncore  # used in assertions
from topdown_tool.perf.windows_perf_parser import parse_windows_perf_json


# test for parse_windows_perf_json
def test_parse_windows_perf_json_skips_fixed_and_preserves_order(tmp_path):
    # Sample structured like the attached wperf JSON (core 0 only, "fixed" first), inside a timeline.
    sample = {
        "timeline": [
            {
                "Time_elapsed": 1.069,
                "core": {
                    "Kernel_mode": False,
                    "cores": [
                        {
                            "core_number": 0,
                            "Multiplexing": False,
                            "Performance_counter": [
                                {
                                    "counter_value": 44715350,
                                    "event_name": "cycle",
                                    "event_idx": "fixed",
                                    "event_note": "e",
                                },
                                {
                                    "counter_value": 44715350,
                                    "event_name": "cpu_cycles",
                                    "event_idx": "0x11",
                                    "event_note": "g0",
                                },
                                {
                                    "counter_value": 26604997,
                                    "event_name": "inst_retired",
                                    "event_idx": "0x08",
                                    "event_note": "g0",
                                },
                                {
                                    "counter_value": 34270127,
                                    "event_name": "inst_spec",
                                    "event_idx": "0x1b",
                                    "event_note": "g0",
                                },
                                {
                                    "counter_value": 155062,
                                    "event_name": "l2d_tlb_refill",
                                    "event_idx": "0x2d",
                                    "event_note": "g0",
                                },
                                {
                                    "counter_value": 0,
                                    "event_name": "sve_inst_spec",
                                    "event_idx": "0x8006",
                                    "event_note": "g0",
                                },
                            ],
                        }
                    ],
                    "overall": {},
                    "ts_metric": {},
                },
                "dsu": {"l3metric": {}, "overall": {}},
                "dmc": {"pmu": {}, "ddr": {}},
            }
        ],
        "Time_elapsed": 1.069,
    }

    json_path = tmp_path / "wperf.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    out = parse_windows_perf_json(str(json_path))

    # Timeline present -> bucket keyed by accumulated time (here single entry: 1.069)
    assert set(out.keys()) == {1.069}
    bucket = out[1.069]

    # 1) one core present
    assert set(bucket.keys()) == {0}

    # 2) "fixed" is skipped, order of remaining event_idx values is preserved
    expected_event_idxs = ["0x11", "0x08", "0x1b", "0x2d", "0x8006"]
    got_event_idxs = [eid for (eid, _note, _val) in bucket[0]]
    assert got_event_idxs == expected_event_idxs

    # 3) values are floats
    assert all(isinstance(val, float) for (_eid, _note, val) in bucket[0])


def test_parser_handles_string_numbers_and_emits_nan_on_bad_values(tmp_path):
    # Verify float normalization from strings & NaN fallback, inside a timeline bucket.
    sample = {
        "timeline": [
            {
                "Time_elapsed": 0.5,
                "core": {
                    "cores": [
                        {
                            "core_number": 0,
                            "Performance_counter": [
                                {"counter_value": "12345", "event_idx": "0x01"},
                                {"counter_value": "not-a-number", "event_idx": "0x02"},
                            ],
                        }
                    ],
                    "overall": {},
                },
            }
        ],
        "Time_elapsed": 0.5,
    }

    json_path = tmp_path / "wperf2.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    out = parse_windows_perf_json(str(json_path))

    # Timeline present -> bucket keyed by accumulated time (0.5)
    assert set(out.keys()) == {0.5}
    bucket = out[0.5]
    assert set(bucket.keys()) == {0}

    vals = [v for (_eid, _note, v) in bucket[0]]
    assert vals[0] == 12345.0
    assert vals[1] is None


def test_timeline_accumulates_across_multiple_entries_preserving_core_data(tmp_path):
    # Two timeline entries with elapsed 0.5s each -> buckets at 0.5 and 1.0
    entry1 = {
        "Time_elapsed": 0.5,
        "core": {
            "Kernel_mode": False,
            "cores": [
                {
                    "core_number": 1,
                    "Performance_counter": [
                        {"counter_value": 10, "event_idx": "0xA", "event_note": "g0"},
                        {"counter_value": 20, "event_idx": "0xB", "event_note": "g1"},
                    ],
                }
            ],
            "overall": {},
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    entry2 = {
        "Time_elapsed": 0.5,
        "core": {
            "Kernel_mode": False,
            "cores": [
                {
                    "core_number": 1,
                    "Performance_counter": [
                        {"counter_value": 30, "event_idx": "0xA", "event_note": "g0"},
                        {"counter_value": 40, "event_idx": "0xB", "event_note": "g1"},
                    ],
                }
            ],
            "overall": {},
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    sample = {"timeline": [entry1, entry2], "Time_elapsed": 1.0}

    json_path = tmp_path / "wperf_timeline.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    out = parse_windows_perf_json(str(json_path))

    # Keys should be accumulated times: 0.5, then 1.0
    times = sorted(out.keys())
    assert times == [0.5, 1.0]

    # Check contents of each bucket for core 1, preserving order and notes
    b1 = out[0.5][1]
    b2 = out[1.0][1]

    assert b1[0] == ("0xA", "g0", 10.0)
    assert b1[1] == ("0xB", "g1", 20.0)
    assert b2[0] == ("0xA", "g0", 30.0)
    assert b2[1] == ("0xB", "g1", 40.0)


def test_timeline_multiple_entries_with_multiple_cores_and_overall(tmp_path):
    # Two entries with different cores and systemwide overall present in each.
    entry1 = {
        "Time_elapsed": 0.25,
        "core": {
            "Kernel_mode": True,
            "cores": [
                {
                    "core_number": 0,
                    "Performance_counter": [
                        {"counter_value": 100, "event_idx": "0x01"},
                    ],
                }
            ],
            "overall": {
                "Systemwide_Overall_Performance_Counters": [
                    {"counter_value": 999, "event_idx": "0xFF"}
                ]
            },
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    entry2 = {
        "Time_elapsed": 0.75,
        "core": {
            "Kernel_mode": True,
            "cores": [
                {
                    "core_number": 2,
                    "Performance_counter": [
                        {"counter_value": 200, "event_idx": "0x02"},
                    ],
                }
            ],
            "overall": {
                "Systemwide_Overall_Performance_Counters": [
                    {"counter_value": 888, "event_idx": "0xEE"}
                ]
            },
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    sample = {"timeline": [entry1, entry2], "Time_elapsed": 1.0}

    json_path = tmp_path / "wperf_multi_core_overall.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    out = parse_windows_perf_json(str(json_path))

    # Accumulated keys: 0.25, then 1.0 (0.25 + 0.75)
    times = sorted(out.keys())
    assert times == [0.25, 1.0]

    # First bucket: core 0 present
    b_first = out[0.25]
    assert set(b_first.keys()) == {0}
    assert b_first[0][0] == ("0x01", None, 100.0)

    # Second bucket: core 2 present
    b_second = out[1.0]
    assert set(b_second.keys()) == {2}
    assert b_second[2][0] == ("0x02", None, 200.0)


def test_timeline_multi_entries_skip_fixed_each_entry(tmp_path):
    # Ensure 'fixed' counters are skipped independently within each timeline entry and order is preserved.
    e1 = {
        "Time_elapsed": 0.4,
        "core": {
            "cores": [
                {
                    "core_number": 3,
                    "Performance_counter": [
                        {"counter_value": 111, "event_idx": "fixed", "event_note": "e"},
                        {"counter_value": 222, "event_idx": "0x10", "event_note": "g0"},
                        {"counter_value": 333, "event_idx": "0x11", "event_note": "g1"},
                    ],
                }
            ],
            "overall": {},
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    e2 = {
        "Time_elapsed": 0.6,
        "core": {
            "cores": [
                {
                    "core_number": 3,
                    "Performance_counter": [
                        {"counter_value": 555, "event_idx": "fixed", "event_note": "e"},
                        {"counter_value": 666, "event_idx": "0x10", "event_note": "g0"},
                        {"counter_value": 777, "event_idx": "0x11", "event_note": "g1"},
                    ],
                }
            ],
            "overall": {},
        },
        "dsu": {"l3metric": {}, "overall": {}},
        "dmc": {"pmu": {}, "ddr": {}},
    }
    sample = {"timeline": [e1, e2], "Time_elapsed": 1.0}

    json_path = tmp_path / "wperf_skip_fixed_each_entry.json"
    json_path.write_text(json.dumps(sample), encoding="utf-8")

    out = parse_windows_perf_json(str(json_path))

    # Accumulated keys: 0.4 then 1.0 (0.4 + 0.6)
    times = sorted(out.keys())
    assert times == [0.4, 1.0]

    # In each bucket for core 3, 'fixed' must be absent and order preserved.
    b_first = out[0.4][3]
    b_second = out[1.0][3]

    assert [eid for (eid, note, _v) in b_first] == ["0x10", "0x11"]
    assert [eid for (eid, note, _v) in b_second] == ["0x10", "0x11"]

    # Values are floats; notes preserved
    assert b_first[0] == ("0x10", "g0", 222.0)
    assert b_first[1] == ("0x11", "g1", 333.0)
    assert b_second[0] == ("0x10", "g0", 666.0)
    assert b_second[1] == ("0x11", "g1", 777.0)


# tests for WperfCoordinator functions
# --- helpers ---------------------------------------------------------------


class _FakePopen:
    """Collect the last command and pretend to be a running process."""

    last_cmd = None

    def __init__(self, cmd, *args, **kwargs):
        type(self).last_cmd = list(cmd)
        self.pid = 4242

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class FakeEvent:
    """Minimal stand-in for PerfEvent: only perf_name() is used by coordinator."""

    def __init__(self, perf_name: str):
        self._n = perf_name

    def perf_name(self) -> str:
        return self._n

    def __repr__(self) -> str:
        return f"FakeEvent({self._n})"


class _FakeProbe:
    """Matches the attributes WperfCoordinator reads from WindowsPerf."""

    def __init__(self, pid: str, cores, interval_ms: Optional[int]):
        self.windows_perf_instance_id = pid
        self._cores = list(cores or [])
        self._interval = interval_ms
        self._events_groups: List[List[FakeEvent]] = []
        self._received = None

    def get_events_groups(self):
        return self._events_groups

    def get_cores(self):
        return self._cores

    def get_interval(self) -> Optional[int]:
        return self._interval

    def set_results(self, result) -> None:
        self._collected_result = result


@pytest.fixture(autouse=True)
def reset_singleton():
    # Ensure a fresh coordinator for every test
    WperfCoordinator.get_instance().cleanup()
    yield
    WperfCoordinator.get_instance().cleanup()


# --- tests: _launch_combined_wperf ----------------------------------------


def test_launch_combined_wperf_builds_cmd_and_files(tmp_path, monkeypatch):
    # Patch Popen
    monkeypatch.setattr("topdown_tool.perf.windows_coordinator.Popen", _FakePopen)

    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")

    # Two probes → one combined launch. First interval wins.
    p1 = _FakeProbe("p1", cores=[0], interval_ms=100)
    p1._events_groups = [[FakeEvent("r0x11"), FakeEvent("r0x08")]]
    coord.register(p1)

    p2 = _FakeProbe("p2", cores=[2], interval_ms=200)  # ignored interval
    p2._events_groups = [[FakeEvent("r0x2d")], [FakeEvent("r0x8006")]]
    coord.register(p2)

    # Launch directly (private is fine for unit test)
    coord._launch_combined_wperf()  # noqa: SLF001

    # Validate command line
    cmd = _FakePopen.last_cmd
    assert cmd[:2] == ["wperf", "stat"]
    assert "--json" in cmd
    assert "-o" in cmd
    assert "-e" in cmd
    # Interval must be the first probe's 100ms
    assert "-I" in cmd
    i = cmd.index("-I")
    assert cmd[i + 1] == "100"

    # Validate we wrote an events cmdfile and used @file
    at_file_arg = cmd[cmd.index("-e") + 1]
    assert at_file_arg.startswith("@")
    cmdfile = Path(at_file_arg[1:])
    assert cmdfile.exists()
    contents = cmdfile.read_text(encoding="utf-8")

    # Expect per-core segments (sorted by core), groups preserved:
    #   core_0/{r0x11,r0x08}/,core_2/{r0x2d},{r0x8006}/
    # Order within each group matches the group list we gave.
    assert "core_0/{" in contents and "core_2/{" in contents
    assert "{r0x11,r0x08}" in contents
    assert "{r0x2d}" in contents
    assert "{r0x8006}" in contents

    # Output JSON path recorded on coordinator
    assert coord._output_file is not None  # noqa: SLF001
    assert str(coord._output_file).endswith(".json")


def test_launch_combined_wperf_handles_inactive_or_empty(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("topdown_tool.perf.windows_coordinator.Popen", _FakePopen)

    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")

    # One probe deactivated; another active but without events -> nothing to launch
    p1 = _FakeProbe("p1", cores=[0], interval_ms=100)
    coord.register(p1)
    coord.deactivate(p1)
    p2 = _FakeProbe("p2", cores=[1], interval_ms=100)
    # p2 has no events_groups set → should not launch
    coord.register(p2)
    # Reset sentinel so this test doesn't see leftovers from previous tests
    _FakePopen.last_cmd = None

    caplog.clear()
    coord._launch_combined_wperf()  # noqa: SLF001

    # Should NOT have launched Popen
    assert _FakePopen.last_cmd is None

    # And we should have logged the warning
    assert any("No event groups to launch" in rec.message for rec in caplog.records)


# --- tests: _filter_result_for_probe --------------------------------------


def test_filter_result_for_probe_groups_and_timestamps(tmp_path):
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")

    # Make _filter_result_for_probe store values by timestamp (not None)
    coord._interval = 1000  # noqa: SLF001  # pretend we had an interval set

    # ParsedCounters-like structure from the parser:
    # ts -> core_id -> [(event_idx, event_note, value), ...]
    records = {
        0.5: {
            1: [("0x0A", "g0", 10.0), ("0x0B", "g1", 20.0)],
            -1: [("0xFF", None, 99.0)],
        },
        1.0: {
            1: [("0x0A", "g0", 30.0)],  # 0x0B missing at t=1.0
        },
    }

    # Ask for a group of two events: normalize r0x.. matches 0x.. tokens
    group = (FakeEvent("r0x0a"), FakeEvent("r0x0b"))

    out = coord._filter_instance_results(records, cores=[1], events=[group])  # noqa: SLF001

    # out is PerfRecords: keys are Cpu(core_id) / Uncore()
    assert Cpu(1) in out
    timed = out[Cpu(1)]  # PerfTimedResults mapping

    # Both timestamps present (since interval set)
    assert set(timed.keys()) == {0.5, 1.0}

    # Results are stored by the *group* tuple key; we only care about values.
    vals_t05 = list(timed[0.5].values())[0]  # (10.0, 20.0)
    vals_t10 = list(timed[1.0].values())[0]  # (None, None) because 0x0B missing

    assert vals_t05 == (10.0, 20.0)
    assert vals_t10 == (None, None)

    # Uncore (-1) not requested (cores=[1]); ensure it is absent
    assert all((getattr(k, "core_id", None) != -1) for k in out.keys())


def test_filter_result_prefers_common_note_across_group(monkeypatch, tmp_path):
    """
    When both events in a group share multiple notes (e.g., 'g0' and 'g1'),
    the coordinator should choose a common note; policy prefers non-None notes
    lexicographically (so 'g0' over 'g1').
    """
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")
    coord._interval = 1000  # force timestamp buckets (not None)  # noqa: SLF001

    # ts -> core -> [(token, note, value)]
    records = {
        0.5: {
            0: [
                ("0x10", "g0", 1.0),
                ("0x11", "g0", 2.0),
                ("0x10", "g1", 100.0),
                ("0x11", "g1", 200.0),
            ]
        }
    }

    group = (FakeEvent("r0x10"), FakeEvent("r0x11"))
    out = coord._filter_instance_results(records, cores=[0], events=[group])  # noqa: SLF001

    assert Cpu(0) in out
    timed = out[Cpu(0)]
    assert set(timed.keys()) == {0.5}

    vals = list(timed[0.5].values())[0]
    # Because both tokens have g0 and g1, choose 'g0' by policy → (1.0, 2.0)
    assert vals == (1.0, 2.0)


def test_filter_result_fallback_to_token_only_when_no_common_note(tmp_path):
    """
    If the group’s events do not share a common note (e.g., 0x10 only at 'g0'
    and 0x11 only at 'g1'), fall back to token-only map (first-seen per token).
    """
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")
    coord._interval = 1000  # noqa: SLF001

    # Order first-seen so token-only picks 0x10 -> 1.0 and 0x11 -> 200.0
    records = {
        0.5: {
            0: [
                ("0x10", "g0", 1.0),  # first-seen for 0x10
                ("0x11", "g1", 200.0),  # first-seen for 0x11
            ]
        }
    }

    group = (FakeEvent("r0x10"), FakeEvent("0x11"))
    out = coord._filter_instance_results(records, cores=[0], events=[group])  # noqa: SLF001

    vals = list(out[Cpu(0)][0.5].values())[0]
    assert vals == (1.0, 200.0)


def test_filter_result_missing_event_yields_nan(tmp_path):
    """
    If a requested event token is absent entirely at a timestamp, yield None for the group.
    """
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")
    coord._interval = 1000  # noqa: SLF001

    records = {
        0.5: {
            0: [
                ("0x10", "g0", 1.0),  # 0x11 missing
            ]
        }
    }

    group = (FakeEvent("0x10"), FakeEvent("0x11"))
    out = coord._filter_instance_results(records, cores=[0], events=[group])  # noqa: SLF001

    vals = list(out[Cpu(0)][0.5].values())[0]
    assert vals == (None, None)


def test_filter_result_uncore_included_when_no_core_filter_and_interval_none(tmp_path):
    """
    When cores=[] (no filter), include uncore (-1). Also verify that when
    self._interval is None, the timestamp key is replaced with None.
    """
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")
    coord._interval = None  # ensure we collapse ts to None  # noqa: SLF001

    records = {
        0.5: {
            -1: [("0xFF", None, 99.0)],
            2: [("0x10", "g0", 1.0)],
        }
    }

    group = (FakeEvent("0xFF"),)
    # cores=[] → no filtering; expect Uncore() present
    out = coord._filter_instance_results(records, cores=[], events=[group])  # noqa: SLF001

    assert Uncore() in out
    timed = out[Uncore()]
    # With interval None, key must be None (not 0.5)
    assert set(timed.keys()) == {None}
    vals = list(timed[None].values())[0]
    assert vals == (99.0,)


def test_filter_result_core_filtering_excludes_other_cores(tmp_path):
    """
    If cores=[2], core 1 is excluded; Uncore (-1) is intentionally retained.
    """
    coord = WperfCoordinator.get_instance(perf_path="wperf", output_file=tmp_path / "dummy.json")
    coord._interval = 1234  # noqa: SLF001

    records = {
        0.25: {
            1: [("0xAA", "g0", 5.0)],
            2: [("0xBB", "g0", 6.0)],
            -1: [("0xFF", None, 50.0)],
        }
    }

    group = (FakeEvent("0xBB"),)
    out = coord._filter_instance_results(records, cores=[2], events=[group])  # noqa: SLF001

    # Cpu(1) excluded; Uncore is retained by design
    assert set(out.keys()) == {Cpu(2), Uncore()}
    vals = list(out[Cpu(2)][0.25].values())[0]
    assert vals == (6.0,)
