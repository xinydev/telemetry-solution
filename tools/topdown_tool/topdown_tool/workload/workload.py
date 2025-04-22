import signal
from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Optional, Set, Type, TypeVar

T = TypeVar("T", bound="Workload")


class Workload(ABC):
    """
    Represents a workload and provides methods to manage its lifecycle,
    such as `start`, `wait`, and `kill`.

    This abstract base class installs signal handlers for SIGINT and SIGTERM, which
    raise an InterruptedError on user interruption (e.g., via Ctrl + C). It is designed
    to be used within a context manager (a "with" block) to ensure proper resource release.

    Derived classes must implement:
        - start: to start and initialize the workload.
        - wait: to block until the workload process completes.
        - kill: to terminate the running workload.
    """

    @staticmethod
    def signal_handler(signum: Any, frame: Any) -> None:
        raise InterruptedError("\rUser interrupt.")

    def __init__(self) -> None:
        if isinstance(self, Workload) and self.__class__ is Workload:
            raise NotImplementedError("Use derived class")

        # Setup signal handlers
        signal.signal(signal.SIGINT, Workload.signal_handler)
        signal.signal(signal.SIGTERM, Workload.signal_handler)

    def __enter__(self: T) -> T:
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_value: Optional[BaseException],
        traceback: Optional[TracebackType],
    ) -> None:
        return None

    @abstractmethod
    def start(self) -> Set[int]:
        """
        Starts workload
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def wait(self) -> Optional[int]:
        """
        Waits for workload process to complete
        """
        raise NotImplementedError("Use derived class")

    @abstractmethod
    def kill(self) -> None:
        """
        Kill running workload
        """
        raise NotImplementedError("Use derived class")
