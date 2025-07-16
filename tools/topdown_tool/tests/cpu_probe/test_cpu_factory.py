# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import argparse
import json
import os
import pytest
from types import SimpleNamespace

from topdown_tool.cpu_probe.cpu_factory import CpuProbeFactory, ArgsError
from topdown_tool.cpu_probe.common import DEFAULT_ALL_STAGES
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.linux_perf import LinuxPerf
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.perf import perf_factory as global_perf_factory

# --- Fixtures ---


def _fake_cpu_midr():
    # Build a MIDR that when processed by CPUDetect.cpu_id yields 0x41d8e
    # MIDR = (implementer << 24) | (part_num << 4)
    implementer = 0x41
    part_num = 0xD8E
    return (implementer << 24) | (part_num << 4)


@pytest.fixture
def fake_cpu_midr():
    return _fake_cpu_midr()


class FakeCPUDetect:
    @staticmethod
    def cpu_count():
        return 2

    @staticmethod
    def cpu_midr(core: int) -> int:
        # Always return the same MIDR
        return _fake_cpu_midr()

    @staticmethod
    def cpu_id(midr: int) -> int:
        implementer = (midr >> 24) & 0xFF
        part_num = (midr >> 4) & 0xFFF
        return (implementer << 12) | part_num


@pytest.fixture
def tmp_metrics_dir(tmp_path):
    # Create a temporary metrics directory with a mapping.json and one telemetry specification file.
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()

    mapping = {"0x41d8e": {"name": "neoverse-n3"}}
    mapping_path = metrics_dir / "mapping.json"
    mapping_path.write_text(json.dumps(mapping))

    # Use the generator function instead of hard-coded spec.
    spec = generate_fake_spec(
        product_name="neoverse-n3",
        part_num="0xd8e",
        implementer="0x41",
        timestamp="dummy",
        description="Test spec for neoverse-n3",
        num_slots=5,
        num_bus_slots=0,
    )
    spec_path = metrics_dir / "neoverse-n3.json"
    spec_path.write_text(json.dumps(spec))

    return str(metrics_dir)


@pytest.fixture
def base_args():
    # Build an argparse.Namespace with default values expected by process_cli_arguments.
    ns = argparse.Namespace()
    ns.cpu = None
    ns.core = None
    ns.sme = None
    ns.cpu_list = False
    ns.cpu_list_groups = False
    ns.cpu_list_metrics = False
    ns.cpu_list_events = False
    ns.cpu_no_multiplex = False
    ns.cpu_collect_by = "metric"
    ns.cpu_metric_group = None
    ns.cpu_node = None
    ns.cpu_level = None
    ns.cpu_stages = "all"
    ns.cpu_descriptions = False
    ns.cpu_show_sample_events = False
    ns.cpu_csv = "dummy.csv"
    ns.events_csv = None
    # For process_cli_arguments error test, add interval attribute
    ns.interval = 1
    # Private arguments
    ns.cpu_dump_events = None
    return ns


# NEW: Generator function for fake Telemetry Specification JSONs.
def generate_fake_spec(
    product_name="default",
    part_num="0x0",
    implementer="0x0",
    architecture="armv9.2",
    pmu_architecture="pmu_v3",
    num_slots=5,
    num_bus_slots=0,
    spec_type="PMU_Specification",
    timestamp="dummy",
    description="Test spec",
):
    return {
        "_type": spec_type,
        "document": {
            "timestamp": timestamp,
            "copyright": "",
            "confidential": False,
            "quality": "Release",
            "license": "Apache-2.0",
            "description": description,
        },
        "product_configuration": {
            "product_name": product_name,
            "part_num": part_num,
            "major_revision": 0,
            "minor_revision": 0,
            "implementer": implementer,
            "architecture": architecture,
            "pmu_architecture": pmu_architecture,
            "num_slots": num_slots,
            "num_bus_slots": num_bus_slots,
        },
        "events": {},
        "metrics": {},
        "groups": {"function": {}, "metrics": {}},
        "methodologies": {
            "topdown_methodology": {
                "title": f"{product_name} methodology title",
                "description": f"{product_name} methodology description",
                "metric_grouping": {"stage_1": [], "stage_2": []},
                "decision_tree": {"root_nodes": [], "metrics": []},
            }
        },
    }


# --- Tests ---


def test_name_method():
    factory = CpuProbeFactory()
    assert factory.name() == "CPU"


def test_is_available_method():
    factory = CpuProbeFactory()
    assert factory.is_available() is True


def test_process_cli_arguments_nominal(tmp_metrics_dir, base_args, monkeypatch, fake_cpu_midr):
    # Point METRICS_DIR to our temp directory
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)

    # Set controlled core list: use cores [0,1]
    base_args.core = [0, 1]
    factory = CpuProbeFactory()

    # Process args using our fake CPUDetect to control cpu_count and cpu_midr
    factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)

    # Check that _midr_core_map was populated with our fake CPU.
    # Since FakeCPUDetect returns same MIDR for each core, mapping must have one entry with two cores.
    midr_map = factory._midr_core_map
    assert len(midr_map) == 1
    for cores in midr_map.values():
        assert cores == [0, 1]

    # Check that _cpu_descriptions has the expected key.
    # CPUDetect.cpu_id(fake_cpu_midr()) should be 0x41d8e.
    cpu_id = FakeCPUDetect.cpu_id(fake_cpu_midr)
    desc = factory._cpu_descriptions.get(cpu_id)
    assert desc is not None
    # Content should be loaded from the spec file.
    assert desc.content is not None
    assert desc.content.product_configuration.product_name == "neoverse-n3"


def test_process_cli_arguments_missing_csv_raises(tmp_metrics_dir, base_args, monkeypatch):
    # Remove cpu_csv and cpu_dump_events and provide interval to trigger error.
    base_args.cpu_csv = None
    base_args.events_csv = None
    base_args.interval = 1
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    factory = CpuProbeFactory()
    with pytest.raises(ArgsError):
        factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)


def test_create_method(monkeypatch, tmp_metrics_dir, base_args):
    # Patch METRICS_DIR to use our temporary metrics folder.
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)

    # Prepare args
    base_args.core = [0]
    base_args.interval = 1
    base_args.cpu_csv = "dummy.csv"

    # NEW: Use the generator to create a dummy SME telemetry spec.
    sme_spec = generate_fake_spec(
        product_name="sme-test",
        part_num="0x123",
        implementer="0x99",
        timestamp="sme",
        description="SME spec",
        num_slots=4,
        num_bus_slots=0,
    )
    sme_path = os.path.join(tmp_metrics_dir, "sme.json")
    with open(sme_path, "w", encoding="utf-8") as f:
        json.dump(sme_spec, f)
    base_args.sme = [(sme_path, [1])]

    factory = CpuProbeFactory()
    # First process arguments to populate cpu description mapping.
    factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)

    # Prepare a list to record CpuProbe constructor calls.
    calls = []

    def fake_CpuProbe(conf, spec, cores, capture_data, perf_class):
        calls.append(
            {
                "conf": conf,
                "spec": spec,
                "cores": cores,
                "capture_data": capture_data,
                "perf_class": perf_class,
            }
        )
        return object()  # return dummy probe object

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", fake_CpuProbe)

    probes = factory.create(base_args, capture_data=True, cpu_detect=FakeCPUDetect)
    # One probe should be created for the detected CPU (from core 0) and one for the SME spec.
    # Total probes == 2.
    assert len(probes) == 2
    # Verify calls recorded.
    # First call: from normal CPU description.
    assert calls[0]["cores"] == [0]
    assert calls[0]["spec"].product_configuration.product_name == "neoverse-n3"
    # Second call: from SME argument.
    assert calls[1]["cores"] == [1]
    assert calls[1]["spec"].product_configuration.product_name == "sme-test"


# NEW: Test that when any listing flag is set, _list_cpus is called and process_cli_arguments returns False.
@pytest.mark.parametrize(
    "list_flag", ["cpu_list", "cpu_list_groups", "cpu_list_metrics", "cpu_list_events"]
)
def test_process_cli_arguments_list_flags_independently(
    tmp_metrics_dir, base_args, monkeypatch, list_flag
):
    # Set the specific flag True and ensure others are False.
    setattr(base_args, list_flag, True)

    list_called = False

    def fake_list_cpus(self, args, cpu_detect=FakeCPUDetect):
        nonlocal list_called
        list_called = True
        # Simulate listing behavior: no error, just return
        return True

    monkeypatch.setattr(CpuProbeFactory, "_list_cpus", fake_list_cpus)
    factory = CpuProbeFactory()
    ret = factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)
    assert list_called is True
    assert ret is False


# NEW: Test that when no listing flags are set, process_cli_arguments returns True.
def test_process_cli_arguments_no_list_flags(tmp_metrics_dir, base_args):
    base_args.cpu_list = False
    base_args.cpu_list_groups = False
    base_args.cpu_list_metrics = False
    base_args.cpu_list_events = False
    factory = CpuProbeFactory()
    ret = factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)
    assert ret is True


# NEW: Complex Probe-Creation Test for Multi-CPU System.
class FakeHeteroDetect:
    @staticmethod
    def cpu_count() -> int:
        return 9  # cores 0 to 8

    @staticmethod
    def cpu_midr(core: int) -> int:
        if core in [0, 1, 2, 3, 4]:
            return 100  # little_core
        elif core in [5, 6]:
            return 200  # mid_core
        elif core in [7, 8]:
            return 300  # big_core
        return 0

    @staticmethod
    def cpu_id(midr: int) -> int:
        return midr  # identity mapping


def fake_load_from_json_file(path):
    # Return a fake spec-like object with a 'product_configuration' attribute.
    spec = SimpleNamespace()
    pc = SimpleNamespace()
    if "little_core" in path:
        pc.product_name = "little_core"
    elif "mid_core" in path:
        pc.product_name = "mid_core"
    elif "big_core" in path:
        pc.product_name = "big_core"
    elif "sme" in path:
        pc.product_name = "sme_probe"
    else:
        pc.product_name = "unknown"
    spec.product_configuration = pc
    return spec


def test_complex_probe_creation(monkeypatch, tmp_path):
    # Set up a temporary metrics directory with a custom mapping for heterogeneous CPUs.
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    mapping = {
        "0x64": {"name": "little_core"},  # 0x64 (=100)
        "0xc8": {"name": "mid_core"},  # 0xc8 (=200)
        "0x12c": {"name": "big_core"},  # 0x12c (=300)
    }
    mapping_path = metrics_dir / "mapping.json"
    mapping_path.write_text(json.dumps(mapping))
    for name in ["little_core", "mid_core", "big_core"]:
        (metrics_dir / f"{name}.json").write_text("{}")  # dummy content

    # Patch METRICS_DIR and TelemetrySpecification.load_from_json_file.
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", str(metrics_dir))
    monkeypatch.setattr(
        "topdown_tool.cpu_probe.cpu_model.TelemetrySpecification.load_from_json_file",
        fake_load_from_json_file,
    )

    # Prepare base_args with --core flag set to "0,2-5,8" and one SME file assigned to cores 0-5.
    base_args = SimpleNamespace()
    base_args.cpu = None
    base_args.core = [0, 2, 3, 4, 5, 8]
    base_args.sme = []  # will assign below
    base_args.cpu_list = False
    base_args.cpu_list_groups = False
    base_args.cpu_list_metrics = False
    base_args.cpu_list_events = False
    base_args.cpu_no_multiplex = False
    base_args.cpu_collect_by = "metric"
    base_args.cpu_metric_group = None
    base_args.cpu_node = None
    base_args.cpu_level = None
    base_args.cpu_stages = "all"
    base_args.cpu_descriptions = False
    base_args.cpu_show_sample_events = False
    base_args.cpu_csv = "dummy.csv"
    base_args.events_csv = None
    base_args.interval = 1
    base_args.cpu_dump_events = None

    # Create a temporary SME spec file using the generator function.
    sme_spec = generate_fake_spec(
        product_name="sme_probe",
        part_num="0x999",
        implementer="0x99",
        timestamp="sme",
        description="SME probe spec",
        num_slots=4,
        num_bus_slots=0,
    )
    sme_path = metrics_dir / "sme.json"
    sme_path.write_text(json.dumps(sme_spec))
    base_args.sme = [(str(sme_path), list(range(0, 6)))]  # assign cores 0 to 5

    factory = CpuProbeFactory()
    factory.process_cli_arguments(base_args, cpu_detect=FakeHeteroDetect)

    # Patch CpuProbe to capture creation calls.
    calls = []

    def fake_CpuProbe(conf, spec, cores, capture_data, perf_class):
        calls.append({"spec": spec, "cores": cores})
        return object()

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", fake_CpuProbe)

    probes = factory.create(base_args, capture_data=True, cpu_detect=FakeHeteroDetect)
    # Expect 4 probes: little_core (cores: 0,2,3,4), mid_core (core: 5), big_core (core: 8), and SME probe (cores: 0-5).
    assert len(probes) == 4
    little = next(
        (c for c in calls if c["spec"].product_configuration.product_name == "little_core"),
        None,
    )
    mid = next(
        (c for c in calls if c["spec"].product_configuration.product_name == "mid_core"),
        None,
    )
    big = next(
        (c for c in calls if c["spec"].product_configuration.product_name == "big_core"),
        None,
    )
    sme = next(
        (c for c in calls if c["spec"].product_configuration.product_name == "sme_probe"),
        None,
    )
    assert little is not None and sorted(little["cores"]) == [0, 2, 3, 4]
    assert mid is not None and mid["cores"] == [5]
    assert big is not None and big["cores"] == [8]
    assert sme is not None and sme["cores"] == list(range(0, 6))


def test_perf_factory_integration(monkeypatch, tmp_metrics_dir, fake_cpu_midr, base_args):
    base_args.perf_path = "/custom/perf"
    base_args.perf_args = "--custom-flag"
    base_args.interval = 500
    base_args.cpu_csv = "out/"
    base_args.cpu_dump_events = None
    base_args.cpu_stages = DEFAULT_ALL_STAGES
    base_args.cpu_collect_by = CollectBy.METRIC

    factory = CpuProbeFactory()
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)

    global_perf_factory.process_cli_arguments(base_args)

    # Patch get_pmu_counters to avoid subprocess
    monkeypatch.setattr(LinuxPerf, "get_pmu_counters", staticmethod(lambda core, path: 6))
    monkeypatch.setattr(WindowsPerf, "get_pmu_counters", staticmethod(lambda core, path: 6))

    result = factory.process_cli_arguments(base_args, cpu_detect=FakeCPUDetect)
    assert result is True

    # Capture the Perf args passed into CpuProbe
    captured = {}

    def fake_CpuProbe(conf, spec, cores, capture_data, perf_factory_instance):
        captured["perf_path"] = perf_factory_instance._perf_path
        captured["perf_args"] = perf_factory_instance._perf_args
        captured["interval"] = perf_factory_instance._interval
        return SimpleNamespace()  # dummy CpuProbe

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", fake_CpuProbe)

    probes = factory.create(base_args)
    assert len(probes) == 1
    assert captured["perf_path"] == "/custom/perf"
    assert captured["perf_args"] == "--custom-flag"
    assert captured["interval"] == 500
