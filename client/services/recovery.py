from pathlib import Path
from typing import Callable

import httpx

from client.core.exceptions import SyncConflictError
from client.services.conflicts import (
    is_version_conflict,
    resolve_version_conflict,
)
from client.services.executor import SyncExecutor
from client.services.planner import PlannedAction, SyncAction
from client.storage.downloads import file_hash, safe_local_path
from client.storage.state import SyncState

UPLOAD_ACTIONS = {
    SyncAction.CREATE_REMOTE,
    SyncAction.UPDATE_REMOTE,
}


def recover_pending_uploads(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    finish_remote: Callable[[Path, SyncState, dict, dict], None],
) -> None:
    """Replay uncertain uploads using their original durable request."""
    for request_id, operation in list(state["pending_operations"].items()):
        path = operation.get("path", "<unknown>")

        try:
            action = SyncAction(operation["action"])
        except (KeyError, ValueError) as exc:
            raise SyncConflictError(f"Unsupported pending operation: {path}") from exc
        # Uploads can be retried using their saved snapshot and original request ID.
        # Local operations (download, rename, delete) may have partially completed
        # before a crash, so we stop rather than risk overwriting or deleting files.
        if action not in UPLOAD_ACTIONS or operation.get("status") == "invalidated":
            raise SyncConflictError(f"Unresolved pending operations: {path}")

        if operation.get("request_id") != request_id:
            raise SyncConflictError(f"Pending request identity mismatch: {path}")

        if not isinstance(path, str) or not path:
            raise SyncConflictError("Pending upload has an invalid path")

        safe_local_path(root, path)

        snapshot_name = operation.get("snapshot_path")
        if not isinstance(snapshot_name, str) or not snapshot_name:
            raise SyncConflictError(f"Pending upload has no snapshot: {path}")

        snapshot = safe_local_path(root, snapshot_name)
        expected_hash = operation.get("expected_hash")
        size = operation.get("size")

        if (
            snapshot.is_symlink()
            or not snapshot.is_file()
            or not isinstance(expected_hash, str)
            or type(size) is not int
            or size < 0
            or snapshot.stat().st_size != size
            or file_hash(snapshot) != expected_hash
        ):
            raise SyncConflictError(f"Pending upload snapshot is invalid: {path}")

        file_id = operation.get("file_id")
        expected_version = operation.get("expected_version")

        if action == SyncAction.CREATE_REMOTE:
            if file_id is not None or expected_version is not None:
                raise SyncConflictError(f"Invalid pending CREATE operation: {path}")
        elif (
            not isinstance(file_id, str)
            or not file_id
            or type(expected_version) is not int
            or expected_version < 1
        ):
            raise SyncConflictError(f"Invalid pending UPDATE operation: {path}")

        planned_action = PlannedAction(
            action,
            path,
            file_id,
            operation.get("target_path"),
        )

        try:
            result = executor.execute(
                planned_action,
                expected_version=expected_version,
                expected_hash=expected_hash,
                request_id=request_id,
                snapshot_path=snapshot,
            )
        except httpx.HTTPStatusError as exc:
            if action != SyncAction.UPDATE_REMOTE or not is_version_conflict(exc):
                raise

            resolve_version_conflict(root, state, operation, executor.api)
            continue

        finish_remote(root, state, operation, result)
