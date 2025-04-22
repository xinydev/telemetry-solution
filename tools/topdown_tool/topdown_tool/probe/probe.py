# SPDX-License-Identifier: Apache-2.0
# Copyright 2025 Arm Limited

"""
Module defining the abstract classes for telemetry capture.

Classes:
    Probe: Abstract definition for a telemetry capture object. A Probe is responsible for capturing
      telemetry data, processing it, and outputting the results. A typical Probe implementation comprises:
        - A module to read its telemetry specification.
        - A module to expose the content of its specification in a structured fashion.
        - An event scheduler.
        - One or multiple renderers to format and output the captured data (for instance CSV, JSON, or CLI).
    ProbeFactory: Abstract definition for a probe factory. A ProbeFactory is used to define and register
      CLI arguments, process those arguments to decide whether the probe should capture data or only print info,
      and create one or more Probe instances. In general, a single ProbeFactory-derived class will create a single
      Probe-derived class. ProbeFactories must be exposed to the application via the entry point "topdown_tool.probe_factories".
      It is a good practice to prefix CLI arguments (e.g. the CPU probe factory uses arguments starting with '--cpu')
      to avoid clashes with unrelated probes.
"""

import argparse
from abc import ABC, abstractmethod
import importlib
import logging
from typing import List, Optional, Sequence, Set, Tuple, Union
from importlib.metadata import EntryPoint


class Probe(ABC):
    """Abstract base class for telemetry probes.

    A Probe object is responsible for capturing and processing telemetry data.
    Implementations typically incorporate components such as:
      - A telemetry specification reader.
      - Structured access to the telemetry specification.
      - An event scheduler to manage and dispatch events.
      - One or more renderers (CSV, JSON, CLI, etc.) to format and output the processed data.

    To use this interface, subclasses must implement the following methods:
      - start_capture: Begin capturing telemetry data.
      - stop_capture: End the capture session and process the data.
      - need_capture: Indicate whether more telemetry data needs to be captured.
      - output: Process and present the captured telemetry data.
    """

    def __init__(self) -> None:
        pass

    @abstractmethod
    def start_capture(self, run: int = 1, pids: Optional[Union[int, Set[int]]] = None) -> None:
        """Start telemetry capture for a given run.

        Args:
            run (int): The current run iteration (default 1).
            pids (Optional[Union[int, Set[int]]]): The process ID(s) to monitor, if applicable.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def stop_capture(
        self, run: int = 1, pid: Optional[int] = None, interrupted: bool = False
    ) -> None:
        """Stop telemetry capture for a given run.

        Args:
            run (int): The run iteration (default 1).
            pid (Optional[int]): The process ID for which capture is stopped.
            interrupted (bool): Whether the capture was interrupted.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def need_capture(self) -> bool:
        """Determine if additional telemetry data should be captured.

        Returns:
            bool: True if more data needs to be captured; False otherwise.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def output(self) -> None:
        """Process and output the captured telemetry data."""
        raise NotImplementedError("Use derived class")


class ProbeFactory(ABC):
    """Abstract base class for telemetry probe factories.

    A ProbeFactory is responsible for:
      - Defining CLI arguments specific to the probe.
      - Processing CLI arguments to determine if a Probe should actually capture data or simply print information.
      - Creating one or more instances of Probe.
      - Indicating whether the probe category is available on the current system.

    Note:
      - The method process_cli_arguments returns a boolean indicating if the probe should capture data.
      - To make a ProbeFactory visible to the application, it must be registered via the entry point "topdown_tool.probe_factories".
      - It is recommended to prefix registered CLI arguments to avoid conflicts; for example, the CPU probe factory's arguments are prefixed with '--cpu'.

    In practice, a derived ProbeFactory typically creates a single Probe-derived class.
    """

    def __init__(self) -> None:
        pass

    @abstractmethod
    def name(self) -> str:
        """Return the name of the probe.

        Returns:
            str: The name of the probe.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def is_available(self) -> bool:
        """Determine if the probe is available on the system.

        Returns:
            bool: True if the probe can capture telemetry data on the system; False otherwise.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def add_cli_arguments(self, argument_group: argparse._ArgumentGroup) -> None:
        """Register CLI arguments specific to the probe to the argument parser.

        Args:
            argument_group (argparse._ArgumentGroup): The parser group to register arguments to.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def process_cli_arguments(self, args: argparse.Namespace) -> bool:
        """Process CLI arguments and determine if the probe should capture data.

        This method may be used to decide whether the probe is only meant to display static
        information or actually capture telemetry data.

        Args:
            args (argparse.Namespace): CLI arguments provided by the user.

        Returns:
            bool: True if the probe should capture data; False if only static information is needed.
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def create(self, args: argparse.Namespace, capture_data: bool) -> Tuple["Probe", ...]:
        """Create probe instance(s) based on the CLI arguments and capture flag.

        Args:
            args (argparse.Namespace): The CLI arguments provided by the user.
            capture_data (bool): Indicates if the probe should capture telemetry data.

        Returns:
            Tuple[Probe, ...]: A tuple of Probe instances.
        """
        raise NotImplementedError("Use derived class")


def load_probe_factories() -> Sequence[ProbeFactory]:
    """Load ProbeFactory instances from registered entry points.

    This function retrieves all plugins registered under the "topdown_tool.probe_factories"
    entry point, attempts to load each plugin, and instantiates it if it is a subclass of ProbeFactory.
    Plugins that do not inherit from ProbeFactory or that fail to load are skipped.

    Returns:
        Sequence[ProbeFactory]: A sequence of ProbeFactory instances.
    """
    eps = importlib.metadata.entry_points()

    entries: Tuple[EntryPoint, ...]
    factory_classes: List[ProbeFactory] = []

    # For Python 3.10+, use the `select` method
    if hasattr(eps, "select"):
        entries = eps.select(group="topdown_tool.probe_factories")
    elif hasattr(eps, "get"):
        # For Python 3.9, entries are organized as a dict keyed by group
        entries = eps.get("topdown_tool.probe_factories", tuple())
    else:
        return factory_classes

    for ep in entries:
        try:
            factory_cls = ep.load()
            if issubclass(factory_cls, ProbeFactory):
                factory_classes.append(factory_cls())
            else:
                logging.warning(
                    "Class listed in %s %s do not inherit from ProbeFactory", ep.name, factory_cls
                )
        except Exception as e:  # pylint: disable=broad-exception-caught
            # handle mis-configured plugins gracefully
            logging.warning("Failed to load plugin %s: %s", ep.name, e)
    return factory_classes
