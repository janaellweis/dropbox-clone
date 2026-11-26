from typing import Any, TypedDict


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
    schema_version: int
    checkpoint: int
    files: dict[str, SyncedFile]  # Relative path -> acknowledged local baseline.
    remote_files: dict[str, RemoteFile]  # File ID -> latest remote observation.
    pending_operations: dict[str, dict[str, Any]]  # Request ID -> durable intent.
