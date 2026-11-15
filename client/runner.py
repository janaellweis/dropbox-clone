from __future__ import annotations

import logging
from pathlib import Path
from time import monotonic

from client.api import DropboxClient
from client.core.exceptions import FileChangedDuringScanError, SyncConflictError
from client.detection.scanner import scan_folder, update_snapshot
from client.detection.tracker import ChangeTracker, PendingChanges
from client.detection.watcher import FileWatcher
from client.services.sync import sync_folder

logger = logging.getLogger(__name__)

FULL_SCAN_INTERVAL = 60.0
DEBOUNCE_INTERVAL = 0.5
MAX_DEBOUNCE_WAIT = 2.0


def collect_changes(tracker: ChangeTracker) -> PendingChanges:
    changed_paths: set[str] = set()
    full_rescan_required = False
    deadline = monotonic() + MAX_DEBOUNCE_WAIT

    while True:
        pending = tracker.take_pending()
        changed_paths.update(pending.paths)
        full_rescan_required |= pending.full_rescan_required

        debounce_timeout = min(
            DEBOUNCE_INTERVAL,
            max(0.0, deadline - monotonic()),
        )

        if debounce_timeout == 0 or not tracker.wait_for_changes(
            timeout=debounce_timeout
        ):
            break

    return PendingChanges(
        paths=frozenset(changed_paths),
        full_rescan_required=full_rescan_required,
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

    tracker = ChangeTracker()
    watcher = FileWatcher(root, tracker)

    local_snapshot = None
    next_remote_poll = monotonic()
    next_full_scan = monotonic()

    watcher.start()
    logger.info("Watching %s", root)

    try:
        while True:
            now = monotonic()
            full_scan_due = local_snapshot is None or now >= next_full_scan

            if full_scan_due:
                # Drain earlier notifications before scanning.
                # Events arriving during the scan remain pending.
                tracker.take_pending()
                try:
                    local_snapshot = scan_folder(str(root))
                except (FileChangedDuringScanError, OSError):
                    logger.exception("Full scan failed; will retry")
                    local_snapshot = None
                else:
                    next_full_scan = monotonic() + FULL_SCAN_INTERVAL

            if local_snapshot is not None:
                try:
                    local_snapshot = sync_folder(
                        api,
                        str(root),
                        local_snapshot,
                    )
                    logger.info("Sync completed")

                except SyncConflictError as exc:
                    logger.warning("Sync conflict: %s", exc)
                    local_snapshot = None

                except Exception:
                    logger.exception("Sync failed; will retry")
                    local_snapshot = None

            next_remote_poll = monotonic() + interval
            # An invalid snapshot already requires a full scan on the next
            # attempt; an expired scan deadline must not bypass the retry wait.
            deadline = (
                next_remote_poll
                if local_snapshot is None
                else min(next_remote_poll, next_full_scan)
            )
            timeout = max(0.0, deadline - monotonic())

            if not tracker.wait_for_changes(timeout=timeout):
                continue

            pending = collect_changes(tracker)

            if pending.full_rescan_required:
                local_snapshot = None
            elif local_snapshot is not None and pending.paths:
                try:
                    local_snapshot = update_snapshot(
                        root,
                        local_snapshot,
                        pending.paths,
                    )
                except Exception:
                    logger.exception("Targeted scan failed; requesting full scan")
                    local_snapshot = None

    except KeyboardInterrupt:
        logger.info("Stopping synchronization")
    finally:
        watcher.stop()
