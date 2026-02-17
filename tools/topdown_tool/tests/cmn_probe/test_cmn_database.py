# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from json import load
from pathlib import Path
from typing import List, Tuple

import pytest

from topdown_tool.cmn_probe.cmn_database import CmnDatabase, DeviceType
from topdown_tool.cmn_probe.common import (
    CmnLocation,
    JsonGroup,
    JsonMetric,
    JsonTopdownGroup,
    JsonTopdownMetric,
    JsonWatchpoint,
    NodeLocation,
    PortLocation,
    XpLocation,
)
from tests.cmn_probe.helpers import assert_reference_text


BASE_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = BASE_DIR / "fixtures"
OUTPUT_DIR = BASE_DIR / "database_out"


@pytest.fixture
def cmn_db() -> CmnDatabase:
    cmn_version = "700"
    cmn_indices = (0, 1)

    with (FIXTURES_DIR / "topology.json").open(encoding="utf-8") as handle:
        topology_json = load(handle)
    with (FIXTURES_DIR / "cmn-700.json").open(encoding="utf-8") as handle:
        specification_json = load(handle)

    return CmnDatabase(cmn_version, cmn_indices, topology_json, specification_json)


def _assert_reference(actual: object, reference_name: str, regen_reference_mode: str) -> None:
    assert_reference_text(str(actual), OUTPUT_DIR / reference_name, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((0,), "get_dtc_count_1.txt"),
    ],
)
def test_get_dtc_count(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[int, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_dtc_count(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((0, 512), "dtc_of_1.txt"),
    ],
)
def test_dtc_of(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[int, ...], reference: str
) -> None:
    _assert_reference(cmn_db.dtc_of(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((0,), "cmn_topology_1.txt"),
    ],
)
def test_cmn_topology(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[int, ...], reference: str
) -> None:
    _assert_reference(cmn_db.cmn_topology(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((0,), "watchpoint_port_map_1.txt"),
    ],
)
def test_watchpoint_port_map(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[int, ...], reference: str
) -> None:
    _assert_reference(cmn_db.watchpoint_port_map(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((CmnLocation(0),), "get_coordinates_1.txt"),
        ((XpLocation(0, 512),), "get_coordinates_2.txt"),
        ((PortLocation(0, 512, 1),), "get_coordinates_3.txt"),
        ((NodeLocation(0, 512, 1, 516),), "get_coordinates_4.txt"),
    ],
)
def test_get_coordinates(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_coordinates(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((PortLocation(0, 512, 1),), "get_node_id_of_port_1.txt"),
    ],
)
def test_get_node_id_of_port(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_node_id_of_port(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE,), "get_dev_id_field_1.txt"),
        ((DeviceType.PORT,), "get_dev_id_field_2.txt"),
    ],
)
def test_get_dev_id_field(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_dev_id_field(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE,), "get_table_for_device_type_1.txt"),
        ((DeviceType.PORT,), "get_table_for_device_type_2.txt"),
    ],
)
def test_get_table_for_device_type(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_table_for_device_type(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_indices_1.txt"),
    ],
)
def test_get_indices(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_indices(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_version_1.txt"),
    ],
)
def test_get_version(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_version(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE, None), "get_devices_1.txt"),
        ((DeviceType.PORT, None), "get_devices_2.txt"),
    ],
)
def test_get_devices(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_devices(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((5,), "get_json_events_1.txt"),
    ],
)
def test_get_json_events(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[int, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_json_events(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE, 5), "get_json_watchpoints_1.txt"),
        ((DeviceType.PORT, 15), "get_json_watchpoints_2.txt"),
    ],
)
def test_get_json_watchpoints(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    watchpoints: List[JsonWatchpoint] = []
    for json_watchpoint in cmn_db.get_json_watchpoints(*params):
        object.__setattr__(json_watchpoint, "wp_val", sorted(json_watchpoint.wp_val))
        watchpoints.append(json_watchpoint)

    _assert_reference(watchpoints, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE, 5), "get_json_metrics_1.txt"),
        ((DeviceType.PORT, 15), "get_json_metrics_2.txt"),
    ],
)
def test_get_json_metrics(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    metrics: List[JsonMetric] = []
    for json_metric in cmn_db.get_json_metrics(*params):
        object.__setattr__(json_metric, "events", sorted(json_metric.events))
        object.__setattr__(json_metric, "watchpoints", sorted(json_metric.watchpoints))
        object.__setattr__(json_metric, "sample_events", sorted(json_metric.sample_events))
        metrics.append(json_metric)

    _assert_reference(metrics, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((DeviceType.NODE, 5), "get_json_groups_1.txt"),
        ((DeviceType.PORT, 15), "get_json_groups_2.txt"),
    ],
)
def test_get_json_groups(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    groups: List[JsonGroup] = []
    for json_group in cmn_db.get_json_groups(*params):
        object.__setattr__(json_group, "metrics", sorted(json_group.metrics))
        groups.append(json_group)

    _assert_reference(groups, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_json_topdown_metrics_1.txt"),
    ],
)
def test_get_json_topdown_metrics(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    metrics: List[JsonTopdownMetric] = []
    for json_topdown_metric in cmn_db.get_json_topdown_metrics(*params):
        object.__setattr__(json_topdown_metric, "metrics", sorted(json_topdown_metric.metrics))
        metrics.append(json_topdown_metric)

    _assert_reference(metrics, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_json_topdown_groups_1.txt"),
    ],
)
def test_get_json_topdown_groups(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    groups: List[JsonTopdownGroup] = []
    for json_topdown_group in cmn_db.get_json_topdown_groups(*params):
        object.__setattr__(json_topdown_group, "metrics", sorted(json_topdown_group.metrics))
        groups.append(json_topdown_group)

    _assert_reference(groups, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        (
            (
                (
                    "cmn_hnf_fwded_snp_rate",
                    "cmn_hns_fwded_snp_rate",
                    "cmn_hni_axi_ar_stall_rate",
                    "cmn_hnp_rxreq_rate",
                    "cmn_rnf_txreq_rate",
                    "cmn_rni_rrt_avg_occupancy",
                    "cmn_rnd_txreq_rate",
                    "cmn_snf_rxreq_rate",
                    "cmn_ccgra_reqtrk_avg_occupancy",
                    "cmn_ccgha_reqtrk_avg_occupancy",
                    "cmn_ccgla_rxcxs_avg_size",
                    "cmn_ccg_rxreq_rate",
                ),
            ),
            "get_collectable_metrics_1.txt",
        ),
    ],
)
def test_get_collectable_metrics(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_collectable_metrics(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        (
            (
                (
                    "HNF_Analysis_Occupancy",
                    "HNS_Analysis_Occupancy",
                    "HNI_CHI_Ingress_Traffic",
                    "HNP_CHI_Ingress_Traffic",
                    "RNF_CHI_Egress_Traffic",
                    "RNI_CHI_Egress_Traffic",
                    "RND_CHI_Egress_Traffic",
                    "SNF_Ingress_Traffic",
                    "CCG_RA_TRK_Effectiveness",
                    "CCG_HA_TRK_Effectiveness",
                    "CCG_LA_Effectiveness",
                    "CCG_Req_Opcode_Mix",
                ),
            ),
            "get_collectable_groups_1.txt",
        ),
    ],
)
def test_get_collectable_groups(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_collectable_groups(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_collectable_topdown_metrics_1.txt"),
    ],
)
def test_get_collectable_topdown_metrics(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_collectable_topdown_metrics(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        ((), "get_collectable_base_metrics_for_topdown_group_1.txt"),
    ],
)
def test_get_collectable_base_metrics_for_topdown_group(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(
        cmn_db.get_collectable_base_metrics_for_topdown_group(*params),
        reference,
        regen_reference_mode,
    )


@pytest.mark.parametrize(
    "params, reference",
    [
        (("cmn_hnf_fwded_snp_rate", False), "get_schedulable_events_for_metric_1.txt"),
        (("cmn_hns_fwded_snp_rate", False), "get_schedulable_events_for_metric_2.txt"),
        (("cmn_hni_axi_ar_stall_rate", False), "get_schedulable_events_for_metric_3.txt"),
        (("cmn_hnp_rxreq_rate", False), "get_schedulable_events_for_metric_4.txt"),
        (("cmn_rnf_txreq_rate", False), "get_schedulable_events_for_metric_5.txt"),
        (("cmn_rni_rrt_avg_occupancy", False), "get_schedulable_events_for_metric_6.txt"),
        (("cmn_rnd_txreq_rate", False), "get_schedulable_events_for_metric_7.txt"),
        (("cmn_snf_rxreq_rate", False), "get_schedulable_events_for_metric_8.txt"),
        (("cmn_ccgra_reqtrk_avg_occupancy", False), "get_schedulable_events_for_metric_9.txt"),
        (("cmn_ccgha_reqtrk_avg_occupancy", False), "get_schedulable_events_for_metric_10.txt"),
        (("cmn_ccgla_rxcxs_avg_size", False), "get_schedulable_events_for_metric_11.txt"),
        (("cmn_ccg_rxreq_rate", False), "get_schedulable_events_for_metric_12.txt"),
    ],
)
def test_get_schedulable_events_for_metric(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(
        cmn_db.get_schedulable_events_for_metric(*params), reference, regen_reference_mode
    )


@pytest.mark.parametrize(
    "params, reference",
    [
        (("cmn_hnf_fwded_snp_rate", False), "get_schedulable_xp_events_for_metric_1.txt"),
        (("cmn_hns_fwded_snp_rate", False), "get_schedulable_xp_events_for_metric_2.txt"),
        (("cmn_hni_axi_ar_stall_rate", False), "get_schedulable_xp_events_for_metric_3.txt"),
        (("cmn_hnp_rxreq_rate", False), "get_schedulable_xp_events_for_metric_4.txt"),
        (("cmn_rnf_txreq_rate", False), "get_schedulable_xp_events_for_metric_5.txt"),
        (("cmn_rni_rrt_avg_occupancy", False), "get_schedulable_xp_events_for_metric_6.txt"),
        (("cmn_rnd_txreq_rate", False), "get_schedulable_xp_events_for_metric_7.txt"),
        (("cmn_snf_rxreq_rate", False), "get_schedulable_xp_events_for_metric_8.txt"),
        (("cmn_ccgra_reqtrk_avg_occupancy", False), "get_schedulable_xp_events_for_metric_9.txt"),
        (("cmn_ccgha_reqtrk_avg_occupancy", False), "get_schedulable_xp_events_for_metric_10.txt"),
        (("cmn_ccgla_rxcxs_avg_size", False), "get_schedulable_xp_events_for_metric_11.txt"),
        (("cmn_ccg_rxreq_rate", False), "get_schedulable_xp_events_for_metric_12.txt"),
    ],
)
def test_get_schedulable_xp_events_for_metric(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(
        cmn_db.get_schedulable_xp_events_for_metric(*params),
        reference,
        regen_reference_mode,
    )


@pytest.mark.parametrize(
    "params, reference",
    [
        (
            ("cmn_hnf_fwded_snp_rate", False, True),
            "get_schedulable_watchpoints_for_metric_1.txt",
        ),
        (
            ("cmn_hns_fwded_snp_rate", False, True),
            "get_schedulable_watchpoints_for_metric_2.txt",
        ),
        (
            ("cmn_hni_axi_ar_stall_rate", False, True),
            "get_schedulable_watchpoints_for_metric_3.txt",
        ),
        (
            ("cmn_hnp_rxreq_rate", False, True),
            "get_schedulable_watchpoints_for_metric_4.txt",
        ),
        (
            ("cmn_rnf_txreq_rate", False, True),
            "get_schedulable_watchpoints_for_metric_5.txt",
        ),
        (
            ("cmn_rni_rrt_avg_occupancy", False, True),
            "get_schedulable_watchpoints_for_metric_6.txt",
        ),
        (
            ("cmn_rnd_txreq_rate", False, True),
            "get_schedulable_watchpoints_for_metric_7.txt",
        ),
        (
            ("cmn_snf_rxreq_rate", False, True),
            "get_schedulable_watchpoints_for_metric_8.txt",
        ),
        (
            ("cmn_ccgra_reqtrk_avg_occupancy", False, True),
            "get_schedulable_watchpoints_for_metric_9.txt",
        ),
        (
            ("cmn_ccgha_reqtrk_avg_occupancy", False, True),
            "get_schedulable_watchpoints_for_metric_10.txt",
        ),
        (
            ("cmn_ccgla_rxcxs_avg_size", False, True),
            "get_schedulable_watchpoints_for_metric_11.txt",
        ),
        (
            ("cmn_ccg_rxreq_rate", False, True),
            "get_schedulable_watchpoints_for_metric_12.txt",
        ),
    ],
)
def test_get_schedulable_watchpoints_for_metric(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[object, ...], reference: str
) -> None:
    _assert_reference(
        cmn_db.get_schedulable_watchpoints_for_metric(*params),
        reference,
        regen_reference_mode,
    )


@pytest.mark.parametrize(
    "params, reference",
    [
        (("cmn_hnf_fwded_snp_rate",), "get_metric_details_1.txt"),
        (("cmn_hns_fwded_snp_rate",), "get_metric_details_2.txt"),
        (("cmn_hni_axi_ar_stall_rate",), "get_metric_details_3.txt"),
        (("cmn_hnp_rxreq_rate",), "get_metric_details_4.txt"),
        (("cmn_rnf_txreq_rate",), "get_metric_details_5.txt"),
        (("cmn_rni_rrt_avg_occupancy",), "get_metric_details_6.txt"),
        (("cmn_rnd_txreq_rate",), "get_metric_details_7.txt"),
        (("cmn_snf_rxreq_rate",), "get_metric_details_8.txt"),
        (("cmn_ccgra_reqtrk_avg_occupancy",), "get_metric_details_9.txt"),
        (("cmn_ccgha_reqtrk_avg_occupancy",), "get_metric_details_10.txt"),
        (("cmn_ccgla_rxcxs_avg_size",), "get_metric_details_11.txt"),
        (("cmn_ccg_rxreq_rate",), "get_metric_details_12.txt"),
    ],
)
def test_get_metric_details(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[str, ...], reference: str
) -> None:
    details = cmn_db.get_metric_details(*params)
    object.__setattr__(details, "sample_events", sorted(details.sample_events))
    _assert_reference(details, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        (("HNF_Analysis_Occupancy",), "get_group_title_1.txt"),
        (("HNS_Analysis_Occupancy",), "get_group_title_2.txt"),
        (("HNI_CHI_Ingress_Traffic",), "get_group_title_3.txt"),
        (("HNP_CHI_Ingress_Traffic",), "get_group_title_4.txt"),
        (("RNF_CHI_Egress_Traffic",), "get_group_title_5.txt"),
        (("RNI_CHI_Egress_Traffic",), "get_group_title_6.txt"),
        (("RND_CHI_Egress_Traffic",), "get_group_title_7.txt"),
        (("SNF_Ingress_Traffic",), "get_group_title_8.txt"),
        (("CCG_RA_TRK_Effectiveness",), "get_group_title_9.txt"),
        (("CCG_HA_TRK_Effectiveness",), "get_group_title_10.txt"),
        (("CCG_LA_Effectiveness",), "get_group_title_11.txt"),
        (("CCG_Req_Opcode_Mix",), "get_group_title_12.txt"),
    ],
)
def test_get_group_title(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[str, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_group_title(*params), reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        (("cmn_bw_ccg_c2c_destination",), "get_topdown_metric_details_1.txt"),
        (("cmn_bw_ccg_c2c_requestor",), "get_topdown_metric_details_2.txt"),
        (("cmn_bw_ccg_cxl",), "get_topdown_metric_details_3.txt"),
        (("cmn_bw_io",), "get_topdown_metric_details_4.txt"),
        (("cmn_bw_peer_cpu_cache",), "get_topdown_metric_details_5.txt"),
        (("cmn_bw_rnd",), "get_topdown_metric_details_6.txt"),
        (("cmn_bw_rnf",), "get_topdown_metric_details_7.txt"),
        (("cmn_bw_rni",), "get_topdown_metric_details_8.txt"),
        (("cmn_bw_slc",), "get_topdown_metric_details_9.txt"),
        (("cmn_bw_snf",), "get_topdown_metric_details_10.txt"),
        (("cmn_bw_total",), "get_topdown_metric_details_11.txt"),
        (("requestor_destination_proportion_local",), "get_topdown_metric_details_12.txt"),
        (("requestor_destination_proportion_remote",), "get_topdown_metric_details_13.txt"),
        (("requestor_proportion_CCG",), "get_topdown_metric_details_14.txt"),
        (("requestor_proportion_RND",), "get_topdown_metric_details_15.txt"),
        (("requestor_proportion_RNF",), "get_topdown_metric_details_16.txt"),
        (("requestor_proportion_RNI",), "get_topdown_metric_details_17.txt"),
        (("target_proportion_CCG",), "get_topdown_metric_details_18.txt"),
        (("target_proportion_IO",), "get_topdown_metric_details_19.txt"),
        (("target_proportion_PeerCPUCache",), "get_topdown_metric_details_20.txt"),
        (("target_proportion_SLC",), "get_topdown_metric_details_21.txt"),
        (("target_proportion_SNF",), "get_topdown_metric_details_22.txt"),
        (("total_requestor_destination_rate",), "get_topdown_metric_details_23.txt"),
        (("total_requestor_rate",), "get_topdown_metric_details_24.txt"),
        (("total_target_rate",), "get_topdown_metric_details_25.txt"),
    ],
)
def test_get_topdown_metric_details(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[str, ...], reference: str
) -> None:
    details = cmn_db.get_topdown_metric_details(*params)
    object.__setattr__(details, "base_metrics", sorted(details.base_metrics))
    object.__setattr__(details, "topdown_metrics", sorted(details.topdown_metrics))
    _assert_reference(details, reference, regen_reference_mode)


@pytest.mark.parametrize(
    "params, reference",
    [
        (
            ("CMN_Requestor_Target_Characterization_Level_One",),
            "get_topdown_group_title_1.txt",
        ),
        (
            ("CMN_Requestor_Target_Characterization_Level_Two",),
            "get_topdown_group_title_2.txt",
        ),
        (
            ("CMN_Requestor_Target_Characterization_Level_Three",),
            "get_topdown_group_title_3.txt",
        ),
        (("CMN_Requestor_Bandwidth",), "get_topdown_group_title_4.txt"),
        (("CMN_Completer_Bandwidth",), "get_topdown_group_title_5.txt"),
        (("CMN_HNF_HNS_Txn_Shared_Read_Rate",), "get_topdown_group_title_6.txt"),
        (("CMN_HNF_HNS_Txn_Data_Update_Rate",), "get_topdown_group_title_7.txt"),
        (("CMN_HNF_HNS_Txn_Writeback_Rate",), "get_topdown_group_title_8.txt"),
        (("CMN_HNF_HNS_Txn_RFO_Rate",), "get_topdown_group_title_9.txt"),
        (("CMN_HNF_HNS_Ratio_Analysis",), "get_topdown_group_title_10.txt"),
        (("CMN_HNF_HNS_Rate_Analysis",), "get_topdown_group_title_11.txt"),
        (("CMN_HNF_HNS_Queue_Occupancy_Analysis",), "get_topdown_group_title_12.txt"),
        (("CMN_RNI_RND_First_Order_Egress_Request_Analysis",), "get_topdown_group_title_13.txt"),
        (("CMN_RNI_RND_First_Order_Queue_Occupancy_Analysis",), "get_topdown_group_title_14.txt"),
        (("CMN_HNI_HNP_First_Order_Ingress_Request_Analysis",), "get_topdown_group_title_15.txt"),
        (("CMN_HNI_HNP_First_Order_Egress_Request_Analysis",), "get_topdown_group_title_16.txt"),
        (("CMN_HNI_HNP_First_Order_Queue_Occupancy_Analysis",), "get_topdown_group_title_17.txt"),
        (
            ("CMN_CCG_First_Order_CXS_Link_Utilization_Analysis",),
            "get_topdown_group_title_18.txt",
        ),
        (
            ("CMN_CCG_First_Order_CHI_Ingress_Request_Analysis",),
            "get_topdown_group_title_19.txt",
        ),
        (
            ("CMN_CCG_First_Order_CHI_Egress_Request_Analysis",),
            "get_topdown_group_title_20.txt",
        ),
        (
            ("CMN_CCG_First_Order_Queue_Occupancy_Analysis",),
            "get_topdown_group_title_21.txt",
        ),
        (("CMN_Stage_Two_POCQ_Effectiveness",), "get_topdown_group_title_22.txt"),
        (("CMN_Stage_Two_SLC_Effectiveness",), "get_topdown_group_title_23.txt"),
        (("CMN_Stage_Two_SF_Effectiveness",), "get_topdown_group_title_24.txt"),
    ],
)
def test_get_topdown_group_title(
    regen_reference_mode, cmn_db: CmnDatabase, params: Tuple[str, ...], reference: str
) -> None:
    _assert_reference(cmn_db.get_topdown_group_title(*params), reference, regen_reference_mode)
