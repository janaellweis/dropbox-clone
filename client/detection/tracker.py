from dataclasses import dataclass
from threading import Condition


@dataclass(frozen=True)
class PendingChanges:
    paths: frozenset[str]
    full_rescan_required: bool


class ChangeTracker:
    def __init__(self) -> None:
        self._condition = Condition()
        self._paths: set[str] = set()
        self._full_rescan_required = False

    def mark_changed(self, path: str) -> None:
        with self._condition:
            self._paths.add(path)
            self._condition.notify()

    def mark_moved(self, source: str, destination: str) -> None:
        with self._condition:
            self._paths.update((source, destination))
            self._condition.notify()

    def request_full_rescan(self) -> None:
        with self._condition:
            self._full_rescan_required = True
            self._condition.notify()

    def take_pending(self) -> PendingChanges:
        with self._condition:
            pending = PendingChanges(
                paths=frozenset(self._paths),
                full_rescan_required=self._full_rescan_required,
            )
            self._paths.clear()
            self._full_rescan_required = False
            return pending

    def wait_for_changes(self, timeout: float | None = None) -> bool:
        with self._condition:
            return self._condition.wait_for(
                lambda: bool(self._paths) or self._full_rescan_required,
                timeout=timeout,
            )
