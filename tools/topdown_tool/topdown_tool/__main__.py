#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

import sys
import os
import os.path

if sys.version_info < (3, 9):
    print("Python 3.9 or later is required to run this script.", file=sys.stderr)
    sys.exit(1)

# Allow relative imports when running file/package directly (not as a module).
if __name__ == "__main__" and not __package__:
    __package__ = "topdown_tool"  # pylint: disable=redefined-builtin

    sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import logging

from typing import Iterable, List, Optional, Sequence, Set

from rich import get_console
import rich.traceback
from rich.console import Console
from topdown_tool.probe.probe import load_probe_factories
from topdown_tool.perf import perf_factory
from topdown_tool.probe import Probe, ProbeFactory
from topdown_tool.workload import CommandWorkload, PidWorkload, SystemwideWorkload

# Install rich pretty exception handler globally and show local variables in tracebacks
rich.traceback.install(show_locals=True)

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(module)s:%(funcName)s - %(message)s"


try:
    PROBE_FACTORY = load_probe_factories()
except Exception:  # pylint: disable=broad-exception-caught
    print("Failed to load probe entry points. Please install topdown_tool with pip")
    sys.exit(1)


def get_selected_factories_from_args(
    all_factories: Sequence[ProbeFactory],
    canonical_probe_names: Sequence[str],
    default_probe_names: Sequence[str],
    _args: Optional[Sequence[str]],
    console: Console,
) -> Sequence[ProbeFactory]:
    """
    Parse CLI arguments for probe selection and return the selected probe factories.

    Args:
        all_factories (Sequence[ProbeFactory]): All available ProbeFactory instances.
        canonical_probe_names (Sequence[str]): List of canonical probe names (from .name()).
        default_probe_names (Sequence[str]): List of probe names (from .name()) to use if --probe is not specified.
        _args (Optional[Sequence[str]]): Argument list to parse (typically sys.argv[1:]). If None, uses sys.argv.
        console: Optional rich console for printing errors/messages.

    Returns:
        list: List of selected ProbeFactory instances, in user-specified or default order.

    Raises:
        SystemExit: If no valid probes are selected, or user input is invalid.
    """
    available_probe_names = {pf.name().lower(): pf for pf in all_factories}

    # Step 1: Minimal parse for --probe
    minimal_parser = argparse.ArgumentParser(add_help=False)
    minimal_parser.add_argument(
        "--probe",
        action="append",
        metavar="NAME[,NAME...]",
        help=f"Select probes to enable (default: {', '.join(default_probe_names)}). NAME is one of: {', '.join(canonical_probe_names)}",
    )
    probe_args, _ = minimal_parser.parse_known_args(_args)

    # Step 2: Normalize/collect --probe args
    selected_names = []
    if probe_args.probe:
        for entry in probe_args.probe:
            selected_names.extend(
                [name.strip().lower() for name in entry.split(",") if name.strip()]
            )
    else:
        selected_names = [name.lower() for name in default_probe_names]
    selected_factories = []
    invalid_names = []
    for name in selected_names:
        pf = available_probe_names.get(name)
        if pf:
            selected_factories.append(pf)
        else:
            invalid_names.append(name)
    if not selected_factories:
        console.print(
            f"No valid probes selected. Please specify using --probe. "
            f"Valid options are: {', '.join(canonical_probe_names)}"
        )
    elif invalid_names:
        console.print(
            f"Unrecognized probe(s): {', '.join(invalid_names)}. "
            f"Valid options are: {', '.join(canonical_probe_names)}"
        )
        sys.exit(1)
    return selected_factories


def build_arg_parser(
    selected_factories: Iterable[ProbeFactory],
    canonical_probe_names: Iterable[str],
    default_probe_names: Iterable[str],
) -> argparse.ArgumentParser:
    """
    Build the application argument parser with global and selected probe-specific arguments.

    Args:
        selected_factories (Iterable[ProbeFactory]): Probe factories to add argument options from.
        canonical_probe_names (Iterable[str]): List of all canonical probe names for help.
        default_probe_names (Iterable[str]): List of default probe names, for setting argument default.

    Returns:
        argparse.ArgumentParser: App argument parser.
    """
    parser = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter)
    # Add --probe purely for help (so appears in usage synopsis)
    parser.add_argument(
        "--probe",
        action="append",
        metavar="NAME[,NAME...]",
        help=f"Select probes to enable (default: {', '.join(default_probe_names)}). NAME is one of: {', '.join(canonical_probe_names)}",
    )
    parser.add_argument(
        "command",
        default=[],
        nargs=argparse.REMAINDER,
        help='Command to analyse. Subsequent arguments are passed as program arguments. e.g. "sleep 10"',
    )

    # Extract pids from the --pid argument
    def pid_set(arg: str) -> Set[int]:
        return set(int(pid) for pid in arg.split(","))

    parser.add_argument(
        "--pid",
        "-p",
        type=pid_set,
        dest="pids",
        help="Comma separated list of process IDs to monitor.",
    )
    output_group = parser.add_argument_group("output options")
    logging_group = output_group.add_mutually_exclusive_group()
    logging_group.add_argument(
        "--verbose",
        "-v",
        action="store_const",
        dest="loglevel",
        const=logging.INFO,
        help="Enable verbose output",
    )
    logging_group.add_argument(
        "--debug",
        action="store_const",
        dest="loglevel",
        const=logging.DEBUG,
        help="Enable debug output",
    )
    output_group.add_argument("--events-csv", help="Output directory for events CSV data")

    # Add general perf options
    perf_arg_group = parser.add_argument_group("General perf capture options")
    perf_factory.add_cli_arguments(perf_arg_group)

    # Add Probe specific options for selected probes only
    for probe in selected_factories:
        arg_group = parser.add_argument_group(f"{probe.name()} capture options")
        probe.add_cli_arguments(arg_group)

    return parser


def capture_command_workload(probes: List[Probe], command: List[str]) -> None:
    """
    Run a command and capture the required telemetry data. The command is executed
    repeatedly until all probes report that they have captured the data they need.

    On each run:
      1. Identify which TelemetryElement instances still require data (via need_capture).
      2. Start capture on those probes, then launch the command process.
      3. Wait for the command to complete.
      4. Stop capture on each probe.

    If the command is interrupted (e.g. with Ctrl+C), the interruption is communicated to the probes.

    Args:
        probes: List of probes responsible for capturing telemetry data.
        command: The command to run, provided as a list of executable and arguments.
    """
    console = get_console()
    run = 0
    while need_capture := tuple(p for p in probes if p.need_capture()):
        run += 1
        running_probes = []
        interrupted_capture = False
        pid = None
        with CommandWorkload(command) as workload:
            try:
                for probe in need_capture:
                    probe.start_capture(run, workload.pid)
                    running_probes.append(probe)
                pid = workload.start().pop()
                if run == 1:
                    console.print(
                        "Monitoring command: "
                        + command[0].split("/" if sys.platform == "linux" else "\\")[-1]
                        + ". Hit Ctrl-C to stop."
                    )
                console.print(f"Run {run}")
                workload.wait()
            except Exception as e:
                interrupted_capture = True
                raise e
            finally:
                for probe in running_probes:
                    probe.stop_capture(run, pid, interrupted_capture)


def capture_systemwide_workload(
    probes: List[Probe],
) -> None:
    """
    Start system-wide telemetry capture until interrupted.

    This function starts capture on all enabled probes and waits until
    the user interrupts the process (e.g., with Ctrl+C). No specific workload is run.

    Args:
        probes: List of telemetry probes to activate.
    """
    running_probes = []
    run = 1
    with SystemwideWorkload() as workload:
        try:
            workload.start()
            for probe in probes:
                if probe.need_capture():
                    probe.start_capture(run)
                    running_probes.append(probe)
            if len(running_probes) == 0:
                return
            console = get_console()
            console.print(
                "Starting system-wide profiling. Hit Ctrl-C to stop. (See --help for usage information.)"
            )
            workload.wait()
        finally:
            for probe in running_probes:
                probe.stop_capture(run=1)


def capture_pid_workload(probes: List[Probe], pids: Set[int]) -> None:
    """
    Monitor a set of PIDs and capture telemetry data until all processes exit or the user interrupts.

    Starts capture on all enabled probes and monitors the given PIDs.
    Capture stops when either all PIDs have exited or the user interrupts with Ctrl+C.

    Args:
        probes: List of telemetry probes to activate.
        pids: Set of process IDs to monitor.
    """

    running_probes = []
    run = 1
    interrupted = False
    unique_pids = set()
    with PidWorkload(pids) as workload:
        try:
            unique_pids = workload.start()
            for probe in probes:
                if probe.need_capture():
                    probe.start_capture(run, unique_pids)
                    running_probes.append(probe)
            if len(running_probes) == 0:
                return
            console = get_console()
            console.print(
                "Monitoring PID"
                + ("s" if len(unique_pids) >= 2 else "")
                + ": "
                + ", ".join(map(str, sorted(unique_pids)))
                + ". Hit Ctrl-C to stop."
            )
            while unique_pids:
                pid = workload.wait()
                assert pid is not None
                for probe in running_probes:
                    probe.stop_capture(run, pid)
                unique_pids.discard(pid)
        except Exception as e:
            interrupted = True
            raise e
        finally:
            for pid in unique_pids:
                for probe in running_probes:
                    probe.stop_capture(run, pid, interrupted)


# pylint: disable=too-many-branches
def main(
    _args: Optional[Sequence[str]] = None,
) -> None:
    console = get_console()

    # Check for required perf privileges before doing anything
    if not perf_factory.have_perf_privilege():
        print(
            "Error: Insufficient privilege. This tool requires either perf_event_paranoid=-1, CAP_PERFMON, or CAP_SYS_ADMIN.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Get available probe types on the system
    available_factories = tuple(pt for pt in PROBE_FACTORY if pt.is_available())
    canonical_probe_names = [pf.name() for pf in available_factories]
    default_probe_names = ["CPU"]

    selected_factories = get_selected_factories_from_args(
        available_factories, canonical_probe_names, default_probe_names, _args, console
    )

    # Build full parser with only selected probe factories
    parser = build_arg_parser(selected_factories, canonical_probe_names, default_probe_names)
    args = parser.parse_args(_args)

    logging.basicConfig(format=LOG_FORMAT, level=args.loglevel)

    # Handle mutually exclusive arguments
    if args.command and args.pids:
        parser.error("Cannot specify a command and a PID")

    # Handle Perf specific arguments
    perf_factory.process_cli_arguments(args)

    # Variable to check if we proceed with real capture
    capture_data = True

    # Handle Probes global arguments

    factory = None
    # FIXME: Split between printing static information and creating instances
    try:
        for factory in selected_factories:
            # Probes detects if user wants to just query information, like listing metrics
            capture_data &= factory.process_cli_arguments(args)
    except Exception as e:  # pylint: disable=broad-exception-caught
        if factory:
            logging.warning("Failed processing of CLI arguments for %s", factory.name())
        console.print(e)
        console.print_exception()
        sys.exit(1)

    # Create probes for capture or querying information
    probes: List[Probe] = []
    try:
        for factory in selected_factories:
            probes.extend(factory.create(args, capture_data))
    except Exception as e:  # pylint: disable=broad-exception-caught
        if factory:
            logging.warning("Failed creation of the probe: %s", factory.name())
        console.print(e)
        console.print_exception()
        sys.exit(1)

    # Capture data depending on requested type
    try:
        if args.command:
            capture_command_workload(probes, args.command)
        elif args.pids:
            capture_pid_workload(probes, args.pids)
        else:
            capture_systemwide_workload(probes)
    except (InterruptedError,) as e:
        console.print(e)
    except Exception as e:  # pylint: disable=broad-exception-caught
        console.print(e)
        console.print_exception()
        sys.exit(1)

    # Print result for each probe
    for probe in probes:
        probe.output()


if __name__ == "__main__":
    main()
