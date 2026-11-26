from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
from uuid6 import uuid7

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.services.conflicts import (
    is_version_conflict,
    prepare_conflict_copy,
    resolve_version_conflict,
)
from client.services.executor import SyncExecutor
from client.services.planner import PlannedAction, SyncAction
from client.services.recovery import recover_pending_operations
from client.storage.downloads import safe_local_path
from client.storage.snapshots import create_snapshot
from client.storage.state import RemoteFile, SyncedFile, SyncState, save_state

UPLOAD_ACTIONS = {
    SyncAction.CREATE_REMOTE,
    SyncAction.UPDATE_REMOTE,
}


def _find_synced_file(state: SyncState, file_id: str) -> tuple[str, SyncedFile] | None:
    return next(
        (
            (path, baseline)
            for path, baseline in state["files"].items()
            if baseline["file_id"] == file_id
        ),
        None,
    )


def _prepare_remote_operation(
    root: Path, state: SyncState, action: PlannedAction
) -> dict:
    request_id = str(uuid7())
    synced_entry = _find_synced_file(state, action.file_id) if action.file_id else None
    expected_version = synced_entry[1]["version"] if synced_entry else None
    operation = {
        "request_id": request_id,
        "action": action.action.value,
        "path": action.path,
        "file_id": action.file_id,
        "target_path": action.target_path,
        "expected_version": expected_version,
        "expected_hash": None,
        "snapshot_path": None,
        "size": None,
    }

    if action.action in UPLOAD_ACTIONS:
        snapshot, digest, size = create_snapshot(
            root, safe_local_path(root, action.path), UUID(request_id)
        )
        operation.update(
            snapshot_path=snapshot.relative_to(root).as_posix(),
            expected_hash=digest,
            size=size,
        )

    state["pending_operations"][request_id] = operation
    save_state(str(root), state)
    return operation


def _record_uploaded_file(state: SyncState, operation: dict, result: dict) -> None:
    path = operation["path"]
    file_id = result["file_id"]
    # A replay acknowledges the original CREATE, which always begins at version 1.
    state["files"][path] = {
        "file_id": file_id,
        "version": 1,
        "hash": operation["expected_hash"],
        "size": operation["size"],
    }
    state["remote_files"][file_id] = {"path": path, "version": 1}


def _record_updated_file(state: SyncState, operation: dict, result: dict) -> None:
    path = operation["path"]
    file_id = operation["file_id"]
    if result["content_hash"] != operation["expected_hash"]:
        raise SyncConflictError(f"Unexpected UPDATE result for {path}")

    synced_entry = _find_synced_file(state, file_id)
    if synced_entry is None:
        raise SyncConflictError(f"Unknown synchronized file: {file_id}")

    state["files"].pop(synced_entry[0])
    # The response describes the acknowledged version, not necessarily the
    # latest version after another client's concurrent mutation.
    state["files"][path] = {
        "file_id": file_id,
        "version": result["version"],
        "hash": operation["expected_hash"],
        "size": operation["size"],
    }
    state["remote_files"][file_id] = {"path": path, "version": result["version"]}


def _record_deleted_file(state: SyncState, operation: dict, result: dict) -> None:
    path = operation["path"]
    file_id = operation["file_id"]
    if result.get("deleted") is not True:
        raise SyncConflictError(f"Unexpected DELETE result for {path}")

    synced_entry = _find_synced_file(state, file_id)
    if synced_entry:
        state["files"].pop(synced_entry[0])
    state["remote_files"].pop(file_id, None)


def _record_renamed_file(state: SyncState, operation: dict, result: dict) -> None:
    path = operation["path"]
    file_id = operation["file_id"]
    target = operation["target_path"]
    if result["key"] != target:
        raise SyncConflictError(f"Unexpected RENAME result for {path}")

    synced_entry = _find_synced_file(state, file_id)
    if synced_entry is None:
        raise SyncConflictError(f"Unknown synchronized file: {file_id}")

    old_path, baseline = synced_entry
    state["files"].pop(old_path)
    state["files"][target] = baseline
    # RENAME does not increment the content version in this backend.
    state["remote_files"][file_id] = {"path": target, "version": baseline["version"]}


def complete_remote_operation(
    root: Path, state: SyncState, operation: dict, result: dict
) -> None:
    """Record the acknowledged remote mutation, then retire its durable intent."""
    match SyncAction(operation["action"]):
        case SyncAction.CREATE_REMOTE:
            _record_uploaded_file(state, operation, result)
        case SyncAction.UPDATE_REMOTE:
            _record_updated_file(state, operation, result)
        case SyncAction.DELETE_REMOTE:
            _record_deleted_file(state, operation, result)
        case SyncAction.RENAME_REMOTE:
            _record_renamed_file(state, operation, result)
        case _:
            raise SyncConflictError("Unsupported remote completion action")

    state["pending_operations"].pop(operation["request_id"])
    save_state(str(root), state)

    if operation["snapshot_path"]:
        safe_local_path(root, operation["snapshot_path"]).unlink(missing_ok=True)


def _verify_download_metadata(
    action: PlannedAction, api: DropboxClient, operation: dict
) -> None:
    """Pin the exact remote contents before recording a pending download."""
    metadata = api.get_file_metadata(action.file_id)
    if (
        metadata["file_id"] != action.file_id
        or metadata["version"] != operation["expected_version"]
        or metadata["key"] != action.path
    ):
        raise SyncConflictError(f"Remote changed before download: {action.path}")

    operation.update(
        download_hash=metadata["content_hash"], download_size=metadata["size"]
    )


def _prepare_local_operation(
    root: Path,
    state: SyncState,
    action: PlannedAction,
    remote: dict[str, RemoteFile],
    api: DropboxClient,
) -> dict:
    """Record a local operation before allowing filesystem changes."""
    synced_entry = _find_synced_file(state, action.file_id) if action.file_id else None
    operation = {
        "request_id": str(uuid7()),
        "action": action.action.value,
        "path": action.path,
        "file_id": action.file_id,
        "target_path": action.target_path,
        "expected_hash": synced_entry[1]["hash"] if synced_entry else None,
        "expected_version": remote[action.file_id]["version"]
        if action.action == SyncAction.DOWNLOAD
        else None,
    }

    if action.action == SyncAction.PRESERVE_LOCAL:
        prepare_conflict_copy(root, state, action, remote, operation)
    elif action.action == SyncAction.DOWNLOAD:
        _verify_download_metadata(action, api, operation)

    state["pending_operations"][operation["request_id"]] = operation
    save_state(str(root), state)
    return operation


def _remove_synced_file(state: SyncState, file_id: str) -> None:
    entry = _find_synced_file(state, file_id)
    if entry:
        state["files"].pop(entry[0])


def _complete_local_operation(
    root: Path,
    state: SyncState,
    action: PlannedAction,
    operation: dict,
    result: dict,
) -> None:
    """Update the baseline after a successful local operation."""
    match action.action:
        case SyncAction.DOWNLOAD:
            _remove_synced_file(state, action.file_id)
            state["files"][action.path] = result

        case SyncAction.DELETE_LOCAL | SyncAction.PRESERVE_LOCAL:
            _remove_synced_file(state, action.file_id)

        case SyncAction.RENAME_LOCAL:
            entry = _find_synced_file(state, action.file_id)
            if entry:
                state["files"].pop(entry[0])
                state["files"][action.target_path] = entry[1]

        case _:
            raise SyncConflictError("Unsupported local completion action")

    state["pending_operations"].pop(operation["request_id"])
    save_state(str(root), state)


def recover_pending(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
) -> None:
    recover_pending_operations(
        root,
        state,
        executor,
        complete_remote_operation,
        _complete_local_operation,
    )


def run_action(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    action: PlannedAction,
    api: DropboxClient,
) -> dict | None:
    """Recover pending operations, then execute the planned action."""
    recover_pending(root, state, executor)

    match action.action:
        case (
            SyncAction.CREATE_REMOTE
            | SyncAction.UPDATE_REMOTE
            | SyncAction.DELETE_REMOTE
            | SyncAction.RENAME_REMOTE
        ):
            return _run_remote_operation(root, state, executor, action, api)

        case (
            SyncAction.DOWNLOAD
            | SyncAction.DELETE_LOCAL
            | SyncAction.RENAME_LOCAL
            | SyncAction.PRESERVE_LOCAL
        ):
            return _run_local_operation(root, state, executor, action, api)

        case _:
            raise SyncConflictError(
                f"Unsupported synchronization action: {action.action}"
            )


def _run_remote_operation(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    action: PlannedAction,
    api: DropboxClient,
) -> dict | None:
    operation = _prepare_remote_operation(root, state, action)
    snapshot = operation["snapshot_path"]
    snapshot_path = safe_local_path(root, snapshot) if snapshot is not None else None

    try:
        result = executor.execute(
            action,
            expected_version=operation["expected_version"],
            expected_hash=operation["expected_hash"],
            request_id=operation["request_id"],
            snapshot_path=snapshot_path,
        )
    except httpx.HTTPStatusError as exc:
        if action.action != SyncAction.UPDATE_REMOTE or not is_version_conflict(exc):
            raise
        return resolve_version_conflict(root, state, operation, api)

    complete_remote_operation(root, state, operation, result)
    return None


def _run_local_operation(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    action: PlannedAction,
    api: DropboxClient,
) -> dict:
    operation = _prepare_local_operation(
        root, state, action, state["remote_files"], api
    )

    prepared_action = PlannedAction(
        action.action,
        operation["path"],
        operation["file_id"],
        operation["target_path"],
    )

    result = executor.execute(
        prepared_action,
        expected_version=operation["expected_version"],
        expected_hash=operation["expected_hash"],
        download_hash=operation.get("download_hash"),
        download_size=operation.get("download_size"),
    )

    _complete_local_operation(root, state, action, operation, result)
    return operation
