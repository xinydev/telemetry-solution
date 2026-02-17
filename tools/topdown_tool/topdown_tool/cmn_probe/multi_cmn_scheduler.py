# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

"""
Multi-CMN Scheduler
===================

Provides a seamless proxy layer (`MultiCmnScheduler`) to handle
event group optimization and result retrieval for multiple Core Mesh Network (CMN)
instances at once. This proxy uses user-facing Event and Watchpoint
classes directly with the underlying scheduler, returns optimized event
groups using original event objects, and supports result mapping and
retrieval in multi-CMN setups.

Features:
- Transparent deduplication of real events for scheduling
- Multi-CMN aware: events bucketed and packed per `cmn_index`
- High-level API mirrors the original CmnScheduler for simple adoption

Exports:
    - MultiCmnScheduler

"""

from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union, cast

from .common import Event, Watchpoint
from .scheduler import CmnInfo, CmnScheduler

__all__ = ["MultiCmnScheduler"]


# ---------------------------------------------------------------------
# Key-to-real event mapping, used by proxy for round-trip retrieval
# ---------------------------------------------------------------------
# Within MultiCmnScheduler:
#   _key_to_real: dict[int, dict[str, Union[Event, Watchpoint]]]
# Populated as events are converted for each CMN in the proxy's __init__


class MultiCmnScheduler:
    """
    Proxy facade for multi-CMN event group scheduling and result retrieval.

    - Accepts metric event tuples (as real event objects) tagged with cmn_index
    - Internally deduplicates and buckets them by CMN instance
    - Uses a separate CmnScheduler for each CMN with optimized key mapping
    - get_optimized_event_groups(): returns flat list of groups, using original real event objects
    - retrieve_metric_result(): maps back perf results with correct order and value for each submitted tuple

    All order and error-handling semantics are inherited from CmnScheduler and
    its internal contract.
    """

    def __init__(
        self,
        metrics: Sequence[Tuple[Union[Event, Watchpoint], ...]],
        cmn_info_map: Mapping[int, CmnInfo],
    ):
        """
        metrics: Sequence of event tuples (each tuple must refer to events for the same cmn_index)
        cmn_info_map: Mapping from cmn_index to CmnInfo describing fabric topology for each CMN instance
        """
        self._cmn_info_map = cmn_info_map
        self._sched_per_cmn: Dict[int, CmnScheduler] = {}
        self._real_tuple_to_cmn: Dict[Tuple[Union[Event, Watchpoint], ...], int] = {}
        self._key_to_real: Dict[int, Dict[str, List[Union[Event, Watchpoint]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        # Group metric tuples by cmn_index, validate membership
        per_cmn: Dict[int, List[Tuple[Union[Event, Watchpoint], ...]]] = defaultdict(list)
        for tup in metrics:
            if not tup:
                continue
            cmn_set = {e.cmn_index for e in tup}
            if None in cmn_set:
                raise ValueError(f"Event tuple {tup} is missing cmn_index on some event(s)")
            if len(cmn_set) != 1:
                raise ValueError(f"Metric tuple contains events from multiple cmn_index: {cmn_set}")
            cmn_idx = list(cmn_set)[0]
            per_cmn[cmn_idx].append(tup)
            self._real_tuple_to_cmn[tup] = cmn_idx
        # Create CmnScheduler and mapping for each CMN
        for cmn_idx, tuples in per_cmn.items():
            cmn_info = cmn_info_map[cmn_idx]
            converted: List[Tuple[Any, ...]] = []
            for tup in tuples:
                conv_events: List[Union[Event, Watchpoint]] = []
                for ev in tup:
                    if not isinstance(ev, (Event, Watchpoint)):
                        raise TypeError(f"Event {ev} is not a recognized Union[Event, Watchpoint]")
                    conv_events.append(ev)
                    self._key_to_real[cmn_idx][ev.key()].append(ev)
                converted.append(tuple(conv_events))
            self._sched_per_cmn[cmn_idx] = CmnScheduler(converted, cmn_info)

    def get_optimized_event_groups(self) -> List[Tuple[Union[Event, Watchpoint], ...]]:
        """
        Returns a flat list of event groups for all fabrics, concatenated by cmn_index.
        Each group contains exactly the deduplicated real event objects needed for instrumentation.
        """
        groups: List[Tuple[Union[Event, Watchpoint], ...]] = []
        for cmn_idx in sorted(self._sched_per_cmn):
            sched = self._sched_per_cmn[cmn_idx]
            for group in sched.get_optimized_event_groups():
                real_group = []
                for ev in group:
                    k = ev.key()
                    # Value semantics: use the first recorded instance for this key
                    real_instances = self._key_to_real[cmn_idx][k]
                    real_group.append(real_instances[0])
                groups.append(tuple(real_group))
        return groups

    def retrieve_metric_result(
        self,
        perf_result: Mapping[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float], ...]],
        metric_events: Tuple[Union[Event, Watchpoint], ...],
    ) -> Tuple[Any, ...]:
        """
        Retrieve result for the given real event tuple using the perf_result as keyed by optimized groups.
        The output is ordered and duplicates are preserved according to user-submitted tuples.
        """
        cmn_idx = self._real_tuple_to_cmn.get(metric_events)
        if cmn_idx is None:
            raise KeyError(f"Input metric tuple {metric_events} was not provided at construction")
        sched = self._sched_per_cmn[cmn_idx]
        scheduler_groups = set(sched.get_optimized_event_groups())
        # Build a perf_result mapping containing **only** the groups that belong to the
        # CMN instance currently being queried. CmnScheduler enforces an exact key
        # match between perf_result and optimized groups, so we must filter out other
        # CMN groups to avoid a KeyError.
        perf_result_internal: Dict[
            Tuple[Union[Event, Watchpoint], ...], Tuple[Union[float, None], ...]
        ] = {}
        for real_group, vals in perf_result.items():
            # Skip any group that is not exclusively from this cmn_idx
            group_cmns = {ev.cmn_index for ev in real_group}
            if len(group_cmns) != 1:
                raise ValueError(f"Multiple CMN for result {real_group} => {vals}")
            if group_cmns != {cmn_idx}:
                continue  # belongs to a different fabric – ignore
            if real_group in scheduler_groups:
                perf_result_internal[real_group] = vals
        result = sched.retrieve_metric_result(perf_result_internal, metric_events)
        return result

    # ------------------------------------------------------------------
    # Optional high-performance path: prepared perf_result
    # ------------------------------------------------------------------

    def prepare_perf_result(
        self,
        perf_result: Mapping[Tuple[Union[Event, Watchpoint], ...], Tuple[Any, ...]],
    ) -> Dict[int, Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Any, ...]]]:
        """Convert *perf_result* keyed by Union[Event, Watchpoint] tuples into a per-CMN
        mapping keyed by event tuples.

        Returned structure::

            {
                cmn_index: {
                    (Event|Watchpoint, ...): (<values tuple>),
                    ...
                },
                ...
            }
        """
        prepared: Dict[int, Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Any, ...]]] = defaultdict(
            dict
        )

        for real_group, values in perf_result.items():
            if not real_group:
                continue  # skip empty

            cmn_set = {ev.cmn_index for ev in real_group}
            if len(cmn_set) != 1:
                raise ValueError(f"perf_result group spans multiple cmn_index values: {cmn_set}")
            cmn_idx = next(iter(cmn_set))
            prepared[cmn_idx][real_group] = values

        return prepared

    def retrieve_metric_result_prepared(
        self,
        prepared_perf_result: Mapping[
            int, Mapping[Tuple[Union[Event, Watchpoint], ...], Tuple[Any, ...]]
        ],
        metric_events: Tuple[Union[Event, Watchpoint], ...],
    ) -> Tuple[Any, ...]:
        """Fast variant operating on a mapping prepared by *prepare_perf_result*."""
        cmn_idx = self._real_tuple_to_cmn.get(metric_events)
        if cmn_idx is None:
            raise KeyError("Metric tuple not part of scheduler input.")

        perf_map = prepared_perf_result.get(cmn_idx)
        if perf_map is None:
            raise KeyError(f"No perf_result for cmn_index {cmn_idx}")

        sched = self._sched_per_cmn[cmn_idx]
        return sched.retrieve_metric_result(
            cast(
                Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float], ...]], perf_map
            ),
            metric_events,
        )
