from dataclasses import dataclass
from threading import Condition


@dataclass(frozen=True)
class PendingChanges:
    paths: frozenset[str]
    directories: frozenset[str]


class ChangeTracker:
    def __init__(self) -> None:
        self._condition = Condition()
        self._paths: set[str] = set()
        self._directories: set[str] = set()
        self._stopping = False

    def mark_changed(self, *paths: str) -> None:
        with self._condition:
            self._paths.update(paths)
            self._condition.notify()

    def mark_directory(self, *paths: str) -> None:
        with self._condition:
            self._directories.update(paths)
            self._condition.notify()

    def take_pending(self) -> PendingChanges:
        with self._condition:
            pending = PendingChanges(
                paths=frozenset(self._paths),
                directories=frozenset(self._directories),
            )
            self._paths.clear()
            self._directories.clear()
            return pending

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def wait_for_changes(self, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: self._stopping or bool(self._paths) or bool(self._directories),
                timeout=timeout,
            )
