# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import pytest
import types
import itertools
from typing import Any, Dict, Generic, TypeVar
from topdown_tool.cpu_probe.cpu_probe import CpuProbe
from topdown_tool.cpu_probe.common import CpuAggregate, CpuProbeConfiguration
from topdown_tool.perf.event_scheduler import CollectBy, EventScheduler
from topdown_tool.perf import Cpu, PerfFactory, Uncore
from topdown_tool.cpu_probe.cpu_telemetry_database import Event, TelemetryDatabase
from topdown_tool.cpu_probe.common import COMBINED_STAGES


def make_event(name, code=None):
    return Event(name=name, title=name, description="desc", code=code or 0)


class DummyFakePerf:
    def __init__(self, *a, **k):
        pass

    def get_pmu_counters(self, core):
        return 4  # match fixture expects 4


@pytest.fixture
def metrics_fixture_with_timestamp(test_telemetry_db):
    groupA = test_telemetry_db.groups["topdown_root_group"]
    groupB = test_telemetry_db.groups["stage2_group2"]
    cpu1 = Cpu(1)
    cpu3 = Cpu(3)
    cpuagg = CpuAggregate((cpu1, cpu3))
    timestamp1 = 123.0
    timestamp2 = 234.0

    # Events as tuples of Event objects from db, not strings:
    perf_records: Dict[Any, Any] = {
        cpuagg: {
            timestamp1: {
                test_telemetry_db.metrics["root_metric1"].events: (11.0,),
                test_telemetry_db.metrics["root_metric2"].events: (101.0, 102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (3.0, 9.0, 6.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (7.0, 2.0),
                test_telemetry_db.metrics["shared_metric2"].events: (5.0, 8.0),
            },
            timestamp2: {
                test_telemetry_db.metrics["root_metric1"].events: (111.0,),
                test_telemetry_db.metrics["root_metric2"].events: (1101.0, 1102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (13.0, 19.0, 16.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (
                    17.0,
                    12.0,
                ),
                test_telemetry_db.metrics["shared_metric2"].events: (15.0, 18.0),
            },
        },
        cpu1: {
            timestamp1: {
                test_telemetry_db.metrics["root_metric1"].events: (12.0,),
                test_telemetry_db.metrics["root_metric2"].events: (101.0, 102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (3.0, 9.0, 6.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (7.0, 2.0),
                test_telemetry_db.metrics["shared_metric2"].events: (5.0, 8.0),
            },
            timestamp2: {
                test_telemetry_db.metrics["root_metric1"].events: (111.0,),
                test_telemetry_db.metrics["root_metric2"].events: (1101.0, 1102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (13.0, 19.0, 16.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (
                    17.0,
                    12.0,
                ),
                test_telemetry_db.metrics["shared_metric2"].events: (15.0, 18.0),
            },
        },
        cpu3: {
            timestamp1: {
                test_telemetry_db.metrics["root_metric1"].events: (13.0,),
                test_telemetry_db.metrics["root_metric2"].events: (101.0, 102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (3.0, 9.0, 6.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (7.0, 2.0),
                test_telemetry_db.metrics["shared_metric2"].events: (5.0, 8.0),
            },
            timestamp2: {
                test_telemetry_db.metrics["root_metric1"].events: (111.0,),
                test_telemetry_db.metrics["root_metric2"].events: (1101.0, 1102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (13.0, 19.0, 16.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (
                    17.0,
                    12.0,
                ),
                test_telemetry_db.metrics["shared_metric2"].events: (15.0, 18.0),
            },
        },
    }

    expected: Dict[Any, Any] = {
        cpuagg: {
            timestamp1: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 11.0,
                    test_telemetry_db.metrics["root_metric2"]: 101.0 + 102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 9.0
                    + 3.0
                    - 6.0,  # formula: evt3 + evt12 - evt7
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 7.0
                    * 2.0,  # evt9 * evt3, sorted to evt3, evt9
                    test_telemetry_db.metrics["shared_metric2"]: 5.0 + 8.0,
                },
            },
            timestamp2: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 111.0,
                    test_telemetry_db.metrics["root_metric2"]: 1101.0 + 1102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 19.0
                    + 13.0
                    - 16.0,  # formula: evt3 + evt12 - evt7
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 17.0
                    * 12.0,  # evt9 * evt3, sorted to evt3, evt9
                    test_telemetry_db.metrics["shared_metric2"]: 15.0 + 18.0,
                },
            },
        },
        cpu1: {
            timestamp1: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 12.0,
                    test_telemetry_db.metrics["root_metric2"]: 101.0 + 102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 9.0 + 3.0 - 6.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 7.0 * 2.0,
                    test_telemetry_db.metrics["shared_metric2"]: 5.0 + 8.0,
                },
            },
            timestamp2: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 111.0,
                    test_telemetry_db.metrics["root_metric2"]: 1101.0 + 1102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 19.0 + 13.0 - 16.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 17.0 * 12.0,
                    test_telemetry_db.metrics["shared_metric2"]: 15.0 + 18.0,
                },
            },
        },
        cpu3: {
            timestamp1: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 13.0,
                    test_telemetry_db.metrics["root_metric2"]: 101.0 + 102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 9.0 + 3.0 - 6.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 7.0 * 2.0,
                    test_telemetry_db.metrics["shared_metric2"]: 5.0 + 8.0,
                },
            },
            timestamp2: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 111.0,
                    test_telemetry_db.metrics["root_metric2"]: 1101.0 + 1102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 19.0 + 13.0 - 16.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 17.0 * 12.0,
                    test_telemetry_db.metrics["shared_metric2"]: 15.0 + 18.0,
                },
            },
        },
    }

    return {"groups": [groupA, groupB], "records": perf_records, "expected": expected}


@pytest.fixture
def metrics_fixture_with_timestamp_pid_tracking(test_telemetry_db):
    groupA = test_telemetry_db.groups["topdown_root_group"]
    groupB = test_telemetry_db.groups["stage2_group2"]
    uncore = Uncore()
    timestamp1 = 123.0
    timestamp2 = 234.0

    # Events as tuples of Event objects from db, not strings:
    perf_records: Dict[Any, Any] = {
        uncore: {
            timestamp1: {
                test_telemetry_db.metrics["root_metric1"].events: (12.0,),
                test_telemetry_db.metrics["root_metric2"].events: (101.0, 102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (3.0, 9.0, 6.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (7.0, 2.0),
                test_telemetry_db.metrics["shared_metric2"].events: (5.0, 8.0),
            },
            timestamp2: {
                test_telemetry_db.metrics["root_metric1"].events: (111.0,),
                test_telemetry_db.metrics["root_metric2"].events: (1101.0, 1102.0),
                test_telemetry_db.metrics["shared_metric1"].events: (13.0, 19.0, 16.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (
                    17.0,
                    12.0,
                ),
                test_telemetry_db.metrics["shared_metric2"].events: (15.0, 18.0),
            },
        },
    }

    expected: Dict[Any, Any] = {
        uncore: {
            timestamp1: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 12.0,
                    test_telemetry_db.metrics["root_metric2"]: 101.0 + 102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 9.0 + 3.0 - 6.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 7.0 * 2.0,
                    test_telemetry_db.metrics["shared_metric2"]: 5.0 + 8.0,
                },
            },
            timestamp2: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 111.0,
                    test_telemetry_db.metrics["root_metric2"]: 1101.0 + 1102.0,
                    test_telemetry_db.metrics["shared_metric1"]: 19.0 + 13.0 - 16.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 17.0 * 12.0,
                    test_telemetry_db.metrics["shared_metric2"]: 15.0 + 18.0,
                },
            },
        },
    }

    return {"groups": [groupA, groupB], "records": perf_records, "expected": expected}


@pytest.fixture
def metrics_fixture_without_timestamp(test_telemetry_db):
    groupA = test_telemetry_db.groups["topdown_root_group"]
    groupB = test_telemetry_db.groups["stage2_group2"]
    cpuagg = CpuAggregate((Cpu(1), Cpu(3)))
    # Fixed test values, different from timestamp one
    perf_records = {
        cpuagg: {
            None: {
                test_telemetry_db.metrics["root_metric1"].events: (50.0,),
                test_telemetry_db.metrics["root_metric2"].events: (5.0, 3.0),
                test_telemetry_db.metrics["shared_metric1"].events: (8.0, 13.0, 1.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (4.0, 9.0),
                test_telemetry_db.metrics["shared_metric2"].events: (
                    None,
                    10.0,
                ),  # test None propagation
            }
        }
    }
    expected = {
        cpuagg: {
            None: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 50.0,
                    test_telemetry_db.metrics["root_metric2"]: 5.0 + 3.0,
                    test_telemetry_db.metrics["shared_metric1"]: 13.0 + 8.0 - 1.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 4.0 * 9.0,
                    test_telemetry_db.metrics["shared_metric2"]: None,
                },
            }
        }
    }
    return {"groups": [groupA, groupB], "records": perf_records, "expected": expected}


@pytest.fixture
def metrics_fixture_without_timestamp_pid_tracking(test_telemetry_db):
    groupA = test_telemetry_db.groups["topdown_root_group"]
    groupB = test_telemetry_db.groups["stage2_group2"]
    uncore = Uncore()
    # Fixed test values, different from timestamp one
    perf_records = {
        uncore: {
            None: {
                test_telemetry_db.metrics["root_metric1"].events: (50.0,),
                test_telemetry_db.metrics["root_metric2"].events: (5.0, 3.0),
                test_telemetry_db.metrics["shared_metric1"].events: (8.0, 13.0, 1.0),
                test_telemetry_db.metrics["stage_2_group2_metric1"].events: (4.0, 9.0),
                test_telemetry_db.metrics["shared_metric2"].events: (
                    None,
                    10.0,
                ),  # test None propagation
            }
        }
    }
    expected = {
        uncore: {
            None: {
                groupA: {
                    test_telemetry_db.metrics["root_metric1"]: 50.0,
                    test_telemetry_db.metrics["root_metric2"]: 5.0 + 3.0,
                    test_telemetry_db.metrics["shared_metric1"]: 13.0 + 8.0 - 1.0,
                },
                groupB: {
                    test_telemetry_db.metrics["stage_2_group2_metric1"]: 4.0 * 9.0,
                    test_telemetry_db.metrics["shared_metric2"]: None,
                },
            }
        }
    }
    return {"groups": [groupA, groupB], "records": perf_records, "expected": expected}


def build_fixtures_case_single_cpu_timed():
    """
    Single CPU, timed results, checking hierarchy matching over event content,
    with Group1 (1event), Group2 (5events, overlap), Group3 (3events, partly overlaps with group2)
    (group3 has one event not included in group2)
    """
    evA = make_event("A")
    evB = make_event("B")
    evC = make_event("C")
    evD = make_event("D")
    evE = make_event("E")
    evF = make_event("F")
    # Groups
    group1 = (evA,)  # 1 event
    group2 = (evA, evB, evC, evD, evE)  # 5 events, includes evA from group1
    group3 = (evC, evD, evF)  # 3 events, only evC and evD shared with group2
    cpu = Cpu(0)
    # Provide values for each group at time 0.0, only group3 has a None, and not in groups overlapping with group2
    event_records = {
        cpu: {
            0.0: {
                group1: (1.0,),
                group2: (10.0, 20.0, 30.0, 40.0, 50.0),
                group3: (110.0, 200.0, None),  # Only last value None here
            }
        }
    }
    recorded_groups = [group1, group2, group3]
    # For the single-CPU case, do not return expected_aggregate or expected_agg_records:
    return event_records, recorded_groups, None, None


def build_fixtures_case_three_cpu_no_time():
    # Events for groups (reuse overlaps, group3 has new event)
    evA = make_event("A")
    evB = make_event("B")
    evC = make_event("C")
    evD = make_event("D")
    evE = make_event("E")
    evF = make_event("F")
    group1 = (evA,)
    group2 = (evA, evB, evC, evD, evE)
    group3 = (evC, evD, evF)

    cpu0, cpu1, cpu2 = Cpu(0), Cpu(1), Cpu(2)
    event_records = {
        cpu0: {
            None: {
                group1: (1.0,),
                group2: (1.0, 2.0, 3.0, 4.0, 5.0),
                group3: (9.0, 10.0, 11.0),
            }
        },
        cpu1: {
            None: {
                group1: (3.0,),
                group2: (6.0, 7.0, 8.0, 9.0, 10.0),
                group3: (12.0, 20.0, 21.0),
            }
        },
        cpu2: {
            None: {
                group1: (5.0,),
                group2: (
                    11.0,
                    12.0,
                    13.0,
                    14.0,
                    None,
                ),  # Only one None in group2 (last value)
                group3: (15.0, 22.0, 23.0),
            }
        },
    }
    recorded_groups = [group1, group2, group3]
    expected_aggregate = CpuAggregate((cpu0, cpu1, cpu2))
    # Aggregation: for each group and tuple index, sum if all non-None, else None
    expected_agg_records = {
        None: {
            group1: (1.0 + 3.0 + 5.0,),
            group2: (
                1.0 + 6.0 + 11.0,  # 18.0
                2.0 + 7.0 + 12.0,  # 21.0
                3.0 + 8.0 + 13.0,  # 24.0
                4.0 + 9.0 + 14.0,  # 28.0
                None,  # 5.0 + 10.0 + None
            ),
            group3: (
                9.0 + 12.0 + 15.0,  # 36.0
                10.0 + 20.0 + 22.0,  # 52.0
                11.0 + 21.0 + 23.0,  # 55.0
            ),
        }
    }
    return event_records, recorded_groups, expected_aggregate, expected_agg_records


def build_fixtures_case_three_cpu_two_times():
    evA = make_event("A")
    evB = make_event("B")
    evC = make_event("C")
    evD = make_event("D")
    evE = make_event("E")
    evF = make_event("F")  # Only in group3
    group1 = (evA,)
    group2 = (evA, evB, evC, evD, evE)
    group3 = (evC, evD, evF)
    cpu0, cpu1, cpu2 = Cpu(0), Cpu(1), Cpu(2)

    # Each time, all CPUs supply values for all groups, with only one None overall (in group2/1.0/cpu0)
    event_records = {
        cpu0: {
            0.0: {
                group1: (5.0,),
                group2: (1.0, 2.0, 3.0, 4.0, 5.0),
                group3: (11.0, 12.0, 13.0),
            },
            1.0: {
                group1: (6.0,),
                group2: (7.0, 8.0, 9.0, 10.0, None),
                group3: (21.0, 22.0, 23.0),
            },
        },
        cpu1: {
            0.0: {
                group1: (10.0,),
                group2: (2.0, 6.0, 8.0, 10.0, 12.0),
                group3: (17.0, 18.0, 19.0),
            },
            1.0: {
                group1: (11.0,),
                group2: (12.0, 13.0, 14.0, 15.0, 16.0),
                group3: (27.0, 28.0, 29.0),
            },
        },
        cpu2: {
            0.0: {
                group1: (15.0,),
                group2: (3.0, 4.0, 5.0, 6.0, 7.0),
                group3: (31.0, 32.0, 33.0),
            },
            1.0: {
                group1: (16.0,),
                group2: (17.0, 18.0, 19.0, 20.0, 21.0),
                group3: (37.0, 38.0, 39.0),
            },
        },
    }
    recorded_groups = [group1, group2, group3]
    expected_aggregate = CpuAggregate((cpu0, cpu1, cpu2))
    expected_agg_records = {
        0.0: {
            group1: (5.0 + 10.0 + 15.0,),
            group2: (
                1.0 + 2.0 + 3.0,  # 7.0
                2.0 + 6.0 + 4.0,  # 12.0
                3.0 + 8.0 + 5.0,  # 16.0
                4.0 + 10.0 + 6.0,  # 20.0
                5.0 + 12.0 + 7.0,  # 24.0
            ),
            group3: (
                11.0 + 17.0 + 31.0,  # 59.0
                12.0 + 18.0 + 32.0,  # 62.0
                13.0 + 19.0 + 33.0,  # 65.0
            ),
        },
        1.0: {
            group1: (6.0 + 11.0 + 16.0,),
            group2: (
                7.0 + 12.0 + 17.0,  # 36.0
                8.0 + 13.0 + 18.0,  # 39.0
                9.0 + 14.0 + 19.0,  # 42.0
                10.0 + 15.0 + 20.0,  # 45.0
                None,  # None + 16.0 + 21.0
            ),
            group3: (
                21.0 + 27.0 + 37.0,  # 85.0
                22.0 + 28.0 + 38.0,  # 88.0
                23.0 + 29.0 + 39.0,  # 91.0
            ),
        },
    }
    return event_records, recorded_groups, expected_aggregate, expected_agg_records


@pytest.mark.parametrize(
    "fixture_builder",
    [
        build_fixtures_case_three_cpu_no_time,
        build_fixtures_case_three_cpu_two_times,
    ],
)
def test_compute_aggregate_with_complex_cases(fixture_builder):
    event_records, recorded_groups, expected_aggregate, expected_agg_records = fixture_builder()
    aggregate, agg_records = CpuProbe._compute_aggregate(event_records, recorded_groups)
    # Compare aggregated CPUs
    assert set(aggregate.cpus) == set(expected_aggregate.cpus)
    # Compare aggregates
    assert agg_records == expected_agg_records


@pytest.mark.parametrize(
    "fixture_builder",
    [
        build_fixtures_case_single_cpu_timed,
        build_fixtures_case_three_cpu_no_time,
        build_fixtures_case_three_cpu_two_times,
    ],
)
def test_update_aggregate(monkeypatch, fixture_builder):
    # Arrange
    event_records, recorded_groups, expected_aggregate, expected_agg_records = fixture_builder()
    # Make a copy to check preservation later
    orig_event_records = dict(event_records)
    probe = object.__new__(CpuProbe)
    probe._event_records = dict(event_records)  # Copy so we can insert below
    probe._cores = [cpu.id for cpu in event_records.keys()]
    probe._pid_tracking = False
    Scheduler = types.SimpleNamespace
    probe._event_scheduler = Scheduler(optimized_event_groups=recorded_groups)
    # Act
    probe._update_aggregate()
    # Assert: the original event_records should remain, plus one new CpuAggregate key/value
    actual_keys = set(probe._event_records.keys())
    orig_keys = set(event_records.keys())
    # New key should be the aggregate, keep track of it
    if len(probe._cores) == 1:
        assert expected_aggregate is None
        assert expected_agg_records is None
        assert actual_keys == orig_keys
    else:
        agg_keys = [k for k in actual_keys if isinstance(k, CpuAggregate)]
        assert len(agg_keys) == 1
        agg = agg_keys[0]
        assert set(agg.cpus) == set(expected_aggregate.cpus)
        assert probe._event_records[agg] == expected_agg_records
    # All previous records must still be present with the same value
    for cpu_key in orig_keys:
        assert cpu_key in probe._event_records
        assert probe._event_records[cpu_key] == orig_event_records[cpu_key]


@pytest.mark.parametrize(
    "fake_values,expected_result",
    [
        (
            (1.0, 4.0, 2.0),
            4.0 + 1.0 - 2.0,
        ),  # shared_metric1: formula is "evt 3 + evt12 - evt7" - events are sorted in alphabetical order
        ((None, 2.0, 3.0), None),  # If any value is None, result is None
        (
            (1.0, 2.0, "bad"),
            None,
        ),  # If the formula fails because of a non-float, returns None
    ],
)
def test_compute_metric_with_shared_metric1(
    test_telemetry_db, fake_values, expected_result, mocker
):
    # Use the shared_metric1 from our spec fixture
    metric = test_telemetry_db.metrics["shared_metric1"]
    group = None
    for g in test_telemetry_db.groups.values():
        if metric in g.metrics:
            group = g
            break
    assert group is not None

    # Build a perf_result dict: the scheduler will fetch a tuple at metric.events
    perf_result = {metric.events: fake_values}
    scheduler = mocker.Mock()
    scheduler.retrieve_event_results.return_value = fake_values

    result = CpuProbe._compute_metric(scheduler, perf_result, group, metric)

    if (
        None in fake_values
        or isinstance(expected_result, type)
        or (isinstance(fake_values[-1], str))
    ):
        assert result is None
    else:
        # For valid values, formula is evt 3 + evt12 - evt7, i.e. a+b-c:
        expected_val = fake_values[0] + fake_values[1] - fake_values[2]
        assert result == expected_val


@pytest.mark.parametrize(
    "fixture_name",
    [
        "metrics_fixture_with_timestamp",
        "metrics_fixture_with_timestamp_pid_tracking",
        "metrics_fixture_without_timestamp",
        "metrics_fixture_without_timestamp_pid_tracking",
    ],
)
def test_compute_metrics_explicit_cases(test_telemetry_db, mocker, request, fixture_name):
    # Load the explicit fixture
    fx = request.getfixturevalue(fixture_name)
    groups = fx["groups"]
    records = fx["records"]
    expected = fx["expected"]

    # The "records" dict is structured as {loc: {timestamp: {metric.events: tuple}}}
    # When _compute_metrics looks up event results, it provides (perf_result, group, events)
    # and expects a tuple. We return the right value directly from perf_result.
    def fake_retrieve_event_results(perf_result, group, events):
        return perf_result.get(events, (None,) * len(events))

    scheduler = mocker.Mock()
    scheduler.retrieve_event_results.side_effect = fake_retrieve_event_results
    result = CpuProbe._compute_metrics(groups, records, scheduler)
    assert result == expected


@pytest.mark.parametrize(
    "conf_overrides, expect_groups",
    [
        # Simulate event dump mode (passing some non-None value for cpu_dump_events)
        (
            {"cpu_dump_events": True},
            lambda db: [g.name for g in db.get_all_events_groups(4)],
        ),
        # Pick a real metric group if available in DB fixture
        ({"metric_group": ["stage1_left_group"]}, lambda db: ["stage1_left_group"]),
        # Pick node present in DB
        (
            {"node": "stage1_left_lv1_metric"},
            lambda db: ["stage1_left_group", "stage2_group2", "stage2_group1"],
        ),
        # Test stages 1 only
        ({"stages": [1]}, lambda db: [g.name for g in db.topdown.stage_1_groups]),
        # Test stages 2 only
        ({"stages": [2]}, lambda db: [g.name for g in db.topdown.stage_2_groups]),
        # Default: all stages
        (
            {},
            lambda db: [g.name for g in db.topdown.stage_1_groups + db.topdown.stage_2_groups],
        ),
    ],
)
def test_build_capture_groups_variants(test_telemetry_db, conf_overrides, expect_groups):
    """Test that _build_capture_groups returns the correct set of groups for each config scenario."""
    db = test_telemetry_db

    # Default config, overridden by conf_overrides
    base_conf = CpuProbeConfiguration()
    for k, v in conf_overrides.items():
        setattr(base_conf, k, v)

    # Assume max_events=4 for test
    capture_groups = CpuProbe._build_capture_groups(base_conf, db, 4)
    group_names = [g.name for g in capture_groups]
    expected_names = expect_groups(db)
    # Sets allow different orderings in the DB, but more precise matching can be used if necessary
    assert set(group_names) == set(expected_names)
    # If you want exact (ordered) matching add: assert group_names == expected_names


def test_cpuprobe_constructor_initializes_state_correctly(test_telemetry_spec):
    """Validate CpuProbe constructor forwards config and initializes main attributes correctly."""
    conf = CpuProbeConfiguration()
    conf.metric_group = ["topdown_root_group"]  # simple real group from fixture
    cores = [2, 0, 1]
    fake_factory = PerfFactory()
    fake_factory._impl_class = DummyFakePerf
    probe = CpuProbe(
        conf,
        test_telemetry_spec,
        core_indices=cores,
        capture_data=True,
        base_csv_dir=None,
        perf_factory_instance=fake_factory,
    )

    # Assert forwarding and initializations
    assert probe._db is not None
    assert isinstance(probe._db, TelemetryDatabase)
    assert probe._product_name == "TestCPU"
    assert probe._cores == sorted(cores)
    assert probe._max_events == 4

    # Capture groups structure (should match resolved group names for this config)
    group_names = [g.name for g in probe._capture_groups]
    assert group_names == ["topdown_root_group"]
    # Event scheduler is initialized and has proper type
    assert isinstance(probe._event_scheduler, EventScheduler)
    # Perf instance not started yet
    assert probe._perf_instance is None
    # Event records dict empty
    assert probe._event_records == {}


E = TypeVar("E")  # Type of event


class FakePerfSimple:
    results_queue = []

    def __init__(self, *a, **k):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def get_perf_result(self):
        # Pop (in use order)
        return FakePerfSimple.results_queue.pop(0)

    def get_pmu_counters(self, core):
        return 5


class FakeSchedulerSimple(Generic[E]):
    def __init__(self, event_groups, output_filename, cores):
        # event_groups: [(evt_tuple, ...), ...]
        self.event_groups = event_groups

        self.optimized_event_groups = []
        for group in event_groups:
            for metric in group:
                self.optimized_event_groups.append(metric)
            # g = set([event for metric in group for event in metric])
            # self.optimized_event_groups.append(tuple(g))

        self.chunks = [[g] for g in event_groups]
        self._index = 0

    def get_event_group_iterator(self, split):
        return FakeGroupIterator(self.chunks)

    def retrieve_event_results(
        self,
        perf_result,
        group,
        metric_events,
    ):
        return perf_result[metric_events]

    @classmethod
    def __class_getitem__(cls, item):
        return cls


class FakeGroupIterator:
    def __init__(self, chunks):
        self.chunks = chunks
        self._index = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self._index >= len(self.chunks):
            raise StopIteration
        val = [self.chunks[self._index]]
        self._index += 1
        return val

    def has_next(self):
        return self._index < len(self.chunks)

    def remaining_chunks(self):
        return len(self.chunks) - self._index

    def index(self):
        return self._index


def test_cpuprobe_e2e_two_groups_two_cores(monkeypatch, test_telemetry_spec, test_telemetry_db):

    spec = test_telemetry_spec
    conf = CpuProbeConfiguration()
    conf.metric_group = ["topdown_root_group", "freestanding_group"]
    conf.collect_by = CollectBy.METRIC
    conf.multiplex = False
    cores = [0, 1]

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_probe.EventScheduler", FakeSchedulerSimple)

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_probe.Perf", FakePerfSimple)
    factory = PerfFactory()
    factory._impl_class = FakePerfSimple
    # Construct probe
    probe = CpuProbe(
        conf,
        spec,
        core_indices=cores,
        capture_data=True,
        base_csv_dir=None,
        perf_factory_instance=factory,
    )

    # Extract all the components. We want to use the same DB as the CpuProbe
    db = probe._db

    gA = db.groups["topdown_root_group"]
    gB = db.groups["freestanding_group"]
    c0 = Cpu(0)
    c1 = Cpu(1)
    agg = CpuAggregate((c0, c1))

    # Hardcode PerfSimple return for each run
    # Chunk 1: topdown_root_group
    # Chunk 2: freestanding_group
    evt = db.events
    FakePerfSimple.results_queue = [
        # Chunk for gA
        {
            c0: {
                None: {
                    (evt["evt1"],): (1.0,),
                    (evt["evt2"], evt["evt3"]): (2.0, 3.0),
                    (evt["evt12"], evt["evt3"], evt["evt7"]): (4.0, 5.0, 6.0),
                }
            },
            c1: {
                None: {
                    (evt["evt1"],): (10.0,),
                    (evt["evt2"], evt["evt3"]): (20.0, 30.0),
                    (evt["evt12"], evt["evt3"], evt["evt7"]): (40.0, 50.0, 60.0),
                }
            },
        },
        # Chunk for gB
        {
            c0: {
                None: {
                    (evt["evt10"],): (7.0,),
                    (evt["evt10"], evt["evt11"]): (8.0, 9.0),
                    (evt["evt8"], evt["evt9"]): (11.0, 12.0),
                }
            },
            c1: {
                None: {
                    (evt["evt10"],): (70.0,),
                    (evt["evt10"], evt["evt11"]): (80.0, 90.0),
                    (evt["evt8"], evt["evt9"]): (110.0, 120.0),
                }
            },
        },
    ]

    # Step through capture for 2 groups/chunks
    assert probe.need_capture()
    probe.start_capture(run=1, pids=10)
    probe.stop_capture(run=1, pid=10)
    assert probe.need_capture()
    probe.start_capture(run=2, pids=20)
    probe.stop_capture(run=2, pid=20)
    assert not probe.need_capture()

    # Full expected structure, including aggregate (sums)
    expected = {
        c0: {
            None: {
                gA: {
                    gA.metrics[0]: 1.0,
                    gA.metrics[1]: 5.0,
                    gA.metrics[2]: 3.0,
                },
                gB: {
                    gB.metrics[0]: 7.0,
                    gB.metrics[1]: 17.0,
                    gB.metrics[2]: 23.0,
                },
            },
        },
        c1: {
            None: {
                gA: {
                    gA.metrics[0]: 10.0,
                    gA.metrics[1]: 50.0,
                    gA.metrics[2]: 30.0,
                },
                gB: {
                    gB.metrics[0]: 70.0,
                    gB.metrics[1]: 170.0,
                    gB.metrics[2]: 230.0,
                },
            },
        },
        agg: {
            None: {
                gA: {
                    gA.metrics[0]: 11.0,  # 1.0 + 10.0
                    gA.metrics[1]: 55.0,  # 5.0 + 50.0
                    gA.metrics[2]: 33.0,  # 3.0 + 30.0
                },
                gB: {
                    gB.metrics[0]: 77.0,  # 7.0 + 70.0
                    gB.metrics[1]: 187.0,  # 17.0 + 170.0
                    gB.metrics[2]: 253.0,  # 23.0 + 230.0
                },
            },
        },
    }

    # Compare complete structure
    assert probe.computed_metrics == expected


def test_cpuprobe_e2e_two_groups_pid_tracking(monkeypatch, test_telemetry_spec, test_telemetry_db):

    spec = test_telemetry_spec
    conf = CpuProbeConfiguration()
    conf.metric_group = ["topdown_root_group", "freestanding_group"]
    conf.collect_by = CollectBy.METRIC
    conf.multiplex = False
    cores = [0]

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_probe.EventScheduler", FakeSchedulerSimple)

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_probe.Perf", FakePerfSimple)
    factory = PerfFactory()
    factory._impl_class = FakePerfSimple
    # Construct probe
    probe = CpuProbe(
        conf,
        spec,
        core_indices=cores,
        capture_data=True,
        perf_factory_instance=factory,
        base_csv_dir="fake_dir",
    )

    # Extract all the components. We want to use the same DB as the CpuProbe
    db = probe._db

    gA = db.groups["topdown_root_group"]
    gB = db.groups["freestanding_group"]
    uncore = Uncore()

    # Hardcode PerfSimple return for each run
    # Chunk 1: topdown_root_group
    # Chunk 2: freestanding_group
    evt = db.events
    FakePerfSimple.results_queue = [
        # Chunk for gA
        {
            uncore: {
                None: {
                    (evt["evt1"],): (1.0,),
                    (evt["evt2"], evt["evt3"]): (2.0, 3.0),
                    (evt["evt12"], evt["evt3"], evt["evt7"]): (4.0, 5.0, 6.0),
                }
            },
        },
        # Chunk for gB
        {
            uncore: {
                None: {
                    (evt["evt10"],): (7.0,),
                    (evt["evt10"], evt["evt11"]): (8.0, 9.0),
                    (evt["evt8"], evt["evt9"]): (11.0, 12.0),
                }
            },
        },
    ]

    # Step through capture for 2 groups/chunks
    assert probe.need_capture()
    probe.start_capture(run=1, pids=10)
    probe.stop_capture(run=1, pid=10)
    assert probe.need_capture()
    probe.start_capture(run=2, pids=20)
    probe.stop_capture(run=2, pid=20)
    assert not probe.need_capture()

    # Full expected structure, including aggregate (sums)
    expected = {
        uncore: {
            None: {
                gA: {
                    gA.metrics[0]: 1.0,
                    gA.metrics[1]: 5.0,
                    gA.metrics[2]: 3.0,
                },
                gB: {
                    gB.metrics[0]: 7.0,
                    gB.metrics[1]: 17.0,
                    gB.metrics[2]: 23.0,
                },
            },
        },
    }

    # Compare complete structure
    assert probe.computed_metrics == expected


# ----- output dispatch/mocking interactivity tests for CpuProbe -----


@pytest.fixture
def dummy_probe(mocker, test_telemetry_db):
    """
    Creates a CpuProbe ready for output() dispatch coverage.
    CLI/CSV renderers are mocked to record calls/params only.
    Probe contains a minimal computed_metrics and capture_groups.
    """
    conf = CpuProbeConfiguration()
    probe = CpuProbe.__new__(CpuProbe)
    probe._conf = conf
    probe._db = test_telemetry_db
    probe._product_name = test_telemetry_db.product_name
    probe._cli_renderer = mocker.Mock()
    probe._csv_renderer = mocker.Mock()
    probe._capture_data = True
    probe._capture_groups = list(test_telemetry_db.groups.values())
    probe.computed_metrics = {"irrelevant": "dummy"}
    probe._event_records = {}
    probe._base_csv_dir = "fake_dir"
    return probe


# For list-modes interactivity: we want to check every combination of the 3 "list" flags.
# The other config values (descriptions, show_sample_events, stages) are defaulted to common values so that
# we don't generate an explosion of unrelated orthogonal cases.
#
# In effect, this generates 8 cases via the cartesian product of all True/False for (events, metrics, groups).
@pytest.mark.parametrize(
    "list_ev, list_met, list_grp, descriptions, show_sample_events, stages",
    [(*vals, False, False, [1, 2]) for vals in itertools.product([False, True], repeat=3)]
    + [
        # Add a few targeted checks for propagation/edges
        (False, True, False, True, True, [1, 2]),
        (True, False, False, True, False, [1]),
        (False, False, True, False, False, [2]),
        (True, True, True, True, True, [1, 2]),
        (
            False,
            False,
            False,
            False,
            False,
            [],
        ),  # none active, no stages (nothing called)
    ],
)
def test_list_modes(
    dummy_probe, list_ev, list_met, list_grp, descriptions, show_sample_events, stages
):
    """
    The first 8 cases are the cartesian product of each possible state for:
      - cpu_list_events
      - cpu_list_metrics
      - cpu_list_groups
    All other arguments are left at normal defaults for these.
    The final cases exercise propagation of more flags and edge behaviors.
    """
    conf = dummy_probe._conf
    conf.cpu_list_events = list_ev
    conf.cpu_list_metrics = list_met
    conf.cpu_list_groups = list_grp
    conf.descriptions = descriptions
    conf.show_sample_events = show_sample_events
    conf.stages = stages

    dummy_probe.output()
    if list_ev:
        dummy_probe._cli_renderer.list_events.assert_called_once_with(descriptions)
    else:
        dummy_probe._cli_renderer.list_events.assert_not_called()
    if list_met:
        dummy_probe._cli_renderer.list_metrics.assert_called_once_with(
            descriptions, show_sample_events
        )
    else:
        dummy_probe._cli_renderer.list_metrics.assert_not_called()
    if list_grp:
        dummy_probe._cli_renderer.list_groups.assert_called_once_with(descriptions, stages)
    else:
        dummy_probe._cli_renderer.list_groups.assert_not_called()


@pytest.mark.parametrize(
    "gen_metrics_csv, gen_events_csv",
    [
        (True, False),
        (False, True),
        (True, True),
        (False, False),
    ],
)
def test_output_csv_calls_csv_renderer(dummy_probe, gen_metrics_csv, gen_events_csv):
    dummy_probe._conf.cpu_dump_events = None
    dummy_probe._conf.cpu_generate_metrics_csv = gen_metrics_csv
    dummy_probe._conf.cpu_generate_events_csv = gen_events_csv

    dummy_probe.output()

    # Assert metrics CSV rendering
    if gen_metrics_csv:
        dummy_probe._csv_renderer.render_metric_groups.assert_called_once()
        args, _ = dummy_probe._csv_renderer.render_metric_groups.call_args
        # input validation of arguments
        assert args[0] == dummy_probe.computed_metrics
        assert args[1] == dummy_probe._capture_groups
        assert args[2] == dummy_probe._db
        assert args[3] == dummy_probe._base_csv_dir + "/cpu"
    else:
        dummy_probe._csv_renderer.render_metric_groups.assert_not_called()

    # Assert event CSV rendering
    if gen_events_csv:
        dummy_probe._csv_renderer.dump_events.assert_called_once()
        args, _ = dummy_probe._csv_renderer.dump_events.call_args
        # input validation of arguments
        assert args[0] == dummy_probe._event_records
        assert args[1] == dummy_probe._db
        assert args[2] == dummy_probe._product_name
        assert args[3] == dummy_probe._base_csv_dir + "/cpu"
    else:
        dummy_probe._csv_renderer.dump_events.assert_not_called()


@pytest.mark.parametrize("gen_metrics_csv", [False, True])
def test_output_cpu_dump_events(dummy_probe, gen_metrics_csv):
    dummy_probe._conf.cpu_dump_events = True
    dummy_probe._conf.cpu_generate_metrics_csv = gen_metrics_csv

    dummy_probe.output()

    dummy_probe._csv_renderer.dump_events.assert_called_once()
    dummy_probe._csv_renderer.render_metric_groups.assert_not_called()
    args, _ = dummy_probe._csv_renderer.dump_events.call_args
    assert args[0] == dummy_probe._event_records
    assert args[1] == dummy_probe._db
    assert args[2] == dummy_probe._product_name
    assert args[3] == dummy_probe._base_csv_dir + "/cpu"


@pytest.mark.parametrize("use_combined, use_node", [(True, False), (False, True), (False, False)])
def test_output_render_routes(dummy_probe, use_combined, use_node):
    dummy_probe._conf.cpu_list_events = False
    dummy_probe._conf.cpu_list_metrics = False
    dummy_probe._conf.cpu_list_groups = False
    dummy_probe._conf.cpu_dump_events = None
    dummy_probe._conf.stages = COMBINED_STAGES if use_combined else [1, 2]
    dummy_probe._conf.node = "frontend_stalled_cycles" if use_node else None

    dummy_probe._cli_renderer.render_metrics_tree.reset_mock()
    dummy_probe._cli_renderer.render_metric_groups_stages.reset_mock()

    dummy_probe.output()
    if use_combined or use_node:
        dummy_probe._cli_renderer.render_metrics_tree.assert_called_once()
        dummy_probe._cli_renderer.render_metric_groups_stages.assert_not_called()
    else:
        dummy_probe._cli_renderer.render_metric_groups_stages.assert_called_once()
        dummy_probe._cli_renderer.render_metrics_tree.assert_not_called()
