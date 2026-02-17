# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations
from difflib import unified_diff
from pathlib import Path
from re import search
from typing import Iterable, Optional, Sequence, Tuple, Union
from topdown_tool.cmn_probe.common import Event as CmnEvent, Watchpoint as CmnWatchpoint
from topdown_tool.cmn_probe.scheduler import CmnInfo, NodeEntry, WatchpointPort, cycle_event


PathLike = Union[str, Path]


def _event_id_from_name(name: Union[str, int]) -> int:
    if isinstance(name, int):
        return name
    match = search(r"(\d+)$", name)
    return int(match.group(1)) if match else 0


def cycle_ev(cmn_index: int = 0) -> CmnEvent:
    return cycle_event(cmn_index=cmn_index)


def glob_ev(
    name: str,
    *,
    event_type: int = 0,
    cmn_index: int = 0,
    occupid: Optional[int] = None,
) -> CmnEvent:
    return CmnEvent(
        name=name,
        title="",
        description="",
        cmn_index=cmn_index,
        type=event_type,
        eventid=_event_id_from_name(name),
        occupid=occupid,
        nodeid=None,
        xp_id=None,
    )


def ev(
    name: str,
    xp_id: int,
    node_id: Optional[int] = None,
    *,
    event_type: int = 0,
    cmn_index: int = 0,
    occupid: Optional[int] = None,
) -> CmnEvent:
    if node_id is None:
        node_id = xp_id
    return CmnEvent(
        name=name,
        title="",
        description="",
        cmn_index=cmn_index,
        type=event_type,
        eventid=_event_id_from_name(name),
        occupid=occupid,
        nodeid=node_id,
        xp_id=xp_id,
    )


def node(
    *,
    dtc: int = 0,
    xp: int = 0,
    node: int = 0,
    type: int = 0,
    port: int = 0,
) -> NodeEntry:
    return NodeEntry(dtc=dtc, xp=xp, node=node, node_type=type, port=port)


def make_cmn_info(
    *,
    dtc_count: int = 1,
    nodes: Optional[Sequence[NodeEntry]] = None,
    dtc_of=None,
    watchpoint_ports_by_device: Optional[dict[str, list[WatchpointPort]]] = None,
    global_type_aliases: Optional[dict[int, int]] = None,
) -> CmnInfo:
    if nodes is None:
        nodes = [NodeEntry(dtc=0, xp=0, node=0, node_type=0, port=0)]
    if dtc_of is None:
        def default_dtc_of(xp: int) -> int:
            return xp % dtc_count
        dtc_of = default_dtc_of
    return CmnInfo(
        dtc_count=dtc_count,
        dtc_of=dtc_of,
        nodes=list(nodes),
        watchpoint_ports_by_device=watchpoint_ports_by_device,
        global_type_aliases={} if global_type_aliases is None else dict(global_type_aliases),
    )


def wp(
    *,
    xp_id: int,
    port: int,
    direction: str = "UP",
    value: int = 1,
    mask: int = 0xFF,
    chn_sel: int = 0,
    grp: int = 0,
    cmn_index: int = 0,
) -> CmnWatchpoint:
    direction_map = {"UP": 0, "DOWN": 2}
    if direction not in direction_map:
        raise ValueError("direction must be 'UP' or 'DOWN'")
    return CmnWatchpoint(
        name="wp",
        title="",
        description="",
        cmn_index=cmn_index,
        mesh_flit_dir=direction_map[direction],
        wp_chn_sel=chn_sel,
        wp_grp=grp,
        wp_mask=mask,
        wp_val=value,
        xp_id=xp_id,
        port=port,
        device=None,
    )


def _event_value(event: CmnEvent | CmnWatchpoint) -> float:
    if isinstance(event, CmnEvent):
        if event.is_cycle():
            return 0.0
        if event.eventid is None:
            return 0.0
        return float(event.eventid)
    return float(event.wp_val)


def tup_val(events: Iterable[CmnEvent | CmnWatchpoint]) -> Tuple[float, ...]:
    return tuple(_event_value(event) for event in events)


def build_perf_result(
    groups: Iterable[Tuple[CmnEvent | CmnWatchpoint, ...]]
) -> dict[Tuple[CmnEvent | CmnWatchpoint, ...], Tuple[float, ...]]:
    return {group: tup_val(group) for group in groups}


def assert_reference_text(actual: str, reference_path: PathLike, regen_reference_mode: str) -> None:
    if regen_reference_mode not in ("off", "write", "dryrun"):
        raise ValueError(f"Invalid regen_reference_mode: {regen_reference_mode}")

    reference_path = Path(reference_path)

    if regen_reference_mode == "write":
        reference_path.write_text(actual, encoding="utf-8")
        return

    if not reference_path.exists():
        raise AssertionError(f"Reference file does not exist: {reference_path}")

    expected = reference_path.read_text(encoding="utf-8")

    if actual == expected:
        return

    if regen_reference_mode == "off":
        raise AssertionError(
            f"\nOutput did not match reference file:\n  {reference_path}\n"
            "To update, run pytest with --regen-reference=write\n"
            "To preview changes, run pytest with --regen-reference=dryrun\n"
        )

    diff = "\n".join(
        unified_diff(
            expected.splitlines(),
            actual.splitlines(),
            fromfile="reference",
            tofile="actual",
            lineterm="",
        )
    )
    raise AssertionError(
        f"\nOutput differs from reference ({reference_path}) [dryrun]:\n"
        f"{diff}\n"
        "To update, run pytest with --regen-reference=write\n"
    )


def assert_reference_file(actual_path: PathLike, reference_path: PathLike, regen_reference_mode: str) -> None:
    actual_path = Path(actual_path)

    if not actual_path.exists():
        raise AssertionError(f"File does not exist: {actual_path}")

    actual = actual_path.read_text(encoding="utf-8")
    assert_reference_text(actual, reference_path, regen_reference_mode)
