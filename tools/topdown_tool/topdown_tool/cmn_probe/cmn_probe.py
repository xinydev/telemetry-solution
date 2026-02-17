# Copyright 2025 Arm Limited
# SPDX-License-Identifier: Apache-2.0

# pylint: disable=too-many-lines

"""
This module implements the CmnProbe class, which is responsible for capturing and analyzing CMN
performance metrics. CmnProbe interacts with a CmnDatabase to retrieve events, metrics, and groups.
It leverages a PerfFactory to construct concrete Perf instances for recording hardware counters,
automatically selecting the appropriate backend (e.g., LinuxPerf or WindowsPerf). The module
integrates CMN-specific profiling logic into the larger telemetry/profiling system, supporting use
cases such as system-wide performance data capture and detailed metric computation via defined
formulas.
"""

import csv
import logging
import os
import shutil
import tempfile
from enum import Enum
from re import sub
from sys import platform
from threading import Event as ThreadEvent
from threading import Lock, Thread
from time import time
from typing import Callable, Dict, Iterator, List, Optional, Set, Tuple, TypedDict, Union, cast

from rich import get_console
from rich.table import Table

import topdown_tool.probe as Base
from topdown_tool.cmn_probe.cmn_database import CmnDatabase, DeviceType
from topdown_tool.cmn_probe.common import (
    CmnLocation,
    CmnProbeFactoryConfig,
    Event,
    Location,
    MetricDetails,
    NodeLocation,
    PortLocation,
    TopdownMetricDetails,
    Watchpoint,
    XpLocation,
)
from topdown_tool.cmn_probe.multi_cmn_scheduler import MultiCmnScheduler
from topdown_tool.cmn_probe.scheduler import CmnInfo
from topdown_tool.common import simple_maths
from topdown_tool.layout import Header, SplitTable
from topdown_tool.perf import PerfFactory, perf_factory
from topdown_tool.perf.event_scheduler import CollectBy
from topdown_tool.perf.perf import Perf
from topdown_tool.perf.windows_perf import WindowsPerf

if platform == "win32":
    from topdown_tool.perf.windows_coordinator import WperfCoordinator


# pylint: disable=missing-class-docstring
class CaptureState(Enum):
    NOT_RUNNING = 0
    RUNNING = 1
    STOP_REQUESTED = 2
    STOPPED = 3


# pylint: disable=missing-class-docstring
class EventResult(TypedDict):
    value: Optional[float]
    subevent_count: int


class GroupData(TypedDict):
    """
    Metrics and events related information for a group
    """
    # Keys: Metric Name; Location; Event Name
    # Final value: (Event Aggregated Value; Number of Subevents)
    events_values: Dict[str, Dict[Location, Dict[str, EventResult]]]

    # Keys: Metric Name; Location
    metrics_values: Dict[str, Dict[Location, Optional[float]]]

    # Keys: Topdown Metric Name; Location
    topdown_metrics_values: Dict[str, Dict[CmnLocation, Optional[Union[float, str]]]]

    group_running_time: float

    # Keys: Metric Name; Location
    original_metrics: Dict[
        str, Dict[Location, List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]]]
    ]

    events_for_perf: Tuple[Tuple[Union[Event, Watchpoint], ...], ...]

    topdown_group: bool

    scheduler: Optional[MultiCmnScheduler]

    optimized_events_for_perf: Tuple[Tuple[Union[Event, Watchpoint], ...], ...]

    restore_information: Dict[str, Dict[Location, List[int]]]


# pylint: disable=too-many-instance-attributes
class CmnProbe(Base.Probe):
    # pylint: disable=too-many-arguments, too-many-branches, too-many-statements, too-many-positional-arguments, broad-exception-raised
    def __init__(
        self,
        conf: CmnProbeFactoryConfig,
        database: CmnDatabase,
        capture_data: bool,
        base_csv_dir: Optional[str],
        perf_factory_instance: PerfFactory = perf_factory,
    ):
        super().__init__()

        self.database: CmnDatabase = database
        self.conf: CmnProbeFactoryConfig = conf
        self.perf_factory: PerfFactory = perf_factory_instance

        self.version = self.database.get_version()

        self.pid: Set[int] = set()
        self.capture_data: bool = False
        self.captured: bool = False
        self.interrupted: bool = False
        self.tmp_dir: str
        self.base_csv_dir: Optional[str] = base_csv_dir

        self.capture_thread: Thread
        self.capture_thread_state: CaptureState = CaptureState.NOT_RUNNING
        self.capture_thread_state_mtx: Lock = Lock()
        self.first_capture_started: ThreadEvent = ThreadEvent()

        # Total collection time
        self.total_running_time: float = 0.0

        # Frequency
        self.frequency: Dict[int, float] = {}

        self.perf_instance: Perf

        if self.conf.cmn_list_devices:
            self.list_devices()

        if self.conf.cmn_list_events is not None:
            self.list_events()

        if self.conf.cmn_list_metrics is not None:
            self.list_metrics()

        if self.conf.cmn_list_groups is not None:
            self.list_groups()

        # Querying option chosen - no collection
        if not capture_data:
            return

        perf_class = self.perf_factory.get_platform_class()

        self.max_mux_interval: int = 1
        for cmn_index in self.database.cmn_indices:
            self.frequency[cmn_index] = perf_class.get_cmn_frequency(cmn_index)
            max_mux_interval = perf_class.get_cmn_mux_interval(cmn_index)
            self.max_mux_interval = max(self.max_mux_interval, max_mux_interval)

        self.metrics_to_collect: Dict[str, Tuple[str, ...]]

        self.base_metrics_capture: bool = False
        self.topdown_metrics_capture: bool = False

        metrics_to_collect: Optional[Dict[str, Tuple[str, ...]]] = None
        topdown_metrics_to_collect: Optional[Dict[str, Tuple[str, ...]]] = None

        # Base metrics handling
        if self.conf.groups is not None:
            metrics_to_collect = self.database.get_collectable_groups(self.conf.groups)
        if self.conf.metrics is not None:
            new_metrics_to_collect = self.database.get_collectable_metrics(self.conf.metrics)
            if metrics_to_collect is None:
                metrics_to_collect = new_metrics_to_collect
            else:
                for group in new_metrics_to_collect:
                    metrics_to_collect[group] = new_metrics_to_collect[group]
        if metrics_to_collect is not None and len(metrics_to_collect) > 0:
            self.base_metrics_capture = True
        else:
            metrics_to_collect = None

        # Topdown metrics handling
        self.topdown_metrics_to_collect, topdown_metrics_to_collect = self.database.get_collectable_topdown_metrics(self.conf.metrics, self.conf.groups)
        self.collectable_base_metrics_for_topdown_groups = self.database.get_collectable_base_metrics_for_topdown_group(self.conf.groups) if not self.conf.metrics else {}
        for topdown_group in self.collectable_base_metrics_for_topdown_groups:
            if topdown_group in topdown_metrics_to_collect:
                topdown_metrics_to_collect[topdown_group] = tuple(set(topdown_metrics_to_collect[topdown_group]) | set(self.collectable_base_metrics_for_topdown_groups[topdown_group]))
            else:
                topdown_metrics_to_collect[topdown_group] = self.collectable_base_metrics_for_topdown_groups[topdown_group]

        if topdown_metrics_to_collect is not None and len(topdown_metrics_to_collect) > 0:
            self.topdown_metrics_capture = True
        else:
            topdown_metrics_to_collect = None

        if self.topdown_metrics_capture and self.base_metrics_capture:
            assert isinstance(topdown_metrics_to_collect, dict)
            assert isinstance(metrics_to_collect, dict)
            self.metrics_to_collect = topdown_metrics_to_collect | metrics_to_collect
        elif self.topdown_metrics_capture:
            assert isinstance(topdown_metrics_to_collect, dict)
            self.metrics_to_collect = topdown_metrics_to_collect
        elif self.base_metrics_capture:
            assert isinstance(metrics_to_collect, dict)
            self.metrics_to_collect = metrics_to_collect
        else:
            logging.info("No CMN metrics to collect")
            self.capture_data = False
            return

        # Create schedule for CMN
        self.create_schedule()

        # Mark state as ready to capture
        self.capture_data = True

    def uses_windows_perf(self) -> bool:
        """Is Windows Perf in use

        Returns:
            bool: is Windows Perf in use
        """
        return self.perf_factory.get_platform_class() is WindowsPerf

    def need_capture(self) -> bool:
        """Returns information whether capture is needed. CMN requires a single capture due to
        enforced multiplexing. Returns true for the first call and false for each subsequent call.

        Returns:
            bool: information whether capture is needed
        """
        if not self.capture_data:
            return False
        captured: bool = self.captured
        self.captured = True
        return not captured

    # pylint: disable=consider-merging-isinstance, too-many-locals
    def create_schedule(self) -> None:
        """Prepares a capture schedule for either base metrics or topdown metrics
        """
        # Keys: Metric Name
        self.metrics_details: Dict[str, MetricDetails] = {}

        # Keys: Topdown Metric Name
        self.topdown_metrics_details: Dict[str, TopdownMetricDetails] = {}

        # Keys: Group Name
        self.group_data: Dict[str, GroupData] = {}

        used_cmns: Set[int] = set()

        def make_dtc_of(cmn_index: int) -> Callable[[int], int]:
            def dtc_of(xp: int) -> int:
                return self.database.dtc_of(cmn_index, xp)
            return dtc_of

        cmn_info_map: Dict[int, CmnInfo] = {}
        for cmn_index in self.database.get_indices():
            cmn_info_map[cmn_index] = CmnInfo(
                self.database.get_dtc_count(cmn_index),
                make_dtc_of(cmn_index),
                self.database.cmn_topology(cmn_index),
                self.database.watchpoint_port_map(cmn_index),
                global_type_aliases=dict(Event.LINUX_FIX_MAP),
            )

        topdown_groups: Set[str] = set(self.topdown_metrics_to_collect.keys()) | set(self.collectable_base_metrics_for_topdown_groups.keys())

        for group_name, metrics in self.metrics_to_collect.items():
            is_topdown_group: bool = group_name in topdown_groups

            self.group_data[group_name] = {
                "events_values": {},
                "metrics_values": {},
                "topdown_metrics_values": {},
                "group_running_time": 0.0,
                "original_metrics": {},
                "events_for_perf": (),
                "topdown_group": is_topdown_group,
                "scheduler": None,
                "optimized_events_for_perf": (),
                "restore_information": {}
            }

            for metric_name in metrics:
                self.group_data[group_name]["original_metrics"][metric_name] = cast(
                    Dict[
                        Union[CmnLocation, XpLocation, PortLocation, NodeLocation],
                        List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]],
                    ],
                    self.database.get_schedulable_events_for_metric(
                        metric_name, is_topdown_group or not self.conf.capture_per_device_id
                    ),
                )
                self.database.merge_events(
                    self.group_data[group_name]["original_metrics"][metric_name],
                    cast(
                        Dict[
                            Union[CmnLocation, XpLocation, PortLocation, NodeLocation],
                            List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]],
                        ],
                        self.database.get_schedulable_xp_events_for_metric(
                            metric_name, is_topdown_group or not self.conf.capture_per_device_id
                        ),
                    ),
                )
                self.database.merge_events(
                    self.group_data[group_name]["original_metrics"][metric_name],
                    cast(
                        Dict[
                            Union[CmnLocation, XpLocation, PortLocation, NodeLocation],
                            List[Union[Event, List[Event], Watchpoint, List[Watchpoint]]],
                        ],
                        self.database.get_schedulable_watchpoints_for_metric(
                            metric_name, is_topdown_group or not self.conf.capture_per_device_id, platform == "linux"
                        ),
                    ),
                )

            # Prepare events for perf
            if self.conf.collect_by == CollectBy.METRIC:
                # self.group_data[group_name]["events_for_perf"] = self.database.flatten_events(
                #     self.group_data[group_name]["original_metrics"]
                # )

                events_for_perf, restore_information = self.database.regroup_events(
                    self.group_data[group_name]["original_metrics"]
                )

                flattened_events_for_perf = []
                for metric in events_for_perf.values():
                    for located_perf_group in metric.values():
                        for perf_subgroup in located_perf_group:
                            flattened_events_for_perf.append(tuple(perf_subgroup))
                self.group_data[group_name]["events_for_perf"] = tuple(flattened_events_for_perf)

                self.group_data[group_name]["restore_information"] = restore_information

            elif self.conf.collect_by == CollectBy.NONE:
                self.group_data[group_name]["events_for_perf"] = tuple(
                    (event,)
                    for event in self.database.eliminate_duplicated_events(
                        self.group_data[group_name]["original_metrics"]
                    )
                )

            for metric_name, located_metric in self.group_data[group_name]["original_metrics"].items():
                self.group_data[group_name]["events_values"][metric_name] = {}
                self.group_data[group_name]["metrics_values"][metric_name] = {}
                if metric_name not in self.metrics_details:
                    self.metrics_details[metric_name] = self.database.get_metric_details(metric_name)
                for location, events in located_metric.items():
                    used_cmns.add(location.cmn_index)
                    self.group_data[group_name]["events_values"][metric_name][location] = {}
                    self.group_data[group_name]["metrics_values"][metric_name][location] = None
                    for event in events:
                        if isinstance(event, Event) or isinstance(event, Watchpoint):
                            self.group_data[group_name]["events_values"][metric_name][location][
                                event.name
                            ] = EventResult(value=None, subevent_count=1)
                        else:
                            self.group_data[group_name]["events_values"][metric_name][location][
                                event[0].name
                            ] = EventResult(value=None, subevent_count=len(event))

            scheduler = MultiCmnScheduler(self.group_data[group_name]["events_for_perf"], cmn_info_map)
            self.group_data[group_name]["scheduler"] = scheduler
            self.group_data[group_name]["optimized_events_for_perf"] = tuple(scheduler.get_optimized_event_groups())

        self.used_cmns = tuple(sorted(used_cmns))

        if self.topdown_metrics_capture:
            for topdown_group_name, topdown_metrics in self.topdown_metrics_to_collect.items():
                self.group_data[topdown_group_name]["topdown_metrics_values"] = {}
                for topdown_metric in topdown_metrics:
                    self.group_data[topdown_group_name]["topdown_metrics_values"][
                        topdown_metric
                    ] = {}
                    if topdown_metric not in self.topdown_metrics_details:
                        self.topdown_metrics_details[
                            topdown_metric
                        ] = self.database.get_topdown_metric_details(topdown_metric)
                    for cmn_index in self.used_cmns:
                        self.group_data[topdown_group_name]["topdown_metrics_values"][
                            topdown_metric
                        ][CmnLocation(cmn_index=cmn_index)] = None

    def start_capture(self, run: int = 1, pids: Optional[Union[int, Set[int]]] = None) -> None:
        """Convert hex to a signed 64 bit int

        Args:
            s (str): string containing hex number

        Returns:
            int: value of the number
        """
        if isinstance(pids, int):
            self.pid.add(pids)
        elif pids is not None:
            self.pid = set(pids)

        # User chosen / default output directory
        if self.conf.debug_path:
            if os.path.exists(self.conf.debug_path):
                shutil.rmtree(self.conf.debug_path)
            os.makedirs(self.conf.debug_path, 0o755, True)
            self.tmp_dir = self.conf.debug_path
        else:
            self.tmp_dir = tempfile.mkdtemp()

        self.capture_thread_state = CaptureState.RUNNING

        self.capture_thread = Thread(target=self.thread_func)
        self.capture_thread.start()
        self.first_capture_started.wait()

    # pylint: disable=too-many-nested-blocks, unused-variable
    def update_events_values_collect_by_metric(
        self,
        group_name: str,
        records: Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float]]],
    ) -> None:
        """Updates events values based on passed result in records (from perf output)

        Args:
            group_name (str): group name
            records (Dict[Tuple[Union[Event, Watchpoint]], Tuple[Optional[float]]]):
        """
        perf_event_iterator: Iterator[Optional[float]]

        for metric_name, metric in self.group_data[group_name]["events_values"].items():
            for location, located_metric in metric.items():
                key = tuple(event for event_list in self.group_data[group_name]["original_metrics"][metric_name][location] for event in (event_list if isinstance(event_list, list) else [event_list]))
                perf_event_iterator = iter(records[key])
                for event_result in located_metric.values():
                    for subevent_index in range(event_result["subevent_count"]):
                        value = next(perf_event_iterator)
                        if value is not None:
                            if event_result["value"] is None:
                                event_result["value"] = value
                            else:
                                event_result["value"] += value
                try:
                    next(perf_event_iterator)
                    assert False, "Perf events iterator is not exhausted"
                except StopIteration:
                    pass

    # pylint: disable=too-many-locals, too-many-nested-blocks
    def update_events_values_collect_by_none(
        self,
        group_name: str,
        records: Dict[Tuple[Union[Event, Watchpoint]], Tuple[Optional[float]]],
    ) -> None:
        """Updates events values based on passed result in records (from perf output)

        Args:
            group_name (str): group name
            records (Dict[Tuple[Union[Event, Watchpoint]], Tuple[Optional[float]]]):
        """
        mapping: Dict[Union[Event, Watchpoint], Optional[float]] = {}
        for perf_group, perf_group_values in records.items():
            for event_name, value in zip(perf_group, perf_group_values):
                assert event_name not in mapping
                mapping[event_name] = value

        for metric_name, metric in self.group_data[group_name]["original_metrics"].items():
            for location, located_metric in metric.items():
                located_events = self.group_data[group_name]["events_values"][metric_name][location]
                for event in located_metric:
                    if isinstance(event, Event) or isinstance(event, Watchpoint):
                        located_event = located_events[event.name]
                        value = mapping[event]
                        if value is not None:
                            if located_event["value"] is not None:
                                located_event["value"] += value
                            else:
                                located_event["value"] = value
                    else:
                        for subevent in event:
                            located_event = located_events[subevent.name]
                            value = mapping[subevent]
                            if value is not None:
                                if located_event["value"] is not None:
                                    located_event["value"] += value
                                else:
                                    located_event["value"] = value

    # pylint: disable=consider-using-dict-items, consider-using-with
    def thread_func(self) -> None:
        """Capture thread, spawns perf for each (topdown) metric group for a specified amount of
        time. Runs until stop is requested from the main thread. After each perf run, updates total
        and group running time, and updates events values.
        """
        # perf run
        run = 0

        # Time needed to scale events results (also needed for some topdown metrics)
        global_start_time = time()

        # Create perf instance and start collection
        while True:
            for group_name in self.group_data:
                optimized_perf_groups = self.group_data[group_name]["optimized_events_for_perf"]
                self.capture_thread_state_mtx.acquire()
                if self.capture_thread_state == CaptureState.STOP_REQUESTED:
                    global_stop_time = time()
                    self.total_running_time = global_stop_time - global_start_time
                    self.capture_thread_state_mtx.release()
                    return

                run += 1
                logging.debug("Perf run #%d Group #%s", run, group_name)
                dir_for_run = os.path.join(self.tmp_dir, str(run))
                os.mkdir(dir_for_run)
                if len(self.group_data) == 1:
                    timeout = None
                else:
                    timeout = int(
                        max(
                            len(optimized_perf_groups) * self.max_mux_interval,
                            100,
                        )
                    )
                self.perf_instance = self.perf_factory.create()
                if platform == "win32":
                    windows_perf_instance: WindowsPerf = cast(WindowsPerf, self.perf_instance)
                    windows_perf_instance.use_parser_for_class(self.__class__)
                self.perf_instance.enable()
                start_time = time()
                self.perf_instance.start(
                    optimized_perf_groups,
                    str(dir_for_run)
                    + "/perf.stat.cmn-"
                    + str(self.version).lower()
                    + ".run"
                    + str(run)
                    + ".txt",
                    None,
                    None,
                    timeout,
                )
                if run == 1:
                    self.first_capture_started.set()
                self.capture_thread_state_mtx.release()
                self.perf_instance.wait()
                end_time = time()
                try:
                    perf_result = next(iter(self.perf_instance.get_perf_result().values()))[None]
                except Exception:  # pylint: disable=broad-exception-caught, broad-except
                    # Stop was requested and Perf was terminated before it captured anything
                    continue
                finally:
                    self.perf_instance.disable()
                running_time = end_time - start_time

                self.group_data[group_name]["group_running_time"] += running_time

                if platform == "win32":
                    WperfCoordinator.get_instance().cleanup()

                restored_perf_result: Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float]]] = {}
                for perf_group in self.group_data[group_name]["events_for_perf"]:
                    scheduler = self.group_data[group_name]["scheduler"]
                    assert scheduler is not None
                    restored_perf_result[perf_group] = scheduler.retrieve_metric_result({
                        cast(Tuple[Union[Event, Watchpoint], ...], k): v
                        for k, v in perf_result.items()
                    }, perf_group)

                if self.conf.collect_by == CollectBy.METRIC:
                    regrouped_perf_result: Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float], ...]] = {}
                    optimized_perf_group_iterator = iter(self.group_data[group_name]["events_for_perf"])
                    for metric_name, metric in self.group_data[group_name]["restore_information"].items():
                        for location, restore_information in metric.items():
                            remaining_events_count = len(restore_information)
                            regrouped_results: List[Optional[float]] = [None] * remaining_events_count
                            index: int = 0
                            while remaining_events_count > 0:
                                optimized_perf_group = next(optimized_perf_group_iterator)
                                for result in restored_perf_result[optimized_perf_group]:
                                    regrouped_results[restore_information[index]] = result
                                    index += 1
                                remaining_events_count -= len(optimized_perf_group)
                            assert remaining_events_count == 0
                            key: List[Union[Event, Watchpoint]] = []
                            for event in self.group_data[group_name]["original_metrics"][metric_name][location]:
                                if isinstance(event, list):
                                    key.extend(event)
                                else:
                                    key.append(event)
                            regrouped_perf_result[tuple(key)] = tuple(regrouped_results)
                    self.update_events_values_collect_by_metric(
                        group_name,
                        cast(
                            Dict[Tuple[Union[Event, Watchpoint], ...], Tuple[Optional[float]]],
                            regrouped_perf_result,
                        ),
                    )
                elif self.conf.collect_by == CollectBy.NONE:
                    self.update_events_values_collect_by_none(
                        group_name,
                        cast(
                            Dict[Tuple[Union[Event, Watchpoint]], Tuple[Optional[float]]],
                            restored_perf_result,
                        ),
                    )

    def stop_capture(
        self, run: int = 1, pid: Optional[int] = None, interrupted: bool = False
    ) -> None:
        """Stop perf instance

        Args:
            run (int): capture number (unused due to enforced multiplexing, provided for
            compatibility with other probes)
            pid (Optional[int]): stop requested due to this pid terminating
            interrupted (bool): whether capture was interrupted by SIGINT (currently unused)
        """
        # Remove pid from a set of monitored processes
        if pid is not None:
            self.pid.discard(pid)

        # If there are no more processes to monitor, stop perf and get output
        if not self.pid:
            with self.capture_thread_state_mtx:
                self.capture_thread_state = CaptureState.STOP_REQUESTED
                self.perf_instance.stop()

    def calculate_metrics(self) -> None:
        """Calculate base and topdown metrics from collected result
        """
        self.capture_thread.join()
        self.capture_thread_state = CaptureState.STOPPED
        logging.debug("Total running time: %f", self.total_running_time)

        # Scale events values
        for group_name in self.group_data:
            group = self.group_data[group_name]["events_values"]
            try:
                scaling_factor = (
                    self.total_running_time / self.group_data[group_name]["group_running_time"]
                )
            except ZeroDivisionError:
                # Group didn't run
                continue
            for metric in group.values():
                for located_metric in metric.values():
                    for event_result in located_metric.values():
                        if event_result["value"] is not None:
                            event_result["value"] *= scaling_factor

        if self.base_csv_dir is not None and self.conf.cmn_generate_events_csv:
            os.makedirs(os.path.join(self.base_csv_dir, "cmn"), 0o755, True)
            events_csv_file = open(
                os.path.join(
                    self.base_csv_dir, "cmn", f"cmn_{self.version.lower().replace(' ', '_')}_events.csv"
                ),
                "w",
                encoding="utf-8",
                newline="",
            )
            events_csv_writer = csv.writer(events_csv_file)
            events_csv_writer.writerow(
                ["Group", "Metric", "Event", "CMN", "X", "Y", "Port", "Node", "Value"]
            )
            for group_name in self.group_data:
                group = self.group_data[group_name]["events_values"]
                for metric_name in sorted(group.keys()):
                    metric = group[metric_name]
                    for location, located_metric in metric.items():
                        for event_name, event in located_metric.items():
                            coordinate_x, coordinate_y = self.database.get_coordinates(location)
                            port: Optional[int]
                            node: Optional[int]
                            if isinstance(location, NodeLocation):
                                port = location.port
                                node = location.node_id
                            elif isinstance(location, PortLocation):
                                port = location.port
                                node = self.database.get_node_id_of_port(location)
                            elif isinstance(location, XpLocation):
                                port = None
                                node = location.xp_id
                            elif isinstance(location, CmnLocation):
                                port = None
                                node = None
                            events_csv_writer.writerow(
                                [
                                    group_name,
                                    metric_name,
                                    event_name,
                                    location.cmn_index,
                                    coordinate_x,
                                    coordinate_y,
                                    port,
                                    node,
                                    event["value"],
                                ]
                            )

        # Update base metrics
        for base_group_name in self.group_data:
            base_group = self.group_data[base_group_name]["metrics_values"]
            for base_metric_name, base_metric in base_group.items():
                for location in base_metric:
                    equation = self.metrics_details[base_metric_name].formula
                    equation = sub(r"\b" + "texec" + r"\b", str(self.total_running_time), equation)
                    equation = sub(
                        r"\b" + "SYS_FREQUENCY" + r"\b",
                        str(self.frequency[location.cmn_index]),
                        equation,
                    )
                    for event_name, event_results in self.group_data[base_group_name][
                        "events_values"
                    ][base_metric_name][location].items():
                        equation = sub(
                            r"\b" + event_name + r"\b", str(event_results["value"]), equation
                        )
                    try:
                        base_metric[location] = simple_maths.evaluate(equation)
                    except Exception:  # pylint: disable=broad-exception-caught
                        # Metric incalculable
                        pass

        # Update Topdown Metrics
        for topdown_group_name in self.group_data:
            topdown_group = self.group_data[topdown_group_name]["topdown_metrics_values"]
            any_not_none: Dict[Tuple[str, CmnLocation], bool] = {}
            # Substitute base metrics
            for topdown_metric_name, topdown_metric in topdown_group.items():
                for location in topdown_metric:
                    any_not_none[(topdown_metric_name, location)] = False
                    equation = self.topdown_metrics_details[topdown_metric_name].formula
                    equation = sub(r"\b" + "texec" + r"\b", str(self.total_running_time), equation)
                    equation = sub(
                        r"\b" + "SYS_FREQUENCY" + r"\b",
                        str(self.frequency[location.cmn_index]),
                        equation,
                    )
                    for base_metric_name in self.topdown_metrics_details[
                        topdown_metric_name
                    ].base_metrics:
                        try:
                            value = self.group_data[topdown_group_name]["metrics_values"][
                                base_metric_name
                            ][location]
                        except KeyError:
                            value = None
                        if value is not None:
                            equation = sub(r"\b" + base_metric_name + r"\b", str(value), equation)
                            any_not_none[(topdown_metric_name, location)] = True
                        else:
                            equation = sub(r"\b" + base_metric_name + r"\b", "0", equation)
                    try:
                        topdown_metric[location] = simple_maths.evaluate(equation)
                    except Exception:  # pylint: disable=broad-exception-caught
                        topdown_metric[location] = equation
            # Substitute topdown metrics
            progress: bool = True
            while progress:
                progress = False
                for topdown_metric_name, topdown_metric in topdown_group.items():
                    for location in topdown_metric:
                        formula = topdown_metric[location]
                        if isinstance(formula, str):
                            for topdown_metric_name2 in self.topdown_metrics_details[
                                topdown_metric_name
                            ].topdown_metrics:
                                expression: Optional[Union[str, float]] = None
                                try:
                                    expression = self.group_data[topdown_group_name]["topdown_metrics_values"][topdown_metric_name2][location]
                                except KeyError:
                                    pass
                                if expression is None:
                                    formula = sub(
                                        r"\b" + topdown_metric_name2 + r"\b", "0", formula
                                    )
                                elif isinstance(expression, float):
                                    formula = sub(
                                        r"\b" + topdown_metric_name2 + r"\b",
                                        str(expression),
                                        formula,
                                    )
                                    if any_not_none[(topdown_metric_name2, location)]:
                                        any_not_none[(topdown_metric_name, location)] = True
                            try:
                                topdown_metric[location] = simple_maths.evaluate(formula)
                                progress = True
                            except Exception:  # pylint: disable=broad-exception-caught
                                # Metric incalculable
                                pass
            # Update state
            for topdown_metric_name, topdown_metric in topdown_group.items():
                for location in topdown_metric:
                    if (
                        isinstance(topdown_metric[location], str)
                        or not any_not_none[(topdown_metric_name, location)]
                    ):
                        # Revert to None
                        topdown_metric[location] = None

    def output(self) -> None:
        """Decide which results output methods to call based on passed command line options
        """
        if not self.capture_data:
            return
        self.calculate_metrics()
        if self.topdown_metrics_capture:
            if self.conf.cmn_generate_metrics_csv:
                self.write_topdown_metrics_to_csv()
            self.print_topdown_metrics_with_values()
        if self.base_metrics_capture:
            if self.conf.cmn_generate_metrics_csv:
                self.write_regular_metrics_to_csv()
            self.print_regular_metrics_with_values()

    def list_devices(self) -> None:
        """Writes information about devices to terminal with rich tables
        """
        console = get_console()

        columns = ["Device", "ID"]
        table_row: List[str]

        table = Table(title=f"CMN-{self.version} Node Devices")
        for column in columns:
            table.add_column(column)
        for device_id, device_name in self.database.get_devices(DeviceType.NODE).items():
            table_row = [device_name, str(device_id)]
            table.add_row(*table_row)
        console.print(table)

        table = Table(title=f"CMN-{self.version} Port Devices")
        for column in columns:
            table.add_column(column)
        for device_id, device_name in self.database.get_devices(DeviceType.PORT).items():
            table_row = [device_name, str(device_id)]
            table.add_row(*table_row)
        console.print(table)

    def list_events(self) -> None:
        """Writes information about events to terminal with rich tables
        """
        assert self.conf.cmn_list_events is not None

        console = get_console()
        console.print(Header(f"CMN-{self.version} Events & Watchpoints", 1))

        columns_events = ["Event", "Title"]
        if self.conf.descriptions:
            columns_events.append("Description")
        columns_events.extend(["Type", "Event ID"])

        columns_watchpoints = ["Watchpoint"]
        if self.conf.descriptions:
            columns_watchpoints.append("Description")
        columns_watchpoints.extend(["Direction", "Channel", "Group", "Mask", "Values"])

        devices = self.conf.cmn_list_events if len(self.conf.cmn_list_events) > 0 else None

        mapping: Dict[str, List[Optional[int]]] = {}
        for device_id, device in self.database.get_devices(DeviceType.NODE, devices).items():
            mapping.setdefault(device, [None, None])[0] = device_id
        for device_id, device in self.database.get_devices(DeviceType.PORT, devices).items():
            mapping.setdefault(device, [None, None])[1] = device_id

        for device, (node_device_id, port_device_id) in mapping.items():
            if node_device_id is not None:
                # Node Events
                table = None
                for event in self.database.get_json_events(node_device_id):
                    if table is None:
                        table = Table(title=f"{device} Events")
                        for column in columns_events:
                            table.add_column(column)
                    table_row = [event.name, event.title]
                    if self.conf.descriptions:
                        table_row.append(event.description)
                    table_row.extend(
                        [
                            hex(event.type).upper().replace("X", "x"),
                            hex(event.eventid).upper().replace("X", "x"),
                        ]
                    )
                    table.add_row(*table_row)
                if table is not None:
                    console.print(table)
                # Node Watchpoints
                table = None
                for watchpoint in self.database.get_json_watchpoints(
                    DeviceType.NODE, node_device_id
                ):
                    if table is None:
                        table = Table(title=f"{device} Watchpoints")
                        for column in columns_watchpoints:
                            table.add_column(column)
                    table_row = [watchpoint.name]
                    if self.conf.descriptions:
                        table_row.append(watchpoint.description)
                    table_row.extend(
                        [
                            watchpoint.mesh_flit_dir_str(),
                            watchpoint.wp_chn_sel_str(),
                            watchpoint.wp_grp_str(),
                            f"0x{watchpoint.wp_mask_normalized():016X}",
                            ", ".join(
                                f"0x{wp_val:016X}" for wp_val in sorted(watchpoint.wp_val_normalized())
                            ),
                        ]
                    )
                    table.add_row(*table_row)
                if table is not None:
                    console.print(table)
            if port_device_id is not None:
                # Port Watchpoints
                table = None
                for watchpoint in self.database.get_json_watchpoints(
                    DeviceType.PORT, port_device_id
                ):
                    if table is None:
                        table = Table(title=f"{device} Watchpoints")
                        for column in columns_watchpoints:
                            table.add_column(column)
                    table_row = [watchpoint.name]
                    if self.conf.descriptions:
                        table_row.append(watchpoint.description)
                    table_row.extend(
                        [
                            watchpoint.mesh_flit_dir_str(),
                            watchpoint.wp_chn_sel_str(),
                            watchpoint.wp_grp_str(),
                            f"0x{watchpoint.wp_mask_normalized():016X}",
                            ", ".join(
                                f"0x{wp_val:016X}" for wp_val in sorted(watchpoint.wp_val_normalized())
                            ),
                        ]
                    )
                    table.add_row(*table_row)
                if table is not None:
                    console.print(table)

    def list_metrics(self) -> None:
        """Writes information about metrics to terminal with rich tables
        """
        assert self.conf.cmn_list_metrics is not None

        console = get_console()
        console.print(Header(f"CMN-{self.version} Metrics", 1))

        columns = ["Metric", "Title"]
        if self.conf.descriptions:
            columns.append("Description")
        columns.extend(["Formula", "Units", "Events", "Watchpoints"])
        if self.conf.show_sample_events:
            columns.append("Sample events")

        devices = self.conf.cmn_list_metrics if len(self.conf.cmn_list_metrics) > 0 else None

        # Node Metrics
        for device_id, device in self.database.get_devices(DeviceType.NODE, devices).items():
            table = Table(title=f"{device} Metrics")
            for column in columns:
                table.add_column(column)
            for metric in self.database.get_json_metrics(DeviceType.NODE, device_id):
                table_row = [metric.name, metric.title]
                if self.conf.descriptions:
                    table_row.append(metric.description)
                table_row.extend(
                    [
                        metric.formula,
                        metric.units,
                        ", ".join(sorted(metric.events)),
                        ", ".join(sorted(metric.watchpoints)),
                    ]
                )
                if self.conf.show_sample_events:
                    table_row.append(", ".join(sorted(metric.sample_events)))
                table.add_row(*table_row)
            if table is not None:
                console.print(table)

        # Port Metrics
        for device_id, device in self.database.get_devices(DeviceType.PORT, devices).items():
            table = Table(title=f"{device} Metrics")
            for column in columns:
                table.add_column(column)
            for metric in self.database.get_json_metrics(DeviceType.PORT, device_id):
                table_row = [metric.name, metric.title]
                if self.conf.descriptions:
                    table_row.append(metric.description)
                table_row.extend(
                    [
                        metric.formula,
                        metric.units,
                        ", ".join(sorted(metric.events)),
                        ", ".join(sorted(metric.watchpoints)),
                    ]
                )
                if self.conf.show_sample_events:
                    table_row.append(", ".join(sorted(metric.sample_events)))
                table.add_row(*table_row)
            if table is not None:
                console.print(table)

        # Topdown Metrics
        topdown_columns = ["Metric", "Title", "Formula", "Metrics"]
        table = Table(title="Topdown Metrics")
        for column in topdown_columns:
            table.add_column(column)
        for topdown_metric in self.database.get_json_topdown_metrics():
            table_row = [topdown_metric.name, topdown_metric.title, topdown_metric.formula, ", ".join(sorted(topdown_metric.metrics))]
            table.add_row(*table_row)
        if table is not None:
            console.print(table)

    def list_groups(self) -> None:
        """Writes information about groups to terminal with rich tables
        """
        assert self.conf.cmn_list_groups is not None

        console = get_console()

        columns = ["Group", "Title"]
        if self.conf.descriptions:
            columns.append("Description")
        columns.append("Metrics")

        devices = self.conf.cmn_list_groups if len(self.conf.cmn_list_groups) > 0 else None

        # Node Groups
        for device_id, device in self.database.get_devices(DeviceType.NODE, devices).items():
            table = Table(title=f"{device} Groups")
            for column in columns:
                table.add_column(column)
            for group in self.database.get_json_groups(DeviceType.NODE, device_id):
                table_row = [group.name, group.title]
                if self.conf.descriptions:
                    table_row.append(group.description)
                table_row.append(", ".join(sorted(group.metrics)))
                table.add_row(*table_row)
            if table is not None:
                console.print(table)

        # Port Groups
        for device_id, device in self.database.get_devices(DeviceType.PORT, devices).items():
            table = Table(title=f"{device} Groups")
            for column in columns:
                table.add_column(column)
            for group in self.database.get_json_groups(DeviceType.PORT, device_id):
                table_row = [group.name, group.title]
                if self.conf.descriptions:
                    table_row.append(group.description)
                table_row.append(", ".join(sorted(group.metrics)))
                table.add_row(*table_row)
            if table is not None:
                console.print(table)

        # Topdown Groups
        topdown_columns = ["Group", "Title", "Metrics"]
        table = Table(title="Topdown Groups")
        for column in topdown_columns:
            table.add_column(column)
        for topdown_group in self.database.get_json_topdown_groups():
            table_row = [topdown_group.name, topdown_group.title, ", ".join(sorted(topdown_group.metrics))]
            table.add_row(*table_row)
        if table is not None:
            console.print(table)

    # pylint: disable=comparison-with-itself
    def print_topdown_metrics_with_values(self) -> None:
        """Writes topdown metrics results to terminal with rich tables
        """
        console = get_console()
        header_row_prefix = ["Metric"]
        if self.conf.descriptions:
            header_row_prefix.append("Description")
        for cmn_index in self.used_cmns:
            console.print(Header(f"Topdown Metrics CMN-{self.version} at index {cmn_index}", 1))
            for group_name in self.group_data:
                rows: List[Tuple[str, str, str, str]] = []
                group = self.group_data[group_name]["topdown_metrics_values"]
                for metric_name, metric in group.items():
                    if self.topdown_metrics_to_collect[group_name][metric_name]:
                        try:
                            value = metric[CmnLocation(cmn_index=cmn_index)]
                        except KeyError:
                            continue
                        rows.append(
                            (
                                self.topdown_metrics_details[metric_name].title,
                                self.topdown_metrics_details[metric_name].description,
                                str(value) if value is not None and value == value else "❌",
                                self.topdown_metrics_details[metric_name].units,
                            )
                        )
                # Topdown group may contain base metrics directly
                if group_name in self.collectable_base_metrics_for_topdown_groups:
                    for metric_name in self.collectable_base_metrics_for_topdown_groups[group_name]:
                        value = self.group_data[group_name]["metrics_values"][metric_name][CmnLocation(cmn_index=cmn_index)]
                        rows.append(
                            (
                                self.metrics_details[metric_name].title,
                                self.metrics_details[metric_name].description,
                                str(value) if value is not None and value == value else "❌",
                                self.metrics_details[metric_name].units,
                            )
                        )
                if rows:
                    table = SplitTable(
                        header_row_prefix, ["Value"], ["Units"], self.database.get_topdown_group_title(group_name)
                    )
                    for row in sorted(rows, key=lambda row: row[0].lower()):
                        row_prefix = [row[0], row[1]] if self.conf.descriptions else [row[0]]
                        table.add_row(row_prefix, [row[2]], [row[3]])
                    console.print(table)

    # pylint: disable=too-many-boolean-expressions, cell-var-from-loop
    def print_regular_metrics_with_values(self) -> None:
        """Writes metrics results to terminal with rich tables
        """
        console = get_console()

        for cmn_index in self.used_cmns:
            console.print(Header(f"Regular Metrics CMN-{self.version} at index {cmn_index}", 1))
            for group_name in self.group_data:
                if self.group_data[group_name]["topdown_group"]:
                    continue
                group = self.group_data[group_name]["metrics_values"]
                # Node Locations
                node_locations: Set[NodeLocation] = set()
                for metric in group.values():
                    for location in metric:
                        if isinstance(location, NodeLocation) and location.cmn_index == cmn_index:
                            node_locations.add(location)
                node_locations_sorted: List[NodeLocation] = sorted(
                    node_locations, key=lambda location: location.node_id
                )
                # Port Locations
                port_locations: Set[PortLocation] = set()
                for metric in group.values():
                    for location in metric:
                        if isinstance(location, PortLocation) and location.cmn_index == cmn_index:
                            port_locations.add(location)
                port_locations_sorted: List[PortLocation] = sorted(
                    port_locations, key=lambda location: location.xp_id + location.port
                )
                # XP Locations
                xp_locations: Set[XpLocation] = set()
                for metric in group.values():
                    for location in metric:
                        if isinstance(location, XpLocation) and location.cmn_index == cmn_index:
                            xp_locations.add(location)
                xp_locations_sorted: List[XpLocation] = sorted(
                    xp_locations, key=lambda location: location.xp_id
                )

                # Iterators
                node_iterator = iter(node_locations_sorted)
                port_iterator = iter(port_locations_sorted)
                xp_iterator = iter(xp_locations_sorted)

                # Initial values
                node_location = next(node_iterator, None)
                port_location = next(port_iterator, None)
                xp_location = next(xp_iterator, None)

                columns: List[Set[Location]] = [{CmnLocation(cmn_index=cmn_index)}]

                # Merge locations
                while (
                    node_location is not None
                    or port_location is not None
                    or xp_location is not None
                ):
                    new_node_location = node_location
                    new_port_location = port_location
                    new_xp_location = xp_location

                    column: Set[Location] = set()
                    if (
                        xp_location is not None
                        and (node_location is None or xp_location.xp_id <= node_location.xp_id)
                        and (port_location is None or xp_location.xp_id <= port_location.xp_id)
                    ):
                        column.add(xp_location)
                        new_xp_location = next(xp_iterator, None)
                    if (
                        port_location is not None
                        and (xp_location is None or port_location.xp_id <= xp_location.xp_id)
                        and (
                            node_location is None
                            or port_location.xp_id <= node_location.xp_id
                            and port_location.port <= node_location.port
                        )
                    ):
                        column.add(port_location)
                        new_port_location = next(port_iterator, None)
                    if (
                        node_location is not None
                        and (xp_location is None or node_location.xp_id <= xp_location.xp_id)
                        and (
                            port_location is None
                            or node_location.xp_id <= port_location.xp_id
                            and node_location.port <= port_location.port
                        )
                    ):
                        column.add(node_location)
                        new_node_location = next(node_iterator, None)
                    columns.append(column)

                    node_location = new_node_location
                    port_location = new_port_location
                    xp_location = new_xp_location

                mapping: Dict[Location, int] = {}

                for index, locations in enumerate(columns):
                    for location in locations:
                        mapping[location] = index

                # Table Header Prefix
                header_row_prefix = ["Metric"]
                if self.conf.descriptions:
                    header_row_prefix.append("Description")
                if self.conf.show_sample_events:
                    header_row_prefix.append("Sample events")

                # Table Header Locations
                label_priority = {
                    CmnLocation: 1,
                    NodeLocation: 2,
                    PortLocation: 3,
                    XpLocation: 4,
                }
                sorted_columns: List[List[Location]] = [
                    sorted(locations, key=lambda location: label_priority[type(location)])
                    for locations in columns
                ]
                header_row_data = [str(label[0]) for label in sorted_columns]

                # Table Header Suffix
                header_row_suffix = ["Units"]

                table = SplitTable(
                    header_row_prefix,
                    header_row_data,
                    header_row_suffix,
                    self.database.get_group_title(group_name),
                )
                for metric_name, metric in self.group_data[group_name]["metrics_values"].items():
                    # Data
                    row_data = ["❌"] * len(header_row_data)
                    for location, value in metric.items():
                        if location.cmn_index == cmn_index and value is not None and value == value:
                            row_data[mapping[location]] = str(value)
                    # Prefix
                    metric_details = self.metrics_details[metric_name]
                    row_prefix = [metric_details.title]
                    if self.conf.descriptions:
                        row_prefix.append(metric_details.description)
                    if self.conf.show_sample_events:
                        row_prefix.append(", ".join(sorted(metric_details.sample_events)))
                    # Suffix
                    row_suffix = [metric_details.units]
                    # Append Row
                    table.add_row(row_prefix, row_data, row_suffix)

                console.print(table)

    # pylint: disable=line-too-long
    def write_topdown_metrics_to_csv(self) -> None:
        """Writes topdown metrics results to CSV
        """
        assert self.base_csv_dir is not None and self.conf.cmn_generate_metrics_csv

        cmn_dir = os.path.join(self.base_csv_dir, "cmn")

        os.makedirs(cmn_dir, 0o755, True)

        for cmn_index in self.used_cmns:
            with open(
                os.path.join(
                    cmn_dir,
                    f"experimental_topdown_cmn_{self.version.lower().replace(' ', '_')}_index_{str(cmn_index)}_metrics.csv",
                ),
                "w",
                encoding="utf-8",
                newline="",
            ) as csv_file:
                csv_writer = csv.writer(csv_file)
                # Header row
                csv_writer.writerow(["Group", "Metric", "Value", "Units"])
                # Data rows
                for group_name in self.group_data:
                    group = self.group_data[group_name]["topdown_metrics_values"]
                    for metric_name in sorted(group.keys()):
                        metric = group[metric_name]
                        if self.topdown_metrics_to_collect[group_name][metric_name]:
                            try:
                                value = metric[CmnLocation(cmn_index=cmn_index)]
                            except KeyError:
                                continue
                            csv_writer.writerow([group_name, metric_name, value, self.topdown_metrics_details[metric_name].units])
                    # Topdown group may contain base metrics directly
                    if group_name in self.collectable_base_metrics_for_topdown_groups:
                        for metric_name in self.collectable_base_metrics_for_topdown_groups[group_name]:
                            value = self.group_data[group_name]["metrics_values"][metric_name][CmnLocation(cmn_index=cmn_index)]
                            csv_writer.writerow([group_name, metric_name, value, self.metrics_details[metric_name].units])

    def write_regular_metrics_to_csv(self) -> None:
        """Writes metrics results to CSV
        """
        assert self.base_csv_dir is not None and self.conf.cmn_generate_metrics_csv

        cmn_dir = os.path.join(self.base_csv_dir, "cmn")

        os.makedirs(cmn_dir, 0o755, True)

        for cmn_index in self.used_cmns:
            with open(
                os.path.join(
                    cmn_dir,
                    f"cmn_{self.version.lower().replace(' ', '_')}_index_{str(cmn_index)}_metrics.csv",
                ),
                "w",
                encoding="utf-8",
                newline="",
            ) as csv_file:
                csv_writer = csv.writer(csv_file)
                # Header row
                csv_writer.writerow(
                    [
                        "run",
                        "time",
                        "level",
                        "stage",
                        "group",
                        "metric",
                        "node",
                        "nodeid",
                        "value",
                        "interrupted",
                        "units",
                    ]
                )
                # Data rows
                for group_name in self.group_data:
                    if self.group_data[group_name]["topdown_group"]:
                        continue
                    groups = self.group_data[group_name]["metrics_values"]
                    for metric_name, metric in groups.items():
                        for location, value in metric.items():
                            if location.cmn_index == cmn_index:
                                node_id: Optional[int]
                                if isinstance(location, NodeLocation):
                                    node_id = location.node_id
                                elif isinstance(location, PortLocation):
                                    node_id = self.database.get_node_id_of_port(location)
                                elif isinstance(location, XpLocation):
                                    node_id = location.xp_id
                                elif isinstance(location, CmnLocation):
                                    node_id = None
                                csv_writer.writerow(
                                    (
                                        1,
                                        None,
                                        None,
                                        None,
                                        group_name,
                                        metric_name,
                                        str(location),
                                        node_id,
                                        value,
                                        None,
                                        self.metrics_details[metric_name].units,
                                    )
                                )
