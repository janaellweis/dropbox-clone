from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
from uuid6 import uuid7

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.core.types import SyncedFile, SyncState
from client.services.conflicts import (
    is_version_conflict,
    prepare_conflict_copy,
    resolve_version_conflict,
)
from client.services.executor import SyncExecutor
from client.services.planner import PlannedAction, SyncAction
from client.services.recovery import recover_pending_operations
from client.storage.filesystem import safe_local_path
from client.storage.snapshots import create_snapshot
from client.storage.state import StateStore

REMOTE_ACTIONS = {
    SyncAction.CREATE_REMOTE,
    SyncAction.UPDATE_REMOTE,
    SyncAction.DELETE_REMOTE,
    SyncAction.RENAME_REMOTE,
}

LOCAL_ACTIONS = {
    SyncAction.DOWNLOAD,
    SyncAction.DELETE_LOCAL,
    SyncAction.RENAME_LOCAL,
    SyncAction.PRESERVE_LOCAL,
}

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


def _record_uploaded_file(
    store: StateStore, state: SyncState, operation: dict, result: dict
) -> None:
    path = operation["path"]
    old_paths = set()

    if operation["action"] == SyncAction.UPDATE_REMOTE.value:
        file_id = operation["file_id"]
        version = result["version"]

        if result["content_hash"] != operation["expected_hash"]:
            raise SyncConflictError(f"Unexpected UPDATE result for {path}")

        synced_entry = _find_synced_file(state, file_id)
        if synced_entry is None:
            raise SyncConflictError(f"Unknown synchronized file: {file_id}")

        old_paths.add(synced_entry[0])
    else:
        file_id = result["file_id"]
        version = 1

    store.complete_operation(
        state,
        operation["request_id"],
        local_deletes=old_paths,
        local_upserts={
            path: {
                "file_id": file_id,
                "version": version,
                "hash": operation["expected_hash"],
                "size": operation["size"],
            }
        },
        remote_upserts={file_id: {"path": path, "version": version}},
    )


def _record_deleted_file(
    store: StateStore, state: SyncState, operation: dict, result: dict
) -> None:
    path = operation["path"]
    file_id = operation["file_id"]

    if result.get("deleted") is not True:
        raise SyncConflictError(f"Unexpected DELETE result for {path}")

    synced_entry = _find_synced_file(state, file_id)

    store.complete_operation(
        state,
        operation["request_id"],
        local_deletes={synced_entry[0]} if synced_entry else set(),
        remote_deletes={file_id},
    )


def _record_renamed_file(
    store: StateStore, state: SyncState, operation: dict, result: dict
) -> None:
    path = operation["path"]
    file_id = operation["file_id"]
    target = operation["target_path"]

    if result["key"] != target:
        raise SyncConflictError(f"Unexpected RENAME result for {path}")

    synced_entry = _find_synced_file(state, file_id)
    if synced_entry is None:
        raise SyncConflictError(f"Unknown synchronized file: {file_id}")

    old_path, baseline = synced_entry
    remote = {"path": target, "version": baseline["version"]}

    store.complete_operation(
        state,
        operation["request_id"],
        local_deletes={old_path},
        local_upserts={target: baseline},
        remote_upserts={file_id: remote},
    )


def complete_remote_operation(
    root: Path, state: SyncState, operation: dict, result: dict, store: StateStore
) -> None:
    """Record the acknowledged remote mutation, then retire its durable intent."""
    match SyncAction(operation["action"]):
        case SyncAction.CREATE_REMOTE | SyncAction.UPDATE_REMOTE:
            _record_uploaded_file(store, state, operation, result)
        case SyncAction.DELETE_REMOTE:
            _record_deleted_file(store, state, operation, result)
        case SyncAction.RENAME_REMOTE:
            _record_renamed_file(store, state, operation, result)
        case _:
            raise SyncConflictError("Unsupported remote completion action")

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


def _prepare_operation(
    root: Path,
    state: SyncState,
    action: PlannedAction,
    api: DropboxClient,
    store: StateStore,
) -> dict:
    request_id = str(uuid7())
    entry = _find_synced_file(state, action.file_id) if action.file_id else None
    baseline = entry[1] if entry else {}

    operation = {
        "request_id": request_id,
        "action": action.action.value,
        "path": action.path,
        "file_id": action.file_id,
        "target_path": action.target_path,
        "expected_version": None,
        "expected_hash": None,
        "snapshot_path": None,
        "size": None,
    }

    if action.action in REMOTE_ACTIONS:
        operation["expected_version"] = baseline.get("version")
    else:
        operation["expected_hash"] = baseline.get("hash")

    if action.action in UPLOAD_ACTIONS:
        snapshot, digest, size = create_snapshot(
            root, safe_local_path(root, action.path), UUID(request_id)
        )
        operation.update(
            snapshot_path=snapshot.relative_to(root).as_posix(),
            expected_hash=digest,
            size=size,
        )

    elif action.action == SyncAction.DOWNLOAD:
        operation["expected_version"] = state["remote_files"][action.file_id]["version"]
        _verify_download_metadata(action, api, operation)

    elif action.action == SyncAction.PRESERVE_LOCAL:
        prepare_conflict_copy(root, state, operation)

    store.add_pending_operation(state, operation)
    return operation


def _complete_local_operation(
    state: SyncState,
    operation: dict,
    result: dict,
    store: StateStore,
) -> None:
    """Persist the baseline after a successful local operation."""
    entry = _find_synced_file(state, operation["file_id"])

    upserts: dict[str, SyncedFile] = {}
    deletes: set[str] = set()

    if entry:
        deletes.add(entry[0])

    match SyncAction(operation["action"]):
        case SyncAction.DOWNLOAD:
            upserts[operation["path"]] = result

        case SyncAction.DELETE_LOCAL | SyncAction.PRESERVE_LOCAL:
            pass

        case SyncAction.RENAME_LOCAL:
            if entry:
                upserts[operation["target_path"]] = entry[1]

        case _:
            raise SyncConflictError("Unsupported local completion action")

    store.complete_operation(
        state,
        operation["request_id"],
        local_upserts=upserts,
        local_deletes=deletes,
    )


def recover_pending(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    store: StateStore,
) -> None:
    recover_pending_operations(
        root,
        state,
        executor,
        complete_remote_operation,
        _complete_local_operation,
        store,
    )


def run_action(
    root: Path,
    state: SyncState,
    executor: SyncExecutor,
    action: PlannedAction,
    api: DropboxClient,
    store: StateStore,
) -> dict | None:
    if action.action not in REMOTE_ACTIONS | LOCAL_ACTIONS:
        raise SyncConflictError(f"Unsupported synchronization action: {action.action}")

    operation = _prepare_operation(root, state, action, api, store)
    is_remote = action.action in REMOTE_ACTIONS

    prepared_action = PlannedAction(
        action.action,
        operation["path"],
        operation["file_id"],
        operation["target_path"],
    )

    snapshot = operation["snapshot_path"]

    try:
        result = executor.execute(
            prepared_action,
            expected_version=operation["expected_version"],
            expected_hash=operation["expected_hash"],
            request_id=operation["request_id"] if is_remote else None,
            snapshot_path=safe_local_path(root, snapshot) if snapshot else None,
            download_hash=operation.get("download_hash"),
            download_size=operation.get("download_size"),
        )
    except httpx.HTTPStatusError as exc:
        if action.action != SyncAction.UPDATE_REMOTE or not is_version_conflict(exc):
            raise
        return resolve_version_conflict(root, state, operation, api, store)

    if is_remote:
        complete_remote_operation(root, state, operation, result, store)
        return None

    _complete_local_operation(state, operation, result, store)
    return operation
