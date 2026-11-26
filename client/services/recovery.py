import os
from pathlib import Path
from typing import Callable

import httpx

from client.core.exceptions import SyncConflictError
from client.core.validation import is_sha256
from client.services.conflicts import (
    is_version_conflict,
    resolve_version_conflict,
)
from client.services.executor import SyncExecutor
from client.services.planner import PlannedAction, SyncAction
from client.storage.downloads import file_hash, safe_local_path
from client.storage.durability import (
    sync_directories,
    sync_existing_directories,
)
from client.storage.state import SyncState


def recover_pending_operations(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    finish_remote: Callable[[Path, SyncState, dict, dict], None],
    finish_local: Callable[[Path, SyncState, PlannedAction, dict, dict], None],
) -> None:
    """Recover interrupted operations using their durable intent."""
    for request_id, operation in list(state["pending_operations"].items()):
        path = operation.get("path", "<unknown>")

        try:
            action = SyncAction(operation["action"])
        except (KeyError, ValueError) as exc:
            raise SyncConflictError(f"Unsupported pending operation: {path}") from exc
        if operation.get("status") == "invalidated":
            raise SyncConflictError(f"Unresolved pending operations: {path}")

        if operation.get("request_id") != request_id:
            raise SyncConflictError(f"Pending request identity mismatch: {path}")

        if not isinstance(path, str) or not path:
            raise SyncConflictError("Pending operation has an invalid path")

        safe_local_path(root, path)

        match action:
            case SyncAction.DOWNLOAD:
                result = _recover_download(root, operation, executor)

            case SyncAction.DELETE_LOCAL:
                result = _recover_local_delete(root, operation, executor)

            case SyncAction.RENAME_LOCAL:
                result = _recover_local_rename(root, operation, executor)

            case SyncAction.PRESERVE_LOCAL:
                result = _recover_local_preservation(root, operation, executor)

            case SyncAction.CREATE_REMOTE | SyncAction.UPDATE_REMOTE:
                _recover_upload(root, state, operation, executor, finish_remote)
                continue

            case _:
                raise SyncConflictError(f"Unresolved pending operations: {path}")

        planned_action = PlannedAction(
            action,
            path,
            operation["file_id"],
            operation.get("target_path"),
        )
        finish_local(root, state, planned_action, operation, result)


def _recover_upload(
    root: Path,
    state: SyncState,
    operation: dict,
    executor: SyncExecutor,
    finish_remote: Callable[[Path, SyncState, dict, dict], None],
) -> None:
    action = SyncAction(operation["action"])
    path = operation["path"]
    request_id = operation["request_id"]

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
        return

    finish_remote(root, state, operation, result)


def _recover_download(
    root: Path,
    operation: dict,
    executor: SyncExecutor,
) -> dict:
    path = operation["path"]
    file_id = operation.get("file_id")
    version = operation.get("expected_version")
    original_hash = operation.get("expected_hash")
    target_hash = operation.get("download_hash")
    target_size = operation.get("download_size")

    if (
        not isinstance(file_id, str)
        or not file_id
        or type(version) is not int
        or version < 1
        or not is_sha256(target_hash)
        or type(target_size) is not int
        or target_size < 0
        or "expected_hash" not in operation
        or (original_hash is not None and not is_sha256(original_hash))
    ):
        raise SyncConflictError(f"Invalid pending download: {path}")

    destination = safe_local_path(root, path)

    if destination.is_symlink():
        raise SyncConflictError(f"Unsafe download destination: {path}")

    if (
        destination.is_file()
        and destination.stat().st_size == target_size
        and file_hash(destination) == target_hash
    ):
        with destination.open("rb") as file:
            os.fsync(file.fileno())

        sync_directories(destination.parent, root)

        return {
            "file_id": file_id,
            "version": version,
            "hash": target_hash,
            "size": target_size,
        }

    return executor.execute(
        PlannedAction(SyncAction.DOWNLOAD, path, file_id),
        expected_version=version,
        expected_hash=original_hash,
        download_hash=target_hash,
        download_size=target_size,
    )


def _recover_local_delete(
    root: Path,
    operation: dict,
    executor: SyncExecutor,
) -> dict:
    path = operation["path"]
    file_id = operation.get("file_id")
    expected_hash = operation.get("expected_hash")

    if not isinstance(file_id, str) or not file_id or not is_sha256(expected_hash):
        raise SyncConflictError(f"Invalid pending deletion: {path}")

    destination = safe_local_path(root, path)

    if destination.is_symlink():
        raise SyncConflictError(f"Unsafe delete destination: {path}")

    if not destination.exists():
        sync_existing_directories(destination.parent, root)
        return {}

    return executor.execute(
        PlannedAction(SyncAction.DELETE_LOCAL, path, file_id),
        expected_hash=expected_hash,
    )


def _recover_local_rename(
    root: Path,
    operation: dict,
    executor: SyncExecutor,
) -> dict:
    path = operation["path"]
    target = operation.get("target_path")
    file_id = operation.get("file_id")
    expected_hash = operation.get("expected_hash")

    if (
        not isinstance(file_id, str)
        or not file_id
        or not isinstance(target, str)
        or not target
        or not is_sha256(expected_hash)
    ):
        raise SyncConflictError(f"Invalid pending rename: {path}")

    source = safe_local_path(root, path)
    destination = safe_local_path(root, target)

    if source == destination:
        raise SyncConflictError(f"Invalid rename destination: {target}")

    if source.is_symlink() or destination.is_symlink():
        raise SyncConflictError("Cannot recover a symlink rename")

    if source.exists():
        return executor.execute(
            PlannedAction(SyncAction.RENAME_LOCAL, path, file_id, target),
            expected_hash=expected_hash,
        )

    if not destination.is_file() or file_hash(destination) != expected_hash:
        raise SyncConflictError(f"Unresolved pending rename: {path}")

    with destination.open("rb") as file:
        os.fsync(file.fileno())

    sync_directories(destination.parent, root)

    if source.parent != destination.parent:
        sync_existing_directories(source.parent, root)

    return {}


def _recover_local_preservation(
    root: Path,
    operation: dict,
    executor: SyncExecutor,
) -> dict:
    path = operation["path"]
    target = operation.get("target_path")
    file_id = operation.get("file_id")
    expected_hash = operation.get("expected_hash")
    size = operation.get("size")

    if (
        not isinstance(file_id, str)
        or not file_id
        or not isinstance(target, str)
        or not target
        or not is_sha256(expected_hash)
        or type(size) is not int
        or size < 0
    ):
        raise SyncConflictError(f"Invalid pending preservation: {path}")

    source = safe_local_path(root, path)
    destination = safe_local_path(root, target)

    if source == destination or source.parent != destination.parent:
        raise SyncConflictError(f"Invalid preservation destination: {target}")

    if source.is_symlink() or destination.is_symlink():
        raise SyncConflictError("Cannot preserve a symlink")

    candidate = source if source.exists() else destination

    if not candidate.is_file() or candidate.stat().st_size != size:
        raise SyncConflictError(f"Unresolved pending preservation: {path}")

    return executor.execute(
        PlannedAction(SyncAction.PRESERVE_LOCAL, path, file_id, target),
        expected_hash=expected_hash,
    )
