import sqlite3
from pathlib import Path
from typing import cast

from client.core.types import RemoteFile, SyncedFile, SyncState
from client.core.validation import is_nonnegative_int, is_sha256
from client.storage.schema import (
    _PENDING_COLUMNS,
    _SCHEMA,
    DATABASE_FILE,
    SCHEMA_VERSION,
)


def _validate_pending_operation(operation: dict) -> None:
    request_id = operation.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("Invalid pending operation request ID")

    action = operation.get("action")
    status = operation.get("status", "pending")

    if status not in ("pending", "invalidated") or (
        status == "invalidated" and action != "update_remote"
    ):
        raise ValueError("Invalid pending operation status")

    if action == "download":
        size = operation.get("download_size")
        if (
            not is_sha256(operation.get("download_hash"))
            or not is_nonnegative_int(size)
            or "expected_hash" not in operation
        ):
            raise ValueError("Invalid pending download target")
    elif (
        operation.get("download_hash") is not None
        or operation.get("download_size") is not None
    ):
        raise ValueError("Invalid pending download target")

    if action == "preserve_local":
        target = operation.get("target_path")
        size = operation.get("size")
        if (
            not isinstance(target, str)
            or not target
            or not is_sha256(operation.get("expected_hash"))
            or not is_nonnegative_int(size)
        ):
            raise ValueError("Invalid pending preservation target or content")


def _apply_changes(entries: dict, upserts: dict, deletes: set[str]) -> None:
    for key in deletes:
        entries.pop(key, None)
    entries.update(upserts)


class StateStore:
    """Manage durable synchronization state in a local SQLite database."""

    def __init__(self, folder: Path) -> None:
        self._connection = sqlite3.connect(
            folder / DATABASE_FILE,
        )

        try:
            self._connection.row_factory = sqlite3.Row
            mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]

            if mode.lower() != "wal":
                raise RuntimeError("Could not enable SQLite WAL mode")

            self._connection.execute("PRAGMA synchronous = FULL")
            self._initialize()
        except BaseException:
            self._connection.close()
            raise

    def _initialize(self) -> None:
        self._connection.executescript(_SCHEMA)

        with self._connection:
            self._connection.execute(
                """
                INSERT INTO metadata (id, schema_version, checkpoint)
                VALUES (1, ?, 0)
                ON CONFLICT(id) DO NOTHING
                """,
                (SCHEMA_VERSION,),
            )

            row = self._connection.execute(
                "SELECT schema_version FROM metadata WHERE id = 1"
            ).fetchone()

            if row["schema_version"] != SCHEMA_VERSION:
                raise ValueError("Unsupported sync state schema version")

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def load_state(self) -> SyncState:
        with self._connection:
            self._connection.execute("BEGIN")

            metadata = self._connection.execute(
                "SELECT schema_version, checkpoint FROM metadata WHERE id = 1"
            ).fetchone()

            files = {
                row["path"]: {
                    "file_id": row["file_id"],
                    "version": row["version"],
                    "hash": row["hash"],
                    "size": row["size"],
                }
                for row in self._connection.execute("SELECT * FROM local_files")
            }

            remote_files = {
                row["file_id"]: {
                    "path": row["path"],
                    "version": row["version"],
                }
                for row in self._connection.execute("SELECT * FROM remote_files")
            }

            pending_operations = {
                row["request_id"]: dict(row)
                for row in self._connection.execute(
                    "SELECT * FROM pending_operations ORDER BY request_id"
                )
            }

            state = {
                "schema_version": metadata["schema_version"],
                "checkpoint": metadata["checkpoint"],
                "files": files,
                "remote_files": remote_files,
                "pending_operations": pending_operations,
            }

        if state["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported sync state schema version")

        checkpoint = state["checkpoint"]
        if not is_nonnegative_int(checkpoint):
            raise ValueError("Invalid sync checkpoint")

        for operation in pending_operations.values():
            _validate_pending_operation(operation)

        return cast(SyncState, state)

    @staticmethod
    def _pending_values(operation: dict) -> dict:
        unknown_fields = operation.keys() - set(_PENDING_COLUMNS)
        if unknown_fields:
            raise ValueError(
                f"Unknown pending operation fields: {sorted(unknown_fields)}"
            )

        record = {"status": "pending", **operation}
        _validate_pending_operation(record)
        return {column: record.get(column) for column in _PENDING_COLUMNS}

    def add_pending_operation(self, state: SyncState, operation: dict) -> None:
        values = self._pending_values(operation)
        columns = ", ".join(_PENDING_COLUMNS)
        placeholders = ", ".join(f":{column}" for column in _PENDING_COLUMNS)

        with self._connection:
            self._connection.execute(
                f"""
                INSERT INTO pending_operations ({columns})
                VALUES ({placeholders})
                """,
                values,
            )

        state["pending_operations"][operation["request_id"]] = operation

    def update_pending_operation(
        self, state: SyncState, request_id: str, **changes
    ) -> None:
        pending = state["pending_operations"].get(request_id)
        if pending is None:
            raise ValueError("Unknown pending operation")
        operation = {**pending, **changes}
        values = self._pending_values(operation)
        assignments = ", ".join(
            f"{column} = :{column}"
            for column in _PENDING_COLUMNS
            if column != "request_id"
        )

        with self._connection:
            cursor = self._connection.execute(
                f"""
                UPDATE pending_operations
                SET {assignments}
                WHERE request_id = :request_id
                """,
                values,
            )

            if cursor.rowcount != 1:
                raise ValueError("Unknown pending operation")

        pending.update(operation)

    def _write_local_changes(
        self,
        upserts: dict[str, SyncedFile],
        deletes: set[str],
    ) -> None:
        self._connection.executemany(
            "DELETE FROM local_files WHERE path = ?",
            [(path,) for path in deletes],
        )

        self._connection.executemany(
            """
            INSERT INTO local_files (path, file_id, version, hash, size)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(path) DO UPDATE SET
                file_id = excluded.file_id,
                version = excluded.version,
                hash = excluded.hash,
                size = excluded.size
            """,
            [
                (
                    path,
                    entry["file_id"],
                    entry["version"],
                    entry["hash"],
                    entry["size"],
                )
                for path, entry in upserts.items()
            ],
        )

    def _write_remote_changes(
        self,
        upserts: dict[str, RemoteFile],
        deletes: set[str],
    ) -> None:
        self._connection.executemany(
            "DELETE FROM remote_files WHERE file_id = ?",
            [(file_id,) for file_id in deletes],
        )

        self._connection.executemany(
            """
            INSERT INTO remote_files (file_id, path, version)
            VALUES (?, ?, ?)
            ON CONFLICT(file_id) DO UPDATE SET
                path = excluded.path,
                version = excluded.version
            """,
            [
                (file_id, entry["path"], entry["version"])
                for file_id, entry in upserts.items()
            ],
        )

    def complete_operation(
        self,
        state: SyncState,
        request_id: str,
        *,
        local_upserts: dict[str, SyncedFile] | None = None,
        local_deletes: set[str] | None = None,
        remote_upserts: dict[str, RemoteFile] | None = None,
        remote_deletes: set[str] | None = None,
    ) -> None:
        local_upserts = local_upserts or {}
        local_deletes = local_deletes or set()
        remote_upserts = remote_upserts or {}
        remote_deletes = remote_deletes or set()

        with self._connection:
            self._write_local_changes(local_upserts, local_deletes)
            self._write_remote_changes(remote_upserts, remote_deletes)

            cursor = self._connection.execute(
                "DELETE FROM pending_operations WHERE request_id = ?",
                (request_id,),
            )

            if cursor.rowcount != 1:
                raise ValueError("Unknown pending operation")

        _apply_changes(state["files"], local_upserts, local_deletes)
        _apply_changes(state["remote_files"], remote_upserts, remote_deletes)
        state["pending_operations"].pop(request_id, None)

    def apply_remote_updates(
        self,
        state: SyncState,
        updates: dict[str, RemoteFile],
        deleted_ids: set[str],
        checkpoint: int,
    ) -> None:
        if not is_nonnegative_int(checkpoint):
            raise ValueError("Invalid sync checkpoint")

        with self._connection:
            self._write_remote_changes(updates, deleted_ids)

            cursor = self._connection.execute(
                """
                UPDATE metadata
                SET checkpoint = ?
                WHERE id = 1 AND checkpoint <= ?
                """,
                (checkpoint, checkpoint),
            )

            if cursor.rowcount != 1:
                raise ValueError("Invalid checkpoint progression")

        _apply_changes(state["remote_files"], updates, deleted_ids)
        state["checkpoint"] = checkpoint

    def remove_local_files(self, state: SyncState, paths: set[str]) -> None:
        if not paths:
            return

        with self._connection:
            self._write_local_changes({}, paths)

        _apply_changes(state["files"], {}, paths)
