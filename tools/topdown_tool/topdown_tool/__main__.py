#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2022-2025 Arm Limited

import sys
import os
import os.path

if sys.version_info < (3, 10):
    print("Python 3.10 or later is required to run this script.", file=sys.stderr)
    sys.exit(1)

# Allow relative imports when running file/package directly (not as a module).
if __name__ == "__main__" and not __package__:
    __package__ = "topdown_tool"  # pylint: disable=redefined-builtin

    sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..")))

import argparse
import logging

from datetime import datetime
from typing import Iterable, List, Optional, Sequence, Set

from rich import get_console
import rich.traceback
from rich.console import Console
from rich.pretty import Pretty
from rich.table import Table
from topdown_tool.probe.probe import load_probe_factories
from topdown_tool.perf import perf_factory
from topdown_tool.probe import Probe, ProbeFactory
from topdown_tool.workload import CommandWorkload, PidWorkload, SystemwideWorkload
from topdown_tool.common import remote_target_manager
from topdown_tool.common.devlib_types import Target
from topdown_tool.version import as_dict as version_as_dict, get_build_info

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
        help=f"Select subsystems to profile (default: {', '.join(default_probe_names)}). List available probes with --probe-list",
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


class SmartFormatter(argparse.RawDescriptionHelpFormatter):
    """
    A formatter that preserves newlines only when the help string starts with 'R|'.
    Otherwise, it behaves like the default help formatter.
    """

    def _split_lines(self, text: str, width: int) -> List[str]:
        if text.startswith("R|"):
            return text[2:].splitlines()
        return super()._split_lines(text, width)


def create_base_arg_parser() -> argparse.ArgumentParser:
    """
    Build the application argument parser with perf arguments.

    Returns:
        argparse.ArgumentParser: App argument parser.
    """
    parser = argparse.ArgumentParser(
        formatter_class=SmartFormatter, add_help=False, allow_abbrev=False
    )

    # Add global remote target options before perf-specific ones so parsing works early
    remote_target_manager.add_cli_arguments(parser)

    # Add general perf options
    perf_factory.add_cli_arguments(parser)

    parser.add_argument(
        "--version",
        action="store_true",
        help="Print topdown-tool version information (use with --verbose for detailed build info) and exit.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show detailed build information when combined with --version.",
    )

    return parser


def extend_arg_parser(
    parser: argparse.ArgumentParser,
    selected_factories: Iterable[ProbeFactory],
    default_probe_names: Iterable[str],
) -> argparse.ArgumentParser:
    """
    Modify the application argument parser with global and selected probe-specific arguments.

    Args:
        selected_factories (Iterable[ProbeFactory]): Probe factories to add argument options from.
        canonical_probe_names (Iterable[str]): List of all canonical probe names for help.
        default_probe_names (Iterable[str]): List of default probe names, for setting argument default.

    Returns:
        argparse.ArgumentParser: App argument parser.
    """
    parser.add_argument(
        "-h",
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit",
    )

    # Add --probe purely for help (so appears in usage synopsis)
    parser.add_argument(
        "--probe",
        action="append",
        metavar="NAME[,NAME...]",
        help=f"Select subsystems to profile (default: {', '.join(default_probe_names)}). List available probes with --probe-list",
    )
    parser.add_argument(
        "--probe-list",
        action="store_true",
        help="List available probes on this system.",
    )
    parser.add_argument(
        "command",
        default=[],
        nargs=argparse.REMAINDER,
        help="Workload to profile (command and its arguments). Use -- to separate tool options from the workload command. Example: -- sleep 10",
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
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "--log-level",
        dest="loglevel",
        type=parse_log_level,
        default=logging.WARNING,
        metavar="LEVEL",
        help="Set logging level (CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET). Default: WARNING",
    )
    output_group.add_argument(
        "--detailed-exceptions",
        action="store_true",
        help="Enable detailed exception traceback output",
    )
    output_group.add_argument(
        "--csv-output-path",
        help="Directory for CSV output. Required when using --cpu-generate-csv (metrics and/or events). A timestamped subdirectory is created automatically.",
    )

    # Add Probe specific options for selected probes only
    for probe_factory in selected_factories:
        probe_factory.add_cli_arguments(parser)

    return parser


def parse_log_level(value: str) -> int:
    """
    Parse a case-insensitive logging level name into its numeric value.

    Accepted values: CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET.
    """
    name = value.strip().upper()
    mapping = {
        "CRITICAL": logging.CRITICAL,
        "ERROR": logging.ERROR,
        "WARNING": logging.WARNING,
        "INFO": logging.INFO,
        "DEBUG": logging.DEBUG,
        "NOTSET": logging.NOTSET,
    }
    if name in mapping:
        return mapping[name]
    raise argparse.ArgumentTypeError(
        "Invalid log level: "
        + value
        + ". Choose from CRITICAL, ERROR, WARNING, INFO, DEBUG, NOTSET"
    )


def print_available_probes_table(
    available_factories: Sequence[ProbeFactory], console: Console
) -> None:
    table = Table(title="Available Probes")
    table.add_column("Probe")
    table.add_column("Description")
    for pf in available_factories:
        table.add_row(pf.name(), pf.get_description())
    console.print(table)


def capture_command_workload(
    probes: List[Probe], command: List[str], target: Optional["Target"]
) -> None:
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
        with CommandWorkload(command, target=target) as workload:
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
    target: Optional["Target"],
) -> None:
    """
    Start system-wide telemetry capture until interrupted.

    This function starts capture on all enabled probes and waits until
    the user interrupts the process (e.g., with Ctrl+C). No specific workload is run.

    Args:
        probes: List of telemetry probes to activate.
    """
    running_probes: List[Probe] = []
    run = 1
    interrupted = False

    with SystemwideWorkload(target) as workload:
        try:
            workload.start()
            for probe in (p for p in probes if p.need_capture()):
                running_probes.append(probe)
                probe.start_capture(run)

            if not running_probes:
                return

            console = get_console()
            console.print(
                "Starting system-wide profiling. Hit Ctrl-C to stop. "
                "(See --help for usage information.)"
            )
            workload.wait()

        except InterruptedError:
            interrupted = True
            raise
        finally:
            for probe in running_probes:
                try:
                    probe.stop_capture(run, interrupted=interrupted)
                except Exception:  # pylint: disable=broad-exception-caught
                    logging.exception("Probe %r failed during stop_capture", probe)


def capture_pid_workload(probes: List[Probe], pids: Set[int], target: Optional["Target"]) -> None:
    """
    Monitor a set of PIDs and capture telemetry data until all processes exit or the user interrupts.

    Starts capture on all enabled probes and monitors the given PIDs.
    Capture stops when either all PIDs have exited or the user interrupts with Ctrl+C.

    Args:
        probes: List of telemetry probes to activate.
        pids: Set of process IDs to monitor.
    """

    running_probes: List[Probe] = []
    run = 1
    interrupted = False
    unique_pids = set()
    with PidWorkload(pids, target=target) as workload:
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
                    try:
                        probe.stop_capture(run, pid, interrupted)
                    except Exception:  # pylint: disable=broad-exception-caught
                        logging.exception(
                            "Failed while stopping capture for probe %r (pid=%s)", probe, pid
                        )


# pylint: disable=too-many-branches, too-many-statements, too-many-locals
def main(
    _args: Optional[Sequence[str]] = None,
) -> None:
    topdown_tool_start_time = datetime.now()

    console = get_console()

    # Build parser with only perf initially
    parser = create_base_arg_parser()

    # Parse known args (so we can identify selected probes before extending the parser)
    args, _ = parser.parse_known_args(_args)

    if args.version:
        if args.verbose:
            console.print(Pretty(version_as_dict(), expand_all=True))
        else:
            console.print(get_build_info().build_identifier)
        return

    # Remote targets need to be configured before privilege checks run.
    remote_target_manager.configure_from_args(args)
    perf_factory.configure_from_cli_arguments(args)

    # Get available probe types on the system
    available_factories = tuple(pt for pt in PROBE_FACTORY if pt.is_available())

    # Early handling of --probe-list (allow listing without perf privileges)
    list_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    list_parser.add_argument("--probe-list", action="store_true")
    early_args, _ = list_parser.parse_known_args(_args)
    if early_args.probe_list:
        print_available_probes_table(available_factories, console)
        return

    # Check for required perf privileges before doing anything else
    if not perf_factory.have_perf_privilege():
        print(
            "Error: Insufficient privilege. This tool requires either perf_event_paranoid=-1, CAP_PERFMON, or CAP_SYS_ADMIN.",
            file=sys.stderr,
        )
        sys.exit(1)

    canonical_probe_names = [pf.name() for pf in available_factories]
    default_probe_names = ["CPU"]

    selected_factories = get_selected_factories_from_args(
        available_factories, canonical_probe_names, default_probe_names, _args, console
    )

    # Extend parser with only selected probe factories
    extend_arg_parser(parser, selected_factories, default_probe_names)
    args = parser.parse_args(_args)
    # Normalize command separator: drop leading '--' if provided
    if args.command and len(args.command) > 0 and args.command[0] == "--":
        args.command = args.command[1:]

    logging.basicConfig(format=LOG_FORMAT, level=args.loglevel)

    # Handle mutually exclusive arguments
    if args.command and args.pids:
        parser.error("Cannot specify a command and a PID")

    # Handle Perf specific arguments
    perf_factory.configure_from_cli_arguments(args)

    if not perf_factory.is_perf_runnable():
        console.print(
            f"Error: The perf tool at {perf_factory.get_effective_perf_path()} is not runnable. Please check that the file exists and you have the necessary rights to run it."
        )
        sys.exit(1)

    # Variable to check if we proceed with real capture
    capture_data = True

    # Handle Probes global arguments

    factory = None

    def get_warning_text(factory: Optional[ProbeFactory]) -> str:
        return f"Failed processing of CLI arguments for {factory.name()}" if factory else ""

    def handle_exception(
        exception: Exception, log_warning_str: str, print_additional_error_str: bool
    ) -> None:
        if log_warning_str:
            logging.warning(log_warning_str)

        console.print(
            f"Internal error: {exception}\nPlease submit a bug report at https://gitlab.arm.com/telemetry-solution/telemetry-solution/-/issues with command line extended by the --detailed-exceptions option."
            if print_additional_error_str
            else str(exception)
        )
        if args.detailed_exceptions:
            console.print_exception()
        sys.exit(1)

    # FIXME: Split between printing static information and creating instances
    try:
        for factory in selected_factories:
            # Probes detect if user wants to just query information, like listing metrics
            capture_flag = factory.configure_from_cli_arguments(args)
            capture_data &= capture_flag
    except (FileNotFoundError, PermissionError, ValueError) as e:
        handle_exception(
            exception=e, log_warning_str=get_warning_text(factory), print_additional_error_str=False
        )
    except Exception as e:  # pylint: disable=broad-exception-caught
        handle_exception(
            exception=e, log_warning_str=get_warning_text(factory), print_additional_error_str=True
        )

    # Create directory for CSV output if needed
    base_csv_dir: Optional[str] = None
    if args.csv_output_path is not None:
        base_csv_dir = os.path.join(
            args.csv_output_path, topdown_tool_start_time.strftime("%Y_%m_%d_%H_%M_%S")
        )
        try:
            os.makedirs(base_csv_dir, 0o755, True)
        except Exception:  # pylint: disable=broad-exception-caught
            console.print(f"Failed to create base CSV path {base_csv_dir}")

    # Create probes for capture or querying information
    probes: List[Probe] = []
    try:
        for factory in selected_factories:
            probes.extend(factory.create(capture_data, base_csv_dir))
    except Exception as e:  # pylint: disable=broad-exception-caught
        handle_exception(
            exception=e, log_warning_str=get_warning_text(factory), print_additional_error_str=True
        )

    # Capture data depending on requested type
    try:
        remote_target = remote_target_manager.get_remote_target()
        if args.command:
            capture_command_workload(probes, args.command, remote_target)
        elif args.pids:
            capture_pid_workload(probes, args.pids, remote_target)
        else:
            capture_systemwide_workload(probes, remote_target)
    except (InterruptedError,) as e:
        console.print(e)
    except OSError as e:
        handle_exception(exception=e, log_warning_str="", print_additional_error_str=False)
    except Exception as e:  # pylint: disable=broad-exception-caught
        handle_exception(exception=e, log_warning_str="", print_additional_error_str=True)

    # Print result for each probe
    for probe in probes:
        probe.output()


if __name__ == "__main__":
    main()
