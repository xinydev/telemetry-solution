# SPDX-License-Identifier: Apache-2.0

"""Remote Linux implementation of the perf runner relying on devlib targets."""

import logging
import shlex
from importlib.resources import as_file, files
from pathlib import Path
from signal import SIGINT
from typing import Any, Dict, Optional, Sequence, Set

from topdown_tool.common import remote_target_manager
from topdown_tool.common.devlib_types import Target
from topdown_tool.common.remote_utils import remote_path_exists
from topdown_tool.perf.linux_perf_base import LinuxPerfBase
from topdown_tool.perf.perf import Perf, PerfEventGroup


class RemoteLinuxPerf(LinuxPerfBase):
    _PROVISIONED_HELPERS: Dict[int, Set[str]] = {}

    """Execute ``perf stat`` on a remote Linux/Android device via devlib."""

    class _Recorder(LinuxPerfBase._Recorder):  # pylint: disable=protected-access
        """Recorder implementation that executes perf on a remote target."""

        # pylint: disable=too-many-arguments, too-many-positional-arguments
        def __init__(
            self,
            events: Sequence[PerfEventGroup],
            *,
            target: "Target",
            cli_filename: Path,
            output_filename: str,
            cores: Optional[Sequence[int]],
            perf_args: Optional[str],
            interval: Optional[int],
            pid: Optional[int],
            timeout: Optional[int],
        ) -> None:
            """Initialise the remote recorder.

            Args:
                events: Event groups scheduled for the recorder.
                target: Connected remote target that executes perf commands.
                cli_filename: Path where the recorder command line should be written locally.
                output_filename: Local file that will eventually contain perf statistics.
                cores: Optional list of CPUs to monitor (forwarded to ``perf -C``).
                perf_args: Additional perf command-line arguments to append.
                interval: Sampling interval supplied to ``perf -I`` when provided.
                pid: Optional process ID to profile via ``perf -p``.
            """
            super().__init__(events=events, output_filename=output_filename)
            self._cli_filename = cli_filename
            self._cores = cores
            self._perf_args = perf_args
            self._interval = interval
            self._pid = pid
            self._timeout = timeout
            self._target = target
            self._perf_path = RemoteLinuxPerf._perf_path
            self._remote_output_filename = self._compute_remote_output_filename(target)
            self._control_fifo = f"{self._remote_output_filename}.ctl"
            self._ack_fifo = f"{self._remote_output_filename}.ack"
            self._control_enabled = False
            self._process = None
            self._pull_success = True

            base_command = LinuxPerfBase._compose_stat_command(
                self._perf_path,
                self._remote_output_filename,
                cores=self._cores,
                pid=self._pid,
                interval=self._interval,
                timeout=self._timeout,
            )
            if self._perf_args:
                base_command += shlex.split(self._perf_args)
            self._base_command = base_command
            self._command = list(self._base_command)

            LinuxPerfBase._initialize_output_file(self._output_filename)

        @property
        def remote_output_filename(self) -> str:
            """Remote file path used to store perf statistics."""
            return self._remote_output_filename

        def _cmd_str(self) -> str:
            """Return the fully quoted command string."""
            return " ".join(shlex.quote(arg) for arg in self._command)

        def _compute_remote_output_filename(self, target: "Target") -> str:
            """Ensure the remote output directory exists and return the file path."""
            try:
                remote_dir = RemoteLinuxPerf.remote_workdir(target)
                target.execute(
                    f"mkdir -p {shlex.quote(remote_dir)}",
                    check_exit_code=False,
                    as_root=True,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                remote_dir = "/data/local/tmp"
            return f"{remote_dir}/{Path(self._output_filename).name}"

        def start(self) -> None:
            """Launch the remote perf process."""
            if self._events is None:
                logging.info("Empty run with no events")
                return

            try:
                control_args = self._prepare_control_args()
                self._command = list(self._base_command)
                self._command.extend(["-e", Perf.build_event_string(self._events)])
                if control_args:
                    self._command.extend(control_args)
                self._target.execute(
                    f"sh -lc '> {shlex.quote(self._remote_output_filename)}'",
                    check_exit_code=False,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

            RemoteLinuxPerf.write_cli_command(self._cli_filename, self._command)
            cmd_str = "devlib-signal-target " + self._cmd_str()
            try:
                self._process = self._target.background(cmd_str)
                logging.info("Remote perf started: %s", cmd_str)
                if self._control_enabled:
                    self._send_enable_command()
                    self._wait_for_enable_ack()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.error("Failed to start remote perf: %s", exc)
                self._cleanup_control_pipes()

        def stop(self) -> None:
            """Send ``SIGINT`` to stop the remote perf process."""
            if self._events is None or self._process is None:
                return
            try:
                self._process.send_signal(SIGINT)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning("Failed to send SIGINT: %s", exc)

        def wait(self) -> None:
            """Wait for perf to exit and synchronise the output locally."""
            if self._events is None or self._process is None:
                return
            self._process.wait()
            self._process = None
            try:
                self._target.execute(
                    f"sh -lc 'sync {shlex.quote(self._remote_output_filename)}'",
                    check_exit_code=False,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._pull_success = self._pull_output()
            self._cleanup_control_pipes()

        def prepare_output(self) -> bool:
            if not self._pull_success:
                logging.warning(
                    "Skipping recorder due to failed remote pull for %s",
                    getattr(self, "remote_output_filename", "<unknown>"),
                )
            return self._pull_success

        # Pipe control methods
        def _prepare_control_args(self) -> Sequence[str]:
            if self._events is None:
                return []
            if not self._create_control_pipes():
                return []
            self._control_enabled = True
            return [
                "--delay",
                "-1",
                "--control",
                f"fifo:{self._control_fifo},{self._ack_fifo}",
            ]

        def _create_control_pipes(self) -> bool:
            created_all = True
            for fifo in (self._control_fifo, self._ack_fifo):
                quoted = shlex.quote(fifo)
                try:
                    self._target.execute(
                        f"rm -f {quoted}",
                        check_exit_code=False,
                        as_root=True,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                try:
                    self._target.execute(
                        f"mkfifo -m 600 {quoted}",
                        check_exit_code=False,
                        as_root=True,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logging.warning("Failed to create control pipe %s: %s", fifo, exc)
                    created_all = False
                    break
            if not created_all:
                self._cleanup_control_pipes()
            return created_all

        def _send_enable_command(self) -> None:
            if not self._control_enabled:
                return
            try:
                self._target.execute(
                    f"sh -lc 'printf enable > {shlex.quote(self._control_fifo)}'",
                    check_exit_code=False,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning("Failed to send enable command to remote perf: %s", exc)

        def _wait_for_enable_ack(self) -> None:
            if not self._control_enabled:
                return
            try:
                self._target.execute(
                    f"sh -lc 'dd if={shlex.quote(self._ack_fifo)} bs=1 count=4 status=none'",
                    check_exit_code=False,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning("Failed to wait for remote perf acknowledgement: %s", exc)

        def _cleanup_control_pipes(self) -> None:
            if not self._control_enabled:
                return
            try:
                self._target.execute(
                    f"rm -f {shlex.quote(self._control_fifo)} {shlex.quote(self._ack_fifo)}",
                    check_exit_code=False,
                    as_root=True,
                )
            except Exception:  # pylint: disable=broad-exception-caught
                pass
            self._control_enabled = False

        def _pull_output(self) -> bool:
            if self._remote_output_filename == self._output_filename:
                return True
            try:
                self._target.pull(
                    self._remote_output_filename,
                    self._output_filename,
                )
                logging.debug("Pulled %s → %s", self._remote_output_filename, self._output_filename)
                return True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning("Failed to pull %s: %s", self._remote_output_filename, exc)
                return False

        # End of pipe control methods

    def __init__(
        self,
        *,
        perf_args: Optional[str] = None,
        interval: Optional[int] = None,
        target: Optional["Target"] = None,
    ) -> None:
        super().__init__(perf_args=perf_args, interval=interval)
        resolved_target = target or remote_target_manager.get_remote_target()
        if resolved_target is None:
            raise RuntimeError("RemoteLinuxPerf requires a configured devlib target")
        self._target: "Target" = resolved_target
        self._perf_on_target: Optional[str] = None

    def _before_start(self) -> None:
        self._perf_on_target = self._resolve_perf_on_target(self._target)
        RemoteLinuxPerf._perf_path = self._perf_on_target

    # pylint: disable=too-many-arguments
    def _recorder_kwargs(
        self,
        *,
        events: Sequence[PerfEventGroup],
        cli_path: Path,
        output_basename: str,
        pid: Optional[int],
        timeout: Optional[int],
    ) -> Dict[str, Any]:
        kwargs = super()._recorder_kwargs(
            events=events,
            cli_path=cli_path,
            output_basename=output_basename,
            pid=pid,
            timeout=timeout,
        )
        kwargs["target"] = self._target
        return kwargs

    @staticmethod
    def have_perf_privilege() -> bool:
        """Return whether the configured target appears to have perf privileges.

        Returns:
            bool: ``True`` if privilege checks succeed; otherwise ``False``.
        """
        target = remote_target_manager.get_remote_target()
        if target is None:
            logging.warning("RemoteLinuxPerf privilege check invoked without target")
            return True
        paranoid_value: Optional[str] = None
        status_value: Optional[str] = None

        try:
            paranoid = target.execute(
                "cat /proc/sys/kernel/perf_event_paranoid",
                check_exit_code=False,
            )
            paranoid_value = (
                paranoid.decode() if isinstance(paranoid, (bytes, bytearray)) else str(paranoid)
            )
        except Exception:  # pylint: disable=broad-exception-caught
            paranoid_value = None

        try:
            status = target.execute("cat /proc/self/status", check_exit_code=False)
            status_value = (
                status.decode() if isinstance(status, (bytes, bytearray)) else str(status)
            )
        except Exception:  # pylint: disable=broad-exception-caught
            status_value = None

        return LinuxPerfBase._has_privilege_from_values(paranoid_value, status_value)

    @classmethod
    def get_pmu_counters(cls, core: int) -> int:
        """Determine the number of concurrently measurable PMU counters on ``core``.

        Args:
            core: CPU core index queried on the remote target.

        Returns:
            int: Maximum number of events that can be scheduled concurrently.
        """
        target = remote_target_manager.get_remote_target()
        if target is None:
            raise RuntimeError("Remote PMU counter query requires a configured target")

        perf_path = cls._resolve_perf_on_target(target)

        def runner(event: str, sample_count: int) -> Sequence[str]:
            command = LinuxPerfBase._build_pmu_probe_command(
                perf_path,
                event,
                sample_count,
                core,
            )
            shell_cmd = " ".join(shlex.quote(arg) for arg in command) + " 2>&1"
            out = target.execute(
                shell_cmd,
                timeout=10,
                check_exit_code=False,
            )
            text = out.decode() if isinstance(out, (bytes, bytearray)) else str(out)
            return [line for line in text.splitlines() if line]

        def on_command_error(event: str, exc: Exception) -> None:
            logging.warning("Remote PMU probe failed with %s: %s", event, exc)

        def check_remote(count: int) -> bool:
            result = cls._probe_pmu_count(
                count=count,
                runner=runner,
                on_command_error=on_command_error,
                require_all_success=True,
            )
            if result is None:
                logging.warning(
                    "Remote PMU probe could not determine availability for %d events", count
                )
                return False
            return result

        pmu_max = LinuxPerfBase._binary_search_pmu_max(check_remote)
        logging.info("Detected %d PMU counters on core %d", pmu_max, core)
        return pmu_max

    @staticmethod
    def _command_exists(target: "Target", executable: str) -> bool:
        """Return ``True`` if ``command -v`` locates ``executable`` on the target."""
        try:
            target.execute(
                f"sh -lc 'command -v {shlex.quote(executable)} >/dev/null 2>&1'",
                check_exit_code=True,
                as_root=True,
            )
            return True
        except Exception:  # pylint: disable=broad-exception-caught
            return False

    @classmethod
    def is_remote_runnable(cls, target: "Target", perf_path: Optional[str]) -> bool:
        """Return whether the configured ``perf`` binary appears runnable on the target."""
        candidate = perf_path or cls._perf_path
        if not perf_path:
            try:
                candidate = cls._resolve_perf_on_target(target)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logging.warning("Failed to resolve perf on target: %s", exc)
                return False

        if cls._command_exists(target, candidate):
            return True

        logging.debug("Remote perf binary %s is not runnable on target", candidate)
        return False

    @staticmethod
    def remote_workdir(target: "Target") -> str:
        """Return the working directory used on the remote target.

        Args:
            target: devlib target object.

        Returns:
            str: Remote working directory path.
        """
        try:
            return target.get_workpath("topdown_perf")
        except Exception:  # pylint: disable=broad-exception-caught
            return getattr(target, "working_directory", "/data/local/tmp")

    @staticmethod
    def _exec_bin_dir_for_target(target: "Target") -> str:
        """Return the directory used to host perf binaries on the target."""
        if getattr(target, "os", None) == "android":
            return "/data/local/tmp/topdown_perf/bin"
        return f"{RemoteLinuxPerf.remote_workdir(target)}/bin"

    @classmethod
    def _ensure_devlib_signal_target(cls, target: "Target", perf_path: str) -> None:
        """Ensure ``devlib-signal-target`` exists alongside the chosen ``perf`` binary.

        Args:
            target: devlib target on which the helper should be available.
            perf_path: Path to the ``perf`` executable on the target.
        """

        helper_destinations: Set[Path] = {Path(perf_path).with_name("devlib-signal-target")}
        # `devlib-signal-target` is invoked both relative to the perf binary (via
        # devlib-signal-target <cmd>) and from devlib's canonical devlib-target/bin
        # directory.  BusyBox-based targets often lack a functional `which`,
        # so we cannot rely on PATH lookups.  Provision the helper in
        # all locations a caller may reference to ensure the recorder works without
        # additional host interaction or interactive setup.

        devlib_root: Optional[Path] = None
        for parent in Path(perf_path).parents:
            if parent.name == "devlib-target":
                devlib_root = parent
                break
        if devlib_root is None:
            workdir = getattr(target, "working_directory", None)
            if workdir:
                devlib_root = Path(workdir)

        if devlib_root is not None:
            helper_destinations.add(devlib_root / "bin" / "devlib-signal-target")

        if getattr(target, "os", None) == "android":
            helper_destinations.add(Path("/data/local/tmp/bin/devlib-signal-target"))

        try:
            # pylint: disable=import-outside-toplevel
            import devlib.bin  # type: ignore  # pylint: disable=import-error

            helper_resource = files(devlib.bin).joinpath("scripts/devlib-signal-target")
            cache = cls._PROVISIONED_HELPERS.setdefault(id(target), set())

            with as_file(helper_resource) as helper:
                for destination in helper_destinations:
                    dest_str = str(destination)
                    if dest_str in cache:
                        continue
                    if remote_path_exists(target, dest_str):
                        cache.add(dest_str)
                        continue
                    cls._install_binary(target, str(helper), dest_str)
                    cache.add(dest_str)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning(
                "Failed to provision devlib-signal-target helper on remote target: %s", exc
            )

    @classmethod
    def _resolve_perf_on_target(cls, target: "Target") -> str:
        """Locate or provision the ``perf`` binary on the remote target.

        Args:
            target: devlib target used for remote execution.

        Returns:
            str: Path to the ``perf`` binary on the remote target.
        """
        if cls._perf_path:
            candidate = cls._perf_path
            if not candidate.startswith("/"):
                candidate = f"{cls.remote_workdir(target)}/{candidate}"
            if remote_path_exists(target, candidate):
                cls._ensure_devlib_signal_target(target, candidate)
                return candidate

        bin_dir = cls._exec_bin_dir_for_target(target)

        # BusyBox shells often lack a reliable `which`, so probe known locations explicitly:
        #  1. the directory where we provisioned a perf binary for the target
        #  2. standard system locations on Linux/Android images
        for candidate in (
            f"{bin_dir}/perf",
            "/usr/bin/perf",
            "/bin/perf",
            "/system/bin/perf",
            "/system/xbin/perf",
        ):
            if remote_path_exists(target, candidate):
                cls._ensure_devlib_signal_target(target, candidate)
                return candidate  # type: ignore[return-value]

        try:
            # pylint: disable=import-outside-toplevel
            import devlib.bin  # type: ignore  # pylint: disable=import-error

            abi = getattr(target, "abi")
            binary = files(devlib.bin).joinpath(f"{abi}/perf")
            with as_file(binary) as host_path:
                try:
                    target.execute(
                        f"mkdir -p {shlex.quote(bin_dir)}",
                        check_exit_code=False,
                        as_root=True,
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                candidate = f"{bin_dir}/perf"
                cls._install_binary(target, str(host_path), candidate)
            cls._ensure_devlib_signal_target(target, candidate)
            return candidate
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logging.warning("Failed to provision perf on target: %s", exc)
            return "perf"

    @staticmethod
    def _install_binary(target: "Target", source: str, destination: str) -> None:
        """Push an executable to the target and ensure it is runnable.

        Args:
            target: devlib target receiving the binary.
            source: Path to the source binary on the host.
            destination: Destination path on the target.
        """

        parent = Path(destination).parent
        try:
            target.execute(
                f"mkdir -p {shlex.quote(str(parent))}",
                check_exit_code=False,
                as_root=True,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        target.push(source, destination, as_root=True)
        target.execute(
            f"chmod 755 {shlex.quote(destination)}",
            check_exit_code=False,
            as_root=True,
        )
