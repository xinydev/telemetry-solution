# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from hashlib import sha1
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from typing import Dict, List, Optional, Sequence, Tuple, Type, TypedDict, Union

import pytest

from topdown_tool.cmn_probe.cmn_factory import CmnProbeFactory
from topdown_tool.cmn_probe.cmn_probe import CmnProbe
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf import (
    Perf,
    PerfEventGroup,
    PerfRecords,
    PerfTimedResults,
    Uncore,
)
from topdown_tool.perf.perf_factory import PerfFactory
from topdown_tool.probe.probe import Probe
from tests.cmn_probe.helpers import assert_reference_file, assert_reference_text


BASE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = BASE_DIR / "fixtures"
OUTPUT_DIR = BASE_DIR / "stdout"


class FakePerf(Perf):
    """
    Implementation of Perf for CMN tests. Returns predictable pseudo-random event
    count values by using initial 4 bytes of SHA-1 hash of perf event name string.
    Uses a threading.Event to communicate with test function to inform it to request
    capture stop. Saves first event group to know when capture loop repeats and then
    notifies that stop should be requested.
    """

    first_events_groups: Optional[Sequence[PerfEventGroup]] = None
    capture_event: Optional[Event] = None

    @classmethod
    def get_cmn_frequency(cls, cmn_index: int) -> float:
        return 2150000000.0

    @classmethod
    def get_cmn_mux_interval(cls, cmn_index: int) -> int:
        """
        This is used by the probe to calculate the minimum expected amount of time that
        perf needs to capture all events. It is not used for any other purpose in the
        probe and it isn't used in this fake perf implementation either. Therefore, we
        can safely return zero here.
        """
        return 0

    def __init__(self, *, perf_args: Optional[str] = None, interval: Optional[int] = None):
        pass

    @property
    def max_event_count(self) -> int:
        raise AssertionError("Not used for test")

    def enable(self) -> None:
        pass

    def disable(self) -> None:
        pass

    def start(
        self,
        events_groups: Sequence[PerfEventGroup],
        output_filename: str,
        pid: Optional[int] = None,
        cores: Optional[Sequence[int]] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.events_groups = events_groups
        self.block = False

        if FakePerf.first_events_groups is None:
            FakePerf.first_events_groups = events_groups
            return

        if FakePerf.first_events_groups == events_groups:
            self.block = True
            self._block_event = Event()
            capture_event = FakePerf.capture_event
            if capture_event is None:
                raise AssertionError("capture_event not set")
            capture_event.set()

    def stop(self) -> None:
        if self.block:
            self._block_event.set()

    def wait(self) -> None:
        if self.block:
            self._block_event.wait()
            FakePerf.first_events_groups = None

    def get_perf_result(self) -> PerfRecords:
        results = {Uncore: {None: {}}}
        for group in self.events_groups:
            results[Uncore][None][group] = tuple(
                int.from_bytes(
                    sha1(event.perf_name().encode("utf-8")).digest()[:4], byteorder="big"
                )
                for event in group
            )
        return results

    @classmethod
    def get_pmu_counters(cls, core: int) -> int:
        raise AssertionError("Not used for test")

    @staticmethod
    def have_perf_privilege() -> bool:
        raise AssertionError("Not used for test")

    @classmethod
    def get_midr_value(cls, core: int) -> int:
        raise AssertionError("Not used for test")

    @classmethod
    def update_perf_path(cls, perf_path: str) -> None:
        raise AssertionError("Not used for test")

    @classmethod
    def get_cmn_version(cls) -> Dict[int, Union[int, str]]:
        raise AssertionError("Not used for test")

    def use_parser_for_class(self, probe_class: Probe) -> None:
        raise AssertionError("Not used for test")

    def prepare_perf_command_line(self) -> Tuple[str, Tuple[str]]:
        raise AssertionError("Not used for test")

    def parse_perf_data(self, data: dict) -> PerfTimedResults:
        raise AssertionError("Not used for test")


class FakePerfFactory(PerfFactory):
    @staticmethod
    def get_platform_class() -> Type[Perf]:
        return FakePerf

    def create(self) -> Perf:
        return FakePerf()


class Scenario(TypedDict):
    capture_per_device_id: bool
    groups: Optional[List[str]]
    collect_by: CollectBy


class Setup(TypedDict):
    tmp_dir: Path
    capture_event: Event


@pytest.fixture
def setup_environment(monkeypatch) -> Setup:
    monkeypatch.setenv("COLUMNS", "160")

    capture_event = Event()
    FakePerf.capture_event = capture_event
    FakePerf.first_events_groups = None

    with TemporaryDirectory() as tmp_dir:
        yield {
            "tmp_dir": Path(tmp_dir),
            "capture_event": capture_event,
        }

    FakePerf.capture_event = None
    FakePerf.first_events_groups = None


def _build_factory() -> CmnProbeFactory:
    cmn_factory = CmnProbeFactory()
    cmn_factory.conf.cmn_specification = str(FIXTURES_DIR / "cmn-700.json")
    cmn_factory.conf.cmn_mesh_layout_input = str(FIXTURES_DIR / "topology.json")
    return cmn_factory


def _set_default_cmns(cmn_factory: CmnProbeFactory) -> None:
    cmn_factory.cmns = {
        0: 0x43C00,
        1: 0x43C00,
    }


def _reference_suffix(scenario: Scenario) -> str:
    parts: List[str] = []
    if scenario["collect_by"] == CollectBy.METRIC:
        parts.append("collect_by_metric")
    elif scenario["collect_by"] == CollectBy.NONE:
        parts.append("collect_by_none")

    if scenario["groups"]:
        parts.extend(group.lower() for group in scenario["groups"])

    if scenario["capture_per_device_id"]:
        parts.append("capture_per_device_id")

    return f"__{'__'.join(parts)}" if parts else ""


def _reference_name(prefix: str, scenario: Scenario, ext: str) -> str:
    return f"{prefix}{_reference_suffix(scenario)}.{ext}"


def _metrics_output_name(cmn_index: int, groups: Optional[List[str]]) -> str:
    if groups is None:
        return f"experimental_topdown_cmn_700_r0p0_index_{cmn_index}_metrics.csv"
    return f"cmn_700_r0p0_index_{cmn_index}_metrics.csv"


SCENARIOS: List[Scenario] = [
    {"capture_per_device_id": False, "groups": None, "collect_by": CollectBy.NONE},
    {"capture_per_device_id": False, "groups": None, "collect_by": CollectBy.METRIC},
    {
        "capture_per_device_id": False,
        "groups": ["HNI_AXI_Egress_Traffic"],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": True,
        "groups": ["HNI_AXI_Egress_Traffic"],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": False,
        "groups": ["HNI_AXI_Egress_Traffic"],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": True,
        "groups": ["HNI_AXI_Egress_Traffic"],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": False,
        "groups": ["HNS_Req_Opcode_Mix", "RNI_CHI_Egress_Traffic"],
        "collect_by": CollectBy.NONE,
    },
    # capture_per_device_id=True variants for the above group set are disabled.
    {
        "capture_per_device_id": False,
        "groups": ["HNS_Req_Opcode_Mix", "RNI_CHI_Egress_Traffic"],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": False,
        "groups": [
            "RND_CHI_Egress_Traffic",
            "CCG_RA_TRK_Effectiveness",
            "CCG_HA_TRK_Effectiveness",
        ],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": True,
        "groups": [
            "RND_CHI_Egress_Traffic",
            "CCG_RA_TRK_Effectiveness",
            "CCG_HA_TRK_Effectiveness",
        ],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": False,
        "groups": [
            "RND_CHI_Egress_Traffic",
            "CCG_RA_TRK_Effectiveness",
            "CCG_HA_TRK_Effectiveness",
        ],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": True,
        "groups": [
            "RND_CHI_Egress_Traffic",
            "CCG_RA_TRK_Effectiveness",
            "CCG_HA_TRK_Effectiveness",
        ],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": False,
        "groups": [
            "CCG_LA_Effectiveness",
            "HNS_Analysis_Rate",
            "SNF_Ingress_Traffic",
            "RNF_CHI_Egress_Traffic",
            "CCG_Req_Opcode_Mix",
        ],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": True,
        "groups": [
            "CCG_LA_Effectiveness",
            "HNS_Analysis_Rate",
            "SNF_Ingress_Traffic",
            "RNF_CHI_Egress_Traffic",
            "CCG_Req_Opcode_Mix",
        ],
        "collect_by": CollectBy.NONE,
    },
    {
        "capture_per_device_id": False,
        "groups": [
            "CCG_LA_Effectiveness",
            "HNS_Analysis_Rate",
            # "SNF_Ingress_Traffic",  # fails with no optimizer
            "RNF_CHI_Egress_Traffic",
            "CCG_Req_Opcode_Mix",
        ],
        "collect_by": CollectBy.METRIC,
    },
    {
        "capture_per_device_id": True,
        "groups": [
            "CCG_LA_Effectiveness",
            "HNS_Analysis_Rate",
            # "SNF_Ingress_Traffic",  # fails with no optimizer
            "RNF_CHI_Egress_Traffic",
            "CCG_Req_Opcode_Mix",
        ],
        "collect_by": CollectBy.METRIC,
    },
    # Test Topdown Groups with "capture_per_device_id"
    {
        "capture_per_device_id": True,
        "groups": [
            "CMN_Requestor_Target_Characterization_Level_One",
            "CMN_Requestor_Bandwidth",
        ],
        "collect_by": CollectBy.NONE,
    },
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_cmn_capture(regen_reference_mode, capsys, setup_environment, scenario: Scenario) -> None:
    tmp_dir = setup_environment["tmp_dir"]
    capture_event = setup_environment["capture_event"]

    cmn_factory = _build_factory()
    cmn_factory.conf.cmn_list = False
    cmn_factory.conf.cmn_generate_metrics_csv = True
    cmn_factory.conf.cmn_generate_events_csv = True
    cmn_factory.conf.collect_by = scenario["collect_by"]
    cmn_factory.conf.capture_per_device_id = scenario["capture_per_device_id"]
    cmn_factory.conf.groups = scenario["groups"]
    _set_default_cmns(cmn_factory)

    cmn_probes: Tuple[CmnProbe] = cmn_factory.create(
        True, str(tmp_dir), FakePerfFactory()
    )
    cmn_probe: CmnProbe = cmn_probes[0]

    cmn_probe.create_schedule()
    cmn_probe.start_capture()
    capture_event.wait()
    cmn_probe.stop_capture()

    # Fake no-op multipliers
    cmn_probe.capture_thread.join()
    cmn_probe.total_running_time = 1.0

    for group_name in cmn_probe.group_data:
        cmn_probe.group_data[group_name]["group_running_time"] = 1.0

    cmn_probe.output()

    events_reference = _reference_name("events", scenario, "csv")
    assert_reference_file(
        tmp_dir / "cmn" / "cmn_700_r0p0_events.csv",
        OUTPUT_DIR / events_reference,
        regen_reference_mode,
    )

    for cmn_index in (0, 1):
        metrics_reference = _reference_name(f"metrics_{cmn_index}", scenario, "csv")
        metrics_output = _metrics_output_name(cmn_index, scenario["groups"])
        assert_reference_file(
            tmp_dir / "cmn" / metrics_output,
            OUTPUT_DIR / metrics_reference,
            regen_reference_mode,
        )

    stdout_reference = _reference_name("stdout", scenario, "txt")
    captured = capsys.readouterr()
    assert_reference_text(
        captured.out, OUTPUT_DIR / stdout_reference, regen_reference_mode
    )


def test_cmn_factory_list(regen_reference_mode, capsys, setup_environment) -> None:
    cmn_factory = _build_factory()
    cmn_factory.conf.cmn_list = True

    cmn_factory.create(perf_factory_instance=FakePerfFactory())

    captured = capsys.readouterr()
    assert_reference_text(
        captured.out, OUTPUT_DIR / "cmn_list.txt", regen_reference_mode
    )


LIST_OPTIONS = {
    "--cmn-list-devices": ("cmn_list_devices", True, "cmn_list_devices.txt"),
    "--cmn-list-events": ("cmn_list_events", [], "cmn_list_events.txt"),
    "--cmn-list-metrics": ("cmn_list_metrics", [], "cmn_list_metrics.txt"),
    "--cmn-list-groups": ("cmn_list_groups", [], "cmn_list_groups.txt"),
}


@pytest.mark.parametrize("list_option", LIST_OPTIONS)
def test_cmn_probe_list(
    regen_reference_mode, capsys, setup_environment, list_option: str
) -> None:
    attr_name, attr_value, reference_file = LIST_OPTIONS[list_option]

    cmn_factory = _build_factory()
    cmn_factory.conf.cmn_list = False
    _set_default_cmns(cmn_factory)

    setattr(cmn_factory.conf, attr_name, attr_value)

    cmn_factory.create(perf_factory_instance=FakePerfFactory())

    captured = capsys.readouterr()
    assert_reference_text(
        captured.out, OUTPUT_DIR / reference_file, regen_reference_mode
    )


def test_fake_perf_unimplemented_methods_raise() -> None:
    perf = FakePerf()

    with pytest.raises(AssertionError, match="Not used for test"):
        _ = perf.max_event_count
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.get_pmu_counters(0)
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.have_perf_privilege()
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.get_midr_value(0)
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.update_perf_path("perf")
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.get_cmn_version()
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.use_parser_for_class(Probe)
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.prepare_perf_command_line()
    with pytest.raises(AssertionError, match="Not used for test"):
        perf.parse_perf_data({})

    FakePerf.first_events_groups = None
    FakePerf.capture_event = None
    events_groups: Sequence[PerfEventGroup] = (tuple(),)
    perf.start(events_groups, "out")
    with pytest.raises(AssertionError, match="capture_event not set"):
        perf.start(events_groups, "out")
