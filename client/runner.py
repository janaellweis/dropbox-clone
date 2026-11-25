from __future__ import annotations

import logging
import signal
from pathlib import Path
from threading import Event
from time import monotonic

import httpx

from client.api import DropboxClient
from client.core.exceptions import FileChangedDuringScanError, SyncConflictError
from client.detection.scanner import scan_folder, update_directories, update_snapshot
from client.detection.tracker import ChangeTracker, PendingChanges
from client.detection.watcher import FileWatcher
from client.services.sync import sync_folder

logger = logging.getLogger(__name__)

DEBOUNCE_INTERVAL = 0.5
MAX_DEBOUNCE_WAIT = 2.0


def collect_changes(tracker: ChangeTracker) -> PendingChanges:
    changed_paths: set[str] = set()
    directories: set[str] = set()
    deadline = monotonic() + MAX_DEBOUNCE_WAIT

    while True:
        pending = tracker.take_pending()
        changed_paths.update(pending.paths)
        directories.update(pending.directories)

        timeout = min(
            DEBOUNCE_INTERVAL,
            max(0.0, deadline - monotonic()),
        )

        if timeout == 0 or not tracker.wait_for_changes(timeout=timeout):
            break

    return PendingChanges(
        paths=frozenset(changed_paths),
        directories=frozenset(directories),
    )


def run(
    api: DropboxClient,
    folder: str | Path,
    interval: float = 5.0,
) -> None:
    if interval <= 0:
        raise ValueError("Sync interval must be positive")

    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)

    stopping = Event()
    tracker = ChangeTracker()

    def request_shutdown(signum, frame):
        stopping.set()
        tracker.stop()

    previous_handler = signal.signal(signal.SIGTERM, request_shutdown)
    watcher = FileWatcher(root, tracker)

    local_snapshot = None
    next_remote_poll = monotonic()

    try:
        watcher.start()
        logger.info("Watching %s", root)

        while not stopping.is_set():
            if local_snapshot is None:
                # Drain earlier notifications before scanning.
                # Events arriving during the scan remain pending.
                tracker.take_pending()

                try:
                    local_snapshot = scan_folder(str(root))
                except (FileChangedDuringScanError, OSError):
                    logger.exception("Full scan failed; will retry")

            if local_snapshot is not None:
                try:
                    local_snapshot = sync_folder(
                        api,
                        str(root),
                        local_snapshot,
                    )
                    logger.info("Sync completed")
                except SyncConflictError:
                    logger.exception("Sync stopped due to unresolved conflict")
                    raise
                except httpx.TransportError:
                    logger.warning("Network unavailable; retrying on next sync cycle")
                    local_snapshot = None
                except Exception:
                    logger.exception("Sync stopped after failure")
                    raise

            if stopping.is_set():
                break

            # Schedule the next poll independently of local events.
            now = monotonic()
            if now >= next_remote_poll:
                next_remote_poll = now + interval

            timeout = max(0.0, next_remote_poll - monotonic())

            if not tracker.wait_for_changes(timeout=timeout):
                continue

            if stopping.is_set():
                break

            pending = collect_changes(tracker)

            if local_snapshot is not None:
                try:
                    if pending.directories:
                        local_snapshot = update_directories(
                            root, local_snapshot, pending.directories
                        )
                    if pending.paths:
                        local_snapshot = update_snapshot(
                            root, local_snapshot, pending.paths
                        )
                except Exception:
                    logger.exception("Targeted scan failed; stopping")
                    raise

    except KeyboardInterrupt:
        logger.info("Stopping synchronization")
    finally:
        watcher.stop()
        signal.signal(signal.SIGTERM, previous_handler)
        logger.info("Synchronization stopped")
