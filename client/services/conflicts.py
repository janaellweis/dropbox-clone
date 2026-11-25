import os
from pathlib import Path

import httpx

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.detection.scanner import scan_file
from client.services.planner import PlannedAction
from client.storage.downloads import download_verified, safe_local_path
from client.storage.state import RemoteFile, SyncState, save_state


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
    with (path if source_exists else conflict_path).open("rb") as source:
        os.fsync(source.fileno())
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


def _preserve_rejected_snapshot(root: Path, state: SyncState, operation: dict) -> Path:
    """Preserve rejected upload bytes without removing the pending snapshot."""
    import os

    from client.storage.downloads import file_hash

    snapshot = safe_local_path(root, operation["snapshot_path"])
    source = safe_local_path(root, operation["path"])

    if (
        not snapshot.is_file()
        or snapshot.is_symlink()
        or file_hash(snapshot) != operation["expected_hash"]
        or snapshot.stat().st_size != operation["size"]
    ):
        raise SyncConflictError("Rejected upload snapshot is invalid")

    reserved = {root / item["path"] for item in state["remote_files"].values()}
    saved_target = operation.get("target_path")
    if saved_target:
        target = safe_local_path(root, saved_target)
        if os.path.lexists(target):
            raise SyncConflictError(
                "Conflict copy already exists; manual recovery required"
            )
    else:
        target = conflicted_copy_path(source, reserved)
        operation["target_path"] = target.relative_to(root).as_posix()
        save_state(str(root), state)

    import shutil

    try:
        with snapshot.open("rb") as source_file:
            with target.open("xb") as target_file:
                shutil.copyfileobj(source_file, target_file)
                target_file.flush()
                os.fsync(target_file.fileno())
    except FileExistsError as exc:
        raise SyncConflictError("Conflict copy destination occupied") from exc

    if file_hash(target) != operation["expected_hash"]:
        raise SyncConflictError("Conflict copy verification failed")

    return target


def _preserve_newer_local_edits(
    root: Path, state: SyncState, operation: dict
) -> Path | None:
    """Preserve edits made after the rejected upload snapshot."""
    import shutil

    from client.storage.downloads import file_hash

    source = safe_local_path(root, operation["path"])
    local = scan_file(root, operation["path"])

    if local is None or source.is_symlink():
        raise SyncConflictError("Local file missing during conflict resolution")

    if local["hash"] == operation["expected_hash"]:
        return None

    reserved = {root / item["path"] for item in state["remote_files"].values()}
    reserved.add(safe_local_path(root, operation["target_path"]))

    saved_target = operation.get("newer_copy_path")
    if saved_target is not None:
        raise SyncConflictError(
            "Newer conflict copy already reserved; manual recovery required"
        )

    target = conflicted_copy_path(source, reserved)

    # Record the destination before creating the file.
    operation["newer_copy_path"] = target.relative_to(root).as_posix()
    save_state(str(root), state)

    try:
        with source.open("rb") as reader:
            with target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
    except FileExistsError as exc:
        raise SyncConflictError("Newer conflict copy destination occupied") from exc

    if file_hash(target) != local["hash"] or target.stat().st_size != local["size"]:
        raise SyncConflictError("Newer local edits changed during preservation")

    directory_fd = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

    return target


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
) -> dict:
    """Resolve a confirmed stale-version upload."""
    operation["status"] = "invalidated"
    save_state(str(root), state)
    _preserve_rejected_snapshot(root, state, operation)

    metadata = api.get_file_metadata(operation["file_id"])
    if (
        metadata["file_id"] != operation["file_id"]
        or metadata["key"] != operation["path"]
        or metadata["version"] <= operation["expected_version"]
    ):
        raise SyncConflictError(f"Remote changed unexpectedly: {operation['path']}")

    # Preserve edits made after the upload snapshot was created.
    newer_copy = _preserve_newer_local_edits(root, state, operation)

    local = scan_file(root, operation["path"])
    if local is None:
        raise SyncConflictError(f"Local file disappeared: {operation['path']}")

    result = download_verified(
        api,
        root,
        operation["path"],
        operation["file_id"],
        metadata["version"],
        expected_local_hash=local["hash"],
        expected_content_hash=metadata["content_hash"],
        expected_size=metadata["size"],
    )

    state["files"][operation["path"]] = result
    state["remote_files"][operation["file_id"]] = {
        "path": operation["path"],
        "version": metadata["version"],
    }
    state["pending_operations"].pop(operation["request_id"])
    save_state(str(root), state)

    safe_local_path(root, operation["snapshot_path"]).unlink(missing_ok=True)

    operation["affected_paths"] = [
        operation["path"],
        operation["target_path"],
    ]
    if newer_copy is not None:
        operation["affected_paths"].append(newer_copy.relative_to(root).as_posix())
    return operation


def prepare_conflict_copy(
    root: Path,
    state: SyncState,
    action: PlannedAction,
    remote: dict[str, RemoteFile],
    operation: dict,
) -> None:
    """Reserve a conflict-copy destination and record the source contents."""
    source = safe_local_path(root, action.path)
    if source.is_symlink():
        raise SyncConflictError(f"Cannot preserve a symlink: {action.path}")

    local = scan_file(root, action.path)
    if local is None:
        raise SyncConflictError(f"Conflict source missing: {action.path}")

    reserved = {root / info["path"] for info in remote.values()}
    target = conflicted_copy_path(source, reserved)
    operation.update(
        target_path=target.relative_to(root).as_posix(),
        expected_hash=local["hash"],
        size=local["size"],
    )
