# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

import argparse
import json
import os
from typing import Optional
import pytest
from types import SimpleNamespace
import shutil
from unittest.mock import Mock

from topdown_tool.cpu_probe.cpu_factory import CpuProbeFactory, ArgsError
from topdown_tool.cpu_probe.common import DEFAULT_ALL_STAGES
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.linux_perf import LinuxPerf
from topdown_tool.perf.windows_perf import WindowsPerf
from topdown_tool.perf import perf_factory as global_perf_factory
from topdown_tool.perf.remote_linux_perf import RemoteLinuxPerf
from topdown_tool.perf.perf_factory import PerfFactory
import topdown_tool.cpu_probe.cpu_detector as cpu_detector_module
from topdown_tool.cpu_probe.cpu_detector import (
    CpuDetector,
    CpuDetectorFactory,
    RemoteLinuxLikeCpuDetector,
)
from topdown_tool.common import remote_target_manager
from tests.cpu_probe.helpers import get_fixture_path

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


class FakeCPUDetect(CpuDetector):
    def cpu_count(self) -> int:
        return 2

    def cpu_midr(self, core: int) -> int:
        return _fake_cpu_midr()


def process_with_detector(
    factory: CpuProbeFactory,
    args: argparse.Namespace,
    cpu_detector: Optional[CpuDetector] = None,
) -> bool:
    """Helper to process CLI args and apply configuration in tests."""

    return factory.configure_from_cli_arguments(args, cpu_detector=cpu_detector)


def tmp_metrics_dir_common(tmp_path, schema_tag, create_spec_file=True, generate_invalid_tag=False):
    # Create a temporary metrics directory with a mapping.json and one telemetry specification file.
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()

    mapping = {"0x41d8e": {"name": "neoverse-n3"}}
    mapping_path = metrics_dir / "mapping.json"
    mapping_path.write_text(json.dumps(mapping))

    if not create_spec_file:
        return str(metrics_dir)

    # Use the generator function instead of hard-coded spec.
    spec = generate_fake_spec(
        product_name="neoverse-n3",
        part_num="0xd8e",
        implementer="0x41",
        timestamp="dummy",
        description="Test spec for neoverse-n3",
        num_slots=5,
        num_bus_slots=0,
        schema_tag=schema_tag,
        generate_invalid_tag=generate_invalid_tag,
    )
    spec_path = metrics_dir / "neoverse-n3.json"
    spec_path.write_text(json.dumps(spec))

    return str(metrics_dir)


@pytest.fixture
def tmp_metrics_dir(tmp_path):
    return tmp_metrics_dir_common(tmp_path, schema_tag="v1.0.schema.json")


@pytest.fixture
def tmp_metrics_dir_no_schema(tmp_path):
    return tmp_metrics_dir_common(tmp_path, schema_tag=None)


@pytest.fixture
def tmp_metrics_dir_spec_references_invalid_schema(tmp_path):
    return tmp_metrics_dir_common(tmp_path, schema_tag="v1.0.schema-invalid.json")


@pytest.fixture
def tmp_metrics_dir_spec_references_nonexistent_schema(tmp_path):
    return tmp_metrics_dir_common(tmp_path, schema_tag="v1.0.schema-nonexistent.json")


@pytest.fixture
def tmp_metrics_dir_nonexisting_spec(tmp_path):
    return tmp_metrics_dir_common(tmp_path, schema_tag="v1.0.schema.json", create_spec_file=False)


@pytest.fixture
def tmp_metrics_dir_invalid_spec(tmp_path):
    return tmp_metrics_dir_common(
        tmp_path, schema_tag="v1.0.schema.json", generate_invalid_tag=True
    )


def tmp_schemas_dir_common(tmp_path, file_name="v1.0.schema.json"):
    # Create a temporary metrics directory with a mapping.json and one telemetry specification file.
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    shutil.copy(get_fixture_path(file_name), schemas_dir)

    return str(schemas_dir)


@pytest.fixture
def tmp_schemas_dir(tmp_path):
    return tmp_schemas_dir_common(tmp_path)


@pytest.fixture
def tmp_schemas_dir_invalid_schema(tmp_path):
    return tmp_schemas_dir_common(tmp_path, "v1.0.schema-invalid.json")


@pytest.fixture
def base_args():
    # Build an argparse.Namespace with default values expected by process_cli_arguments.
    ns = argparse.Namespace()
    ns.cpu = None
    ns.core = None
    ns.sme = None
    ns.cpu_list_cores = False
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
    ns.csv_output_path = None
    ns.cpu_generate_csv = None
    ns.interval = None
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
    schema_tag=None,
    generate_invalid_tag=False,
):
    ret = {
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
    if schema_tag:
        ret["$schema"] = schema_tag

    if generate_invalid_tag:
        ret["additional_tag"] = "additional_value"

    return ret


# --- Tests ---


def test_name_method():
    factory = CpuProbeFactory()
    assert factory.name() == "CPU"


def test_is_available_method():
    factory = CpuProbeFactory()
    assert factory.is_available() is True


@pytest.mark.parametrize("metrics_fixture_dir", ["tmp_metrics_dir", "tmp_metrics_dir_no_schema"])
def test_process_cli_arguments_nominal(
    request, metrics_fixture_dir, tmp_schemas_dir, base_args, monkeypatch, fake_cpu_midr
):
    # Point METRICS_DIR to our temp directory
    monkeypatch.setattr(
        CpuProbeFactory, "METRICS_DIR", request.getfixturevalue(metrics_fixture_dir)
    )
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)

    # Set controlled core list: use cores [0,1]
    base_args.core = [0, 1]
    factory = CpuProbeFactory()

    # Process args using our fake CPUDetect to control cpu_count and cpu_midr
    process_with_detector(factory, base_args, FakeCPUDetect())

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


def test_process_cli_arguments_spec_references_invalid_schema_raises(
    tmp_metrics_dir_spec_references_invalid_schema,
    tmp_schemas_dir_invalid_schema,
    base_args,
    monkeypatch,
):
    monkeypatch.setattr(
        CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir_spec_references_invalid_schema
    )
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir_invalid_schema)
    factory = CpuProbeFactory()
    with pytest.raises(ValueError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_spec_references_nonexistent_schema_raises(
    tmp_metrics_dir_spec_references_nonexistent_schema, tmp_schemas_dir, base_args, monkeypatch
):
    monkeypatch.setattr(
        CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir_spec_references_nonexistent_schema
    )
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)
    factory = CpuProbeFactory()
    with pytest.raises(FileNotFoundError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_nonexisting_spec_raises(
    tmp_metrics_dir_nonexisting_spec, tmp_schemas_dir, base_args, monkeypatch
):
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir_nonexisting_spec)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)
    factory = CpuProbeFactory()
    with pytest.raises(FileNotFoundError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_invalid_spec_schema_validation_raises(
    tmp_metrics_dir_invalid_spec, tmp_schemas_dir, base_args, monkeypatch
):
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir_invalid_spec)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)
    factory = CpuProbeFactory()
    with pytest.raises(ValueError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_missing_csv_raises(tmp_metrics_dir, base_args, monkeypatch):
    # Remove csv_output_path and provide interval to trigger error.
    base_args.csv_output_path = None
    base_args.interval = 1
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)
    factory = CpuProbeFactory()
    with pytest.raises(ArgsError):
        process_with_detector(factory, base_args, FakeCPUDetect())


@pytest.mark.parametrize(
    "flag_overrides",
    [
        {"cpu_dump_events": True},
        {"cpu_generate_csv": ["metrics"]},
        {"cpu_generate_csv": ["events"]},
        {"cpu_generate_csv": ["metrics", "events"]},
    ],
)
def test_process_cli_arguments_requires_csv_output_path_for_csv_flags(base_args, flag_overrides):
    # No csv_output_path provided; any CSV-related flag must fail early
    for k, v in flag_overrides.items():
        setattr(base_args, k, v)
    factory = CpuProbeFactory()
    with pytest.raises(ArgsError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_interval_requires_metrics_csv(base_args):
    base_args.interval = 1
    # CSV not enabled -> should fail
    factory = CpuProbeFactory()
    with pytest.raises(ArgsError):
        process_with_detector(factory, base_args, FakeCPUDetect())


def test_process_cli_arguments_valid_csv_combinations(
    tmp_metrics_dir, tmp_schemas_dir, base_args, monkeypatch
):
    # Case A: metrics CSV with interval and path
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)
    base_args_a = argparse.Namespace(**vars(base_args))
    base_args_a.cpu_generate_csv = ["metrics"]
    base_args_a.csv_output_path = "out"
    base_args_a.interval = 1
    base_args_a.core = [0]
    factory = CpuProbeFactory()
    assert process_with_detector(factory, base_args_a, FakeCPUDetect()) in (True, False)

    # Case B: events CSV with path (no interval required)
    base_args_b = argparse.Namespace(**vars(base_args))
    base_args_b.cpu_generate_csv = ["events"]
    base_args_b.csv_output_path = "out"
    base_args_b.core = [0]
    assert process_with_detector(factory, base_args_b, FakeCPUDetect()) in (True, False)

    # Case C: dump events with path
    base_args_c = argparse.Namespace(**vars(base_args))
    base_args_c.cpu_dump_events = True
    base_args_c.csv_output_path = "out"
    base_args_c.core = [0]
    assert process_with_detector(factory, base_args_c, FakeCPUDetect()) in (True, False)


def test_create_method(monkeypatch, tmp_metrics_dir, tmp_schemas_dir, base_args):
    # Patch METRICS_DIR to use our temporary metrics folder.
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)

    # Prepare args
    base_args.core = [0]
    base_args.interval = 1
    base_args.csv_output_path = "dummy"
    base_args.cpu_generate_csv = ["metrics"]

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
    process_with_detector(factory, base_args, FakeCPUDetect())

    # Prepare a list to record CpuProbe constructor calls.
    calls = []

    def fake_CpuProbe(conf, spec, cores, capture_data, base_csv_dir, perf_class):
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

    probes = factory.create(capture_data=True, cpu_detector=FakeCPUDetect())
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
    "list_flag", ["cpu_list_cores", "cpu_list_groups", "cpu_list_metrics", "cpu_list_events"]
)
def test_process_cli_arguments_list_flags_independently(
    tmp_metrics_dir, base_args, monkeypatch, list_flag
):
    # Set the specific flag True and ensure others are False.
    setattr(base_args, list_flag, True)

    list_called = False

    def fake_list_cpus(self, args, cpu_detector):
        nonlocal list_called
        list_called = True
        # Simulate listing behavior: no error, just return
        return True

    monkeypatch.setattr(CpuProbeFactory, "_list_cpus", fake_list_cpus)
    factory = CpuProbeFactory()
    ret = process_with_detector(factory, base_args, FakeCPUDetect())
    assert list_called is True
    assert ret is False


# NEW: Test that when no listing flags are set, process_cli_arguments returns True.
def test_process_cli_arguments_no_list_flags(tmp_metrics_dir, base_args):
    base_args.cpu_list_cores = False
    base_args.cpu_list_groups = False
    base_args.cpu_list_metrics = False
    base_args.cpu_list_events = False
    factory = CpuProbeFactory()
    ret = process_with_detector(factory, base_args, FakeCPUDetect())
    assert ret is True


# NEW: Complex Probe-Creation Test for Multi-CPU System.
class FakeHeteroDetect(CpuDetector):
    def cpu_count(self) -> int:
        return 9  # cores 0 to 8

    def cpu_midr(self, core: int) -> int:
        if core in [0, 1, 2, 3, 4]:
            return 100  # little_core
        elif core in [5, 6]:
            return 200  # mid_core
        elif core in [7, 8]:
            return 300  # big_core
        return 0

    @staticmethod
    def cpu_id(midr: int) -> int:
        return midr  # identity mapping for mapping.json expectations


def fake_load_from_json_file(path, schema):
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

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    # Patch METRICS_DIR and TelemetrySpecification.load_from_json_file.
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", str(metrics_dir))
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", str(schemas_dir))
    monkeypatch.setattr(
        "topdown_tool.cpu_probe.cpu_model.TelemetrySpecification.load_from_json_file",
        fake_load_from_json_file,
    )

    # Prepare base_args with --core flag set to "0,2-5,8" and one SME file assigned to cores 0-5.
    base_args = SimpleNamespace()
    base_args.cpu = None
    base_args.core = [0, 2, 3, 4, 5, 8]
    base_args.sme = []  # will assign below
    base_args.cpu_list_cores = False
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
    base_args.csv_output_path = "dummy"
    base_args.cpu_generate_csv = ["metrics"]
    base_args.cpu_generate_events_csv = False
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
    process_with_detector(factory, base_args, FakeHeteroDetect())

    # Patch CpuProbe to capture creation calls.
    calls = []

    def fake_CpuProbe(conf, spec, cores, capture_data, base_csv_dir, perf_class):
        calls.append({"spec": spec, "cores": cores})
        return object()

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", fake_CpuProbe)

    probes = factory.create(capture_data=True, cpu_detector=FakeHeteroDetect())
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


def test_perf_factory_integration(
    monkeypatch, tmp_metrics_dir, tmp_schemas_dir, fake_cpu_midr, base_args
):
    base_args.perf_path = "/custom/perf"
    base_args.perf_args = "--custom-flag"
    base_args.interval = 500
    base_args.cpu_dump_events = None
    base_args.csv_output_path = "out/"
    base_args.cpu_generate_csv = ["metrics"]
    base_args.cpu_stages = DEFAULT_ALL_STAGES
    base_args.cpu_collect_by = CollectBy.METRIC

    factory = CpuProbeFactory()
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)

    global_perf_factory.configure_from_cli_arguments(base_args)

    # Patch get_pmu_counters to avoid subprocess
    monkeypatch.setattr(LinuxPerf, "get_pmu_counters", staticmethod(lambda core, path: 6))
    monkeypatch.setattr(WindowsPerf, "get_pmu_counters", staticmethod(lambda core, path: 6))

    result = process_with_detector(factory, base_args, FakeCPUDetect())
    assert result is True

    # Capture the Perf args passed into CpuProbe
    captured = {}

    def fake_CpuProbe(conf, spec, cores, capture_data, base_csv_dir, perf_factory_instance):
        captured["perf_path"] = perf_factory_instance._perf_path
        captured["perf_args"] = perf_factory_instance._perf_args
        captured["interval"] = perf_factory_instance._interval
        return SimpleNamespace()  # dummy CpuProbe

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", fake_CpuProbe)

    probes = factory.create()
    assert len(probes) == 1
    assert captured["perf_path"] == "/custom/perf"
    assert captured["perf_args"] == "--custom-flag"
    assert captured["interval"] == 500


def test_cpu_detector_factory_remote_linux(monkeypatch):
    remote_target = SimpleNamespace(execute=Mock(return_value=""))
    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: remote_target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: True)

    captured = {}

    def fake_remote_read(tgt, path):
        captured["target"] = tgt
        if "present" in path:
            return "0-1"
        if path == "/proc/cpuinfo":
            return (
                "processor : 0\n"
                "CPU implementer : 0x41\n"
                "CPU variant : 0x0\n"
                "CPU part : 0xd0c\n"
                "CPU revision : 0x1\n"
            )
        return None

    monkeypatch.setattr(cpu_detector_module, "remote_read_text", fake_remote_read)
    monkeypatch.setattr(cpu_detector_module, "remote_path_exists", lambda *_args, **_kwargs: False)

    detector = CpuDetectorFactory.create()

    assert isinstance(detector, RemoteLinuxLikeCpuDetector)
    assert detector.cpu_count() == 2
    assert captured["target"] is remote_target


def test_cpu_detector_factory_remote_unsupported(monkeypatch):
    remote_target = object()
    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: remote_target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: False)

    with pytest.raises(RuntimeError):
        CpuDetectorFactory.create()


def test_cpu_probe_factory_remote_path_smoke(
    monkeypatch, tmp_metrics_dir, tmp_schemas_dir, base_args
):
    """Ensure the CPU factory wires a remote Linux target into probes and perf."""

    midr_hex = hex(_fake_cpu_midr())

    class _FakeCpuTarget:
        def __init__(self) -> None:
            self.os = "linux"
            self.username = "root"

        def get_workpath(self, _name: str) -> str:
            return "/remote/workdir"

        def execute(self, cmd, **_kwargs):  # pragma: no cover - behaviour verified indirectly
            if "cat /sys/devices/system/cpu/present" in cmd:
                return "0-1"
            if "cat /sys/devices/system/cpu/online" in cmd:
                return "0-1"
            if "nproc" in cmd:
                return "2\n"
            if "cat /sys/devices/system/cpu/cpu" in cmd and "midr_el1" in cmd:
                return midr_hex
            if "test -e" in cmd:
                return "OK"
            if "cat /proc/sys/kernel/perf_event_paranoid" in cmd:
                return "-1\n"
            if "cat /proc/self/status" in cmd:
                return "CapEff:\t0000000000200000"
            return ""

    remote_target = _FakeCpuTarget()

    monkeypatch.setattr(remote_target_manager, "has_remote_target", lambda: True)
    monkeypatch.setattr(remote_target_manager, "get_remote_target", lambda: remote_target)
    monkeypatch.setattr(remote_target_manager, "is_target_linuxlike", lambda: True)
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "_resolve_perf_on_target",
        classmethod(lambda cls, _target: "/remote/perf"),
    )
    monkeypatch.setattr(
        RemoteLinuxPerf,
        "get_pmu_counters",
        classmethod(lambda cls, _core: 6),
    )

    base_args.perf_path = None
    base_args.perf_args = None
    base_args.core = None
    base_args.cpu_generate_csv = None

    factory = CpuProbeFactory()
    monkeypatch.setattr(CpuProbeFactory, "METRICS_DIR", tmp_metrics_dir)
    monkeypatch.setattr(CpuProbeFactory, "SCHEMAS_DIR", tmp_schemas_dir)

    global_perf_factory.configure_from_cli_arguments(base_args)

    result = process_with_detector(factory, base_args)
    assert result is True

    captured = {}

    def _fake_probe(conf, spec, cores, capture_data, base_csv_dir, perf_factory_instance):
        captured.setdefault("probes", []).append(
            (spec.product_configuration.product_name, list(cores))
        )
        captured["perf_factory"] = perf_factory_instance
        return SimpleNamespace(spec=spec, cores=cores, capture=capture_data)

    monkeypatch.setattr("topdown_tool.cpu_probe.cpu_factory.CpuProbe", _fake_probe)

    probes = factory.create(capture_data=False)

    assert len(probes) == 1
    assert captured["probes"] == [("neoverse-n3", [0, 1])]

    perf_factory_instance: PerfFactory = captured["perf_factory"]
    remote_perf = perf_factory_instance.create()
    assert isinstance(remote_perf, RemoteLinuxPerf)
