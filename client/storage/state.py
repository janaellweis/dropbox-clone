"""Durable acknowledgement baselines, remote observations, and recovery intents.

Loading validates recovery preconditions without normalizing or discarding legacy
fields. Saving replaces the state atomically after flushing its contents.
"""

import json
import os
import tempfile
from pathlib import Path
from typing import Any, TypedDict, cast

STATE_FILE = ".sync_state.json"
SCHEMA_VERSION = 1


class SyncedFile(TypedDict):
    """Last acknowledged local baseline, which may differ from live local bytes."""

    file_id: str
    version: int
    hash: str
    size: int


class RemoteFile(TypedDict):
    """Last acknowledged/refreshed remote path/version; may lag the server."""

    path: str
    version: int


class SyncState(TypedDict):
    """JSON records retain their existing field names and insertion order."""

    schema_version: int
    checkpoint: int
    files: dict[str, SyncedFile]  # Relative path -> acknowledged local baseline.
    remote_files: dict[str, RemoteFile]  # File ID -> latest remote observation.
    pending_operations: dict[str, dict[str, Any]]  # Request ID -> durable intent.


def _default_state() -> SyncState:
    return {
        "schema_version": SCHEMA_VERSION,
        "checkpoint": 0,
        "files": {},
        "remote_files": {},
        "pending_operations": {},
    }


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_state(state: Any) -> SyncState:
    if not isinstance(state, dict):
        raise ValueError("Sync state must be a JSON object")

    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported sync state schema version")

    checkpoint = state.get("checkpoint")
    if type(checkpoint) is not int or checkpoint < 0:
        raise ValueError("Invalid sync checkpoint")

    for field in ("files", "remote_files", "pending_operations"):
        if not isinstance(state.get(field), dict):
            raise ValueError(f"Invalid sync state field: {field}")

    for request_id, operation in state["pending_operations"].items():
        if not isinstance(operation, dict):
            raise ValueError("Invalid pending operation")
        status = operation.get("status", "pending")
        if status not in ("pending", "invalidated") or (
            status == "invalidated" and operation.get("action") != "update_remote"
        ):
            raise ValueError("Invalid pending operation status")
        if "download_hash" in operation or "download_size" in operation:
            digest = operation.get("download_hash")
            size = operation.get("download_size")
            if (
                operation.get("action") != "download"
                or not _is_sha256(digest)
                or type(size) is not int
                or size < 0
            ):
                raise ValueError("Invalid pending download target")

        # Invalidated UPDATEs remain durable blockers, never replayed intents.
        # Preserve legacy records that lack fields needed only for execution.
        if status == "invalidated":
            continue
        if (
            not isinstance(operation.get("request_id"), str)
            or not operation["request_id"]
            or operation["request_id"] != request_id
        ):
            raise ValueError("Invalid pending operation request ID")
        if operation.get("action") == "preserve_local":
            target = operation.get("target_path")
            size = operation.get("size")
            if (
                not isinstance(target, str)
                or not target
                or not _is_sha256(operation.get("expected_hash"))
                or type(size) is not int
                or size < 0
            ):
                raise ValueError("Invalid pending preservation target or content")

    return cast(SyncState, state)


def load_state(folder: str) -> SyncState:
    path = Path(folder) / STATE_FILE

    try:
        with path.open("r", encoding="utf-8") as file:
            state = json.load(file)
    except FileNotFoundError:
        return _default_state()
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError(f"Corrupted sync state: {path}") from exc

    return _validate_state(state)


def save_state(folder: str, state: SyncState) -> None:
    _validate_state(state)

    path = Path(folder) / STATE_FILE
    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".sync-state-",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(state, file, indent=2)
            file.flush()
            os.fsync(file.fileno())

        temporary_path.replace(path)

        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
