from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
from uuid6 import uuid7

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.detection.changes import detect_local_renames
from client.detection.scanner import scan_file, scan_folder
from client.remote import fetch_remote_updates
from client.services.conflicts import conflicted_copy_path
from client.services.executor import SyncExecutor
from client.services.planner import PlannedAction, SyncAction, plan_sync
from client.storage.downloads import file_hash, safe_local_path
from client.storage.snapshots import create_snapshot
from client.storage.state import (
    RemoteFile,
    SyncedFile,
    SyncState,
    load_state,
    save_state,
)

REMOTE_ACTIONS = {
    SyncAction.CREATE_REMOTE,
    SyncAction.UPDATE_REMOTE,
    SyncAction.DELETE_REMOTE,
    SyncAction.RENAME_REMOTE,
}
UPLOAD_ACTIONS = {SyncAction.CREATE_REMOTE, SyncAction.UPDATE_REMOTE}
LOCAL_ACTIONS = {
    SyncAction.DOWNLOAD,
    SyncAction.DELETE_LOCAL,
    SyncAction.RENAME_LOCAL,
    SyncAction.PRESERVE_LOCAL,
}


def _save(root: Path, state: SyncState) -> None:
    save_state(str(root), state)


def _synced_file_by_id(state: SyncState, file_id: str) -> tuple[str, SyncedFile] | None:
    return next(
        (
            (path, baseline)
            for path, baseline in state["files"].items()
            if baseline["file_id"] == file_id
        ),
        None,
    )


def _prepare_remote(root: Path, state: SyncState, action: PlannedAction) -> dict:
    request_id = str(uuid7())
    synced_entry = _synced_file_by_id(state, action.file_id) if action.file_id else None
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
    _save(root, state)
    return operation


def _finish_remote(root: Path, state: SyncState, operation: dict, result: dict) -> None:
    kind = SyncAction(operation["action"])
    path = operation["path"]
    file_id = operation["file_id"]
    if kind == SyncAction.CREATE_REMOTE:
        file_id = result["file_id"]
        # Successful replay acknowledges this original request. CREATE starts
        # at version 1; current metadata may already reflect later mutations.
        state["files"][path] = {
            "file_id": file_id,
            "version": 1,
            "hash": operation["expected_hash"],
            "size": operation["size"],
        }
        state["remote_files"][file_id] = {"path": path, "version": 1}
    elif kind == SyncAction.UPDATE_REMOTE:
        # The UPDATE response refers to the committed version, even if a later
        # concurrent mutation has already changed the current metadata.
        if result["content_hash"] != operation["expected_hash"]:
            raise SyncConflictError(f"Unexpected UPDATE result for {path}")
        synced_entry = _synced_file_by_id(state, file_id)
        if synced_entry is None:
            raise SyncConflictError(f"Unknown synchronized file: {file_id}")
        old_path, _ = synced_entry
        state["files"].pop(old_path)
        state["files"][path] = {
            "file_id": file_id,
            "version": result["version"],
            "hash": operation["expected_hash"],
            "size": operation["size"],
        }
        state["remote_files"][file_id] = {"path": path, "version": result["version"]}
    elif kind == SyncAction.DELETE_REMOTE:
        if result.get("deleted") is not True:
            raise SyncConflictError(f"Unexpected DELETE result for {path}")
        synced_entry = _synced_file_by_id(state, file_id)
        if synced_entry:
            state["files"].pop(synced_entry[0])
        state["remote_files"].pop(file_id, None)
    elif kind == SyncAction.RENAME_REMOTE:
        if result["key"] != operation["target_path"]:
            raise SyncConflictError(f"Unexpected RENAME result for {path}")
        synced_entry = _synced_file_by_id(state, file_id)
        if synced_entry is None:
            raise SyncConflictError(f"Unknown synchronized file: {file_id}")
        old_path, synced_baseline = synced_entry
        state["files"].pop(old_path)
        state["files"][operation["target_path"]] = synced_baseline
        # RENAME does not increment the content version in this backend.
        state["remote_files"][file_id] = {
            "path": operation["target_path"],
            "version": synced_baseline["version"],
        }

    state["pending_operations"].pop(operation["request_id"])
    _save(root, state)

    if operation["snapshot_path"]:
        safe_local_path(root, operation["snapshot_path"]).unlink(missing_ok=True)


def _recover_remote(
    root: Path, state: SyncState, executor: SyncExecutor, operation: dict
) -> None:
    action = PlannedAction(
        SyncAction(operation["action"]),
        operation["path"],
        operation["file_id"],
        operation["target_path"],
    )
    snapshot = None
    if operation["snapshot_path"]:
        snapshot = safe_local_path(root, operation["snapshot_path"])

    try:
        result = executor.execute(
            action,
            expected_version=operation["expected_version"],
            expected_hash=operation["expected_hash"],
            request_id=operation["request_id"],
            snapshot_path=snapshot,
        )
    except httpx.HTTPStatusError as exc:
        if action.action != SyncAction.UPDATE_REMOTE or exc.response.status_code != 409:
            raise
        # A rejection is not an uncertain success. Preserve the original
        # request and snapshot, but never submit replacement work for this file.
        operation["status"] = "invalidated"
        _save(root, state)
        return
    _finish_remote(root, state, operation, result)


def _prepare_local(
    root: Path,
    state: SyncState,
    action: PlannedAction,
    remote: dict[str, RemoteFile],
    api: DropboxClient,
) -> dict:
    synced_entry = _synced_file_by_id(state, action.file_id) if action.file_id else None
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
        source = safe_local_path(root, action.path)
        if source.is_symlink():
            raise SyncConflictError(f"Cannot preserve a symlink: {action.path}")
        local = scan_file(root, action.path)
        if local is None:
            raise SyncConflictError(f"Conflict source missing: {action.path}")
        reserved = {root / info["path"] for info in remote.values()}
        _, blocked_paths, _ = _recovery_protection(state, {})
        reserved.update(root / path for path in blocked_paths)
        target = conflicted_copy_path(source, reserved)
        operation.update(
            target_path=target.relative_to(root).as_posix(),
            expected_hash=local["hash"],
            size=local["size"],
        )
    elif action.action == SyncAction.DOWNLOAD:
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
    state["pending_operations"][operation["request_id"]] = operation
    _save(root, state)
    return operation


def _recover_download(
    root: Path, state: SyncState, executor: SyncExecutor, operation: dict
) -> dict | None:
    """Return the installed baseline, or None for a safely obsolete intent."""
    path = operation["path"]
    destination = safe_local_path(root, path)
    if destination.is_symlink():
        raise SyncConflictError(f"Download destination is a symlink: {path}")
    local = scan_file(root, path)
    unchanged = (
        local is None and not destination.exists()
        if operation["expected_hash"] is None
        else local is not None and local["hash"] == operation["expected_hash"]
    )
    has_target = "download_hash" in operation
    if has_target and local == {
        "hash": operation["download_hash"],
        "size": operation["download_size"],
    }:
        return {
            "file_id": operation["file_id"],
            "version": operation["expected_version"],
            "hash": operation["download_hash"],
            "size": operation["download_size"],
        }
    if has_target and not unchanged:
        raise SyncConflictError(f"Local file changed during pending download: {path}")

    # Legacy records may be enriched only while their original target is still
    # identifiable. Current metadata cannot prove an older installation.
    try:
        metadata = executor.api.get_file_metadata(operation["file_id"])
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        if unchanged:
            return None
        raise SyncConflictError(f"Ambiguous legacy download: {path}") from exc
    if metadata["file_id"] != operation["file_id"]:
        raise SyncConflictError(f"Unexpected download identity: {path}")
    if metadata["version"] != operation["expected_version"] or metadata["key"] != path:
        if unchanged:
            return None
        raise SyncConflictError(f"Ambiguous legacy download: {path}")
    if not has_target:
        if not unchanged and local != {
            "hash": metadata["content_hash"],
            "size": metadata["size"],
        }:
            raise SyncConflictError(f"Ambiguous legacy download: {path}")
        operation.update(
            download_hash=metadata["content_hash"], download_size=metadata["size"]
        )
        _save(root, state)
        return _recover_download(root, state, executor, operation)

    return executor.execute(
        PlannedAction(SyncAction.DOWNLOAD, path, operation["file_id"]),
        expected_version=operation["expected_version"],
        expected_hash=operation["expected_hash"],
        download_hash=operation["download_hash"],
        download_size=operation["download_size"],
    )


def _recover_local(
    root: Path, state: SyncState, executor: SyncExecutor, operation: dict
) -> None:
    kind = SyncAction(operation["action"])
    action = PlannedAction(
        kind, operation["path"], operation["file_id"], operation["target_path"]
    )
    source = safe_local_path(root, action.path)

    if kind == SyncAction.PRESERVE_LOCAL:
        destination = safe_local_path(root, action.target_path)
        if source.is_symlink() or destination.is_symlink():
            raise SyncConflictError(f"Ambiguous conflict preservation: {action.path}")
        expected = {"hash": operation["expected_hash"], "size": operation["size"]}
        if destination.exists():
            if scan_file(root, action.target_path) != expected or (
                source.exists() and not source.samefile(destination)
            ):
                raise SyncConflictError(
                    f"Ambiguous conflict copy: {action.target_path}"
                )
        executor.execute(action, expected_hash=operation["expected_hash"])
        if scan_file(root, action.target_path) != expected:
            raise SyncConflictError(
                f"Conflict copy missing or changed: {action.target_path}"
            )
        synced_entry = _synced_file_by_id(state, action.file_id)
        if synced_entry:
            if synced_entry[0] != action.path:
                raise SyncConflictError(f"Conflict baseline moved: {action.path}")
            state["files"].pop(synced_entry[0])

    elif kind == SyncAction.DELETE_LOCAL:
        if source.exists():
            executor.execute(action, expected_hash=operation["expected_hash"])
        synced_entry = _synced_file_by_id(state, action.file_id)
        if synced_entry:
            state["files"].pop(synced_entry[0])

    elif kind == SyncAction.RENAME_LOCAL:
        destination = safe_local_path(root, action.target_path)
        if source.exists():
            executor.execute(action, expected_hash=operation["expected_hash"])
        elif (
            not destination.is_file()
            or file_hash(destination) != operation["expected_hash"]
        ):
            raise SyncConflictError(f"Cannot recover local rename: {action.path}")
        synced_entry = _synced_file_by_id(state, action.file_id)
        if synced_entry:
            state["files"].pop(synced_entry[0])
            state["files"][action.target_path] = synced_entry[1]

    elif kind == SyncAction.DOWNLOAD:
        result = _recover_download(root, state, executor, operation)
        if result is not None:
            synced_entry = _synced_file_by_id(state, action.file_id)
            if synced_entry:
                state["files"].pop(synced_entry[0])
            state["files"][action.path] = result

    state["pending_operations"].pop(operation["request_id"])
    _save(root, state)


def _recover_pending(root: Path, state: SyncState, executor: SyncExecutor) -> None:
    for operation in list(state["pending_operations"].values()):
        if operation.get("status") == "invalidated":
            continue
        if SyncAction(operation["action"]) in REMOTE_ACTIONS:
            _recover_remote(root, state, executor, operation)
        else:
            _recover_local(root, state, executor, operation)


def _apply_journal(
    state: SyncState,
    events: list[dict],
    checkpoint: int,
    root: Path,
    api: DropboxClient,
) -> None:
    # Events identify stale observations; their historical paths and versions
    # must never overwrite a more recent acknowledged mutation.
    remote_files = state["remote_files"].copy()
    for file_id in dict.fromkeys(event["file_id"] for event in events):
        try:
            metadata = api.get_file_metadata(file_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            try:
                error = exc.response.json()
            except ValueError:
                raise exc
            # Distinguish this endpoint's inactive-file response from a generic
            # proxy/routing 404. Uncertain failures must not authorize deletion.
            if not isinstance(error, dict) or error.get("detail") != "File not found":
                raise
            remote_files.pop(file_id, None)
            continue
        if metadata["file_id"] != file_id:
            raise SyncConflictError(f"Unexpected journal metadata identity: {file_id}")
        if type(metadata["version"]) is not int or metadata["version"] < 1:
            raise SyncConflictError(f"Invalid journal metadata version: {file_id}")
        safe_local_path(root, metadata["key"])
        remote_files[metadata["file_id"]] = {
            "path": metadata["key"],
            "version": metadata["version"],
        }

    # Stage everything, including removal of legacy suppression markers, so a
    # refresh or persistence failure leaves the caller's state unchanged.
    refreshed = {**state, "remote_files": remote_files, "checkpoint": checkpoint}
    refreshed.pop("completed_requests", None)
    _save(root, refreshed)
    state.clear()
    state.update(refreshed)


def _recovery_protection(
    state: SyncState, local_snapshot: dict
) -> tuple[set[str], set[str], bool]:
    """Protect unresolved identities, occupied paths, and unidentifiable moves."""
    blocked_ids: set[str] = set()
    blocked_paths: set[str] = set()
    defer_creates = False
    for operation in state["pending_operations"].values():
        if operation.get("status") != "invalidated":
            continue
        file_id = operation["file_id"]
        blocked_ids.add(file_id)
        paths = {operation["path"]}
        hashes = {operation["expected_hash"]}
        for path, saved in state["files"].items():
            if saved["file_id"] == file_id:
                paths.add(path)
                hashes.add(saved["hash"])
        if file_id in state["remote_files"]:
            paths.add(state["remote_files"][file_id]["path"])

        # An old name is no longer reserved when both durable observations
        # associate it with a different synchronized identity.
        for path in paths.copy():
            owner = state["files"].get(path, {}).get("file_id")
            if (
                owner is not None
                and owner != file_id
                and state["remote_files"].get(owner, {}).get("path") == path
            ):
                paths.remove(path)
        aliases = {
            path
            for path, info in local_snapshot.items()
            if path not in state["files"] and info["hash"] in hashes
        }
        blocked_paths.update(paths | aliases)
        if not any(
            local_snapshot.get(path, {}).get("hash") in hashes for path in paths
        ):
            # A rename followed by an edit cannot be identified from hashes.
            # Existing independent identities still progress, but unknown local
            # files must not escape protection as fresh CREATE requests.
            defer_creates = True
    return blocked_ids, blocked_paths, defer_creates


def _path_overlaps(path: str, reserved: str) -> bool:
    return (
        path == reserved
        or path.startswith(reserved + "/")
        or reserved.startswith(path + "/")
    )


def sync_folder(
    api: DropboxClient,
    folder: str,
    local_snapshot: dict | None = None,
) -> dict:
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)

    state = load_state(str(root))
    executor = SyncExecutor(api, root)

    # Recovery may change files on disk.
    had_pending = bool(state["pending_operations"])
    _recover_pending(root, state, executor)

    if local_snapshot is None or had_pending:
        local_snapshot = scan_folder(str(root))

    events, checkpoint = fetch_remote_updates(api, state["checkpoint"])
    _apply_journal(state, events, checkpoint, root, api)

    for _ in range(1000):
        moves = dict(detect_local_renames(local_snapshot, state["files"]))
        actions = plan_sync(
            local_snapshot,
            state["remote_files"],
            state["files"],
            moves,
        )

        blocked_ids, blocked_paths, defer_creates = _recovery_protection(
            state, local_snapshot
        )
        actions = [
            action
            for action in actions
            if action.file_id not in blocked_ids
            and not (defer_creates and action.action == SyncAction.CREATE_REMOTE)
            and not any(
                _path_overlaps(path, reserved)
                for path in (
                    action.path,
                    action.target_path,
                    state["remote_files"].get(action.file_id, {}).get("path"),
                )
                if path is not None
                for reserved in blocked_paths
            )
        ]

        conflicts = [
            action for action in actions if action.action == SyncAction.CONFLICT
        ]

        if conflicts:
            raise SyncConflictError(
                "Unresolved synchronization conflicts: "
                + ", ".join(action.path for action in conflicts)
            )

        if not actions:
            for path, saved in list(state["files"].items()):
                if (
                    path not in local_snapshot
                    and saved["file_id"] not in state["remote_files"]
                    and saved["file_id"] not in blocked_ids
                ):
                    state["files"].pop(path)

            _save(root, state)
            if blocked_ids:
                raise SyncConflictError(
                    "Invalidated updates require resolution: "
                    + ", ".join(sorted(blocked_ids))
                )
            return local_snapshot

        action = actions[0]

        if action.action in REMOTE_ACTIONS:
            operation = _prepare_remote(root, state, action)
            _recover_remote(root, state, executor, operation)

        elif action.action in LOCAL_ACTIONS:
            operation = _prepare_local(root, state, action, state["remote_files"], api)
            _recover_local(root, state, executor, operation)

            affected = {action.path}

            if operation["target_path"]:
                affected.add(operation["target_path"])

            for path in affected:
                metadata = scan_file(root, path)

                if metadata is None:
                    local_snapshot.pop(path, None)
                else:
                    local_snapshot[path] = metadata

        else:
            raise SyncConflictError(f"Unsupported action: {action.action}")

    raise SyncConflictError("Too many synchronization actions in one cycle")
