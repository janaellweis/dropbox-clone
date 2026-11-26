import os
import shutil
from pathlib import Path

import httpx

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.core.types import SyncState
from client.detection.scanner import scan_file
from client.storage.downloads import download_verified
from client.storage.durability import sync_directories, sync_file
from client.storage.filesystem import file_hash, safe_local_path
from client.storage.state import StateStore


def _reserved_remote_paths(root: Path, state: SyncState) -> set[Path]:
    return {root / item["path"] for item in state["remote_files"].values()}


def overlaps_reserved(candidate: Path, reserved: set[Path]) -> bool:
    """True if candidate is, contains, or sits inside any reserved path."""
    return any(
        candidate == entry or candidate in entry.parents or entry in candidate.parents
        for entry in reserved
    )


def conflicted_copy_path(path: Path, reserved: set[Path]) -> Path:
    for counter in range(1, 101):
        suffix = (
            " (conflicted copy)" if counter == 1 else f" (conflicted copy {counter})"
        )

        conflict_path = path.with_name(f"{path.stem}{suffix}{path.suffix}")

        if not os.path.lexists(conflict_path) and not overlaps_reserved(
            conflict_path, reserved
        ):
            return conflict_path

    raise SyncConflictError(
        f"Could not find an available conflict copy name for {path}"
    )


def preserve_conflicted_copy(path: Path, conflict_path: Path) -> None:
    """Move to a fixed name without overwriting; interrupted links are retryable."""
    if path.is_symlink() or conflict_path.is_symlink():
        raise SyncConflictError("Cannot preserve a symlink as a conflict copy")
    if path.parent != conflict_path.parent or path == conflict_path:
        raise ValueError("Conflict preservation requires a distinct sibling path")
    source_exists = path.exists()
    sync_file(path if source_exists else conflict_path)
    if source_exists:
        try:
            os.link(path, conflict_path, follow_symlinks=False)
        except FileExistsError:
            if conflict_path.is_symlink() or not path.samefile(conflict_path):
                raise SyncConflictError(
                    f"Conflict destination occupied: {conflict_path}"
                )
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        # Make the new name durable before removing the original name.
        os.fsync(directory_fd)
        if path.exists():
            if (
                path.is_symlink()
                or conflict_path.is_symlink()
                or not path.samefile(conflict_path)
            ):
                raise SyncConflictError(f"Paths changed during preservation: {path}")
            path.unlink()
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _copy_exclusively(source: Path, target: Path) -> None:
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def _preserve_rejected_snapshot(
    root: Path,
    state: SyncState,
    operation: dict,
    store: StateStore,
) -> Path:
    """Preserve rejected upload bytes without removing the pending snapshot."""
    snapshot = safe_local_path(root, operation["snapshot_path"])
    source = safe_local_path(root, operation["path"])

    if (
        not snapshot.is_file()
        or snapshot.is_symlink()
        or file_hash(snapshot) != operation["expected_hash"]
        or snapshot.stat().st_size != operation["size"]
    ):
        raise SyncConflictError("Rejected upload snapshot is invalid")

    reserved = _reserved_remote_paths(root, state)
    saved_target = operation.get("target_path")
    if saved_target:
        target = safe_local_path(root, saved_target)
        if os.path.lexists(target):
            raise SyncConflictError(
                "Conflict copy already exists; manual recovery required"
            )
    else:
        target = conflicted_copy_path(source, reserved)
        store.update_pending_operation(
            state,
            operation["request_id"],
            target_path=target.relative_to(root).as_posix(),
        )

    try:
        _copy_exclusively(snapshot, target)
    except FileExistsError as exc:
        raise SyncConflictError("Conflict copy destination occupied") from exc

    if file_hash(target) != operation["expected_hash"]:
        raise SyncConflictError("Conflict copy verification failed")

    sync_directories(target.parent, root)
    return target


def _preserve_newer_local_edits(
    root: Path,
    state: SyncState,
    operation: dict,
    store: StateStore,
) -> tuple[Path | None, str]:
    """Preserve newer edits and return the safely preserved local hash."""
    source = safe_local_path(root, operation["path"])
    local = scan_file(root, operation["path"])

    if local is None or source.is_symlink():
        raise SyncConflictError("Local file missing during conflict resolution")

    if local["hash"] == operation["expected_hash"]:
        return None, local["hash"]

    reserved = _reserved_remote_paths(root, state)
    reserved.add(safe_local_path(root, operation["target_path"]))

    saved_target = operation.get("newer_copy_path")
    if saved_target is not None:
        raise SyncConflictError(
            "Newer conflict copy already reserved; manual recovery required"
        )

    target = conflicted_copy_path(source, reserved)

    # Record the destination before creating the file.
    store.update_pending_operation(
        state,
        operation["request_id"],
        newer_copy_path=target.relative_to(root).as_posix(),
    )

    try:
        _copy_exclusively(source, target)
    except FileExistsError as exc:
        raise SyncConflictError("Newer conflict copy destination occupied") from exc

    if file_hash(target) != local["hash"] or target.stat().st_size != local["size"]:
        raise SyncConflictError("Newer local edits changed during preservation")

    sync_directories(target.parent, root)

    return target, local["hash"]


def is_version_conflict(exc: httpx.HTTPStatusError) -> bool:
    if exc.response.status_code != 409:
        return False

    try:
        error = exc.response.json()
    except ValueError:
        return False

    return isinstance(error, dict) and error.get("code") == "FileVersionConflictError"


def resolve_version_conflict(
    root: Path,
    state: SyncState,
    operation: dict,
    api: DropboxClient,
    store: StateStore,
) -> dict:
    """Resolve a confirmed stale-version upload."""
    store.update_pending_operation(state, operation["request_id"], status="invalidated")

    _preserve_rejected_snapshot(root, state, operation, store)

    metadata = api.get_file_metadata(operation["file_id"])
    if (
        metadata["file_id"] != operation["file_id"]
        or metadata["key"] != operation["path"]
        or metadata["version"] <= operation["expected_version"]
    ):
        raise SyncConflictError(f"Remote changed unexpectedly: {operation['path']}")

    # Preserve edits made after the upload snapshot was created.
    newer_copy, preserved_hash = _preserve_newer_local_edits(
        root, state, operation, store
    )

    result = download_verified(
        api,
        root,
        operation["path"],
        operation["file_id"],
        metadata["version"],
        expected_local_hash=preserved_hash,
        expected_content_hash=metadata["content_hash"],
        expected_size=metadata["size"],
    )

    path = operation["path"]
    file_id = operation["file_id"]
    remote = {
        "path": path,
        "version": metadata["version"],
    }

    store.complete_operation(
        state,
        operation["request_id"],
        local_upserts={path: result},
        remote_upserts={file_id: remote},
    )

    safe_local_path(root, operation["snapshot_path"]).unlink(missing_ok=True)

    operation["affected_paths"] = [
        operation["path"],
        operation["target_path"],
    ]
    if newer_copy is not None:
        operation["affected_paths"].append(newer_copy.relative_to(root).as_posix())
    return operation


def prepare_conflict_copy(root: Path, state: SyncState, operation: dict) -> None:
    """Reserve a conflict-copy destination and record the source contents."""
    path = operation["path"]
    source = safe_local_path(root, path)

    if source.is_symlink():
        raise SyncConflictError(f"Cannot preserve a symlink: {path}")

    local = scan_file(root, path)
    if local is None:
        raise SyncConflictError(f"Conflict source missing: {path}")

    reserved = _reserved_remote_paths(root, state)
    target = conflicted_copy_path(source, reserved)

    operation.update(
        target_path=target.relative_to(root).as_posix(),
        expected_hash=local["hash"],
        size=local["size"],
    )
