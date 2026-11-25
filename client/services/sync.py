from __future__ import annotations

from pathlib import Path

import httpx

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.detection.changes import detect_local_renames
from client.detection.scanner import scan_file, scan_folder
from client.remote import fetch_remote_updates
from client.services.executor import SyncExecutor
from client.services.operations import (
    complete_remote_operation,
    recover_pending_uploads,
    run_action,
)
from client.services.planner import SyncAction, plan_sync
from client.storage.downloads import safe_local_path
from client.storage.state import SyncState, load_state, save_state


def _save(root: Path, state: SyncState) -> None:
    save_state(str(root), state)


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
    recover_pending_uploads(root, state, executor, complete_remote_operation)

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
                ):
                    state["files"].pop(path)

            _save(root, state)
            return local_snapshot

        action = actions[0]

        operation = run_action(root, state, executor, action, api)

        if operation is None:
            continue

        affected = set(operation.get("affected_paths") or [action.path])
        if operation["target_path"]:
            affected.add(operation["target_path"])

        for path in affected:
            metadata = scan_file(root, path)

            if metadata is None:
                local_snapshot.pop(path, None)
            else:
                local_snapshot[path] = metadata

    raise SyncConflictError("Too many synchronization actions in one cycle")
