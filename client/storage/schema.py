"""Local synchronization database schema."""

STATE_FILE = ".sync_state.json"
DATABASE_FILE = ".sync_state.db"
SCHEMA_VERSION = 1


_PENDING_COLUMNS = (
    "request_id",
    "action",
    "status",
    "path",
    "file_id",
    "target_path",
    "expected_version",
    "expected_hash",
    "snapshot_path",
    "size",
    "download_hash",
    "download_size",
    "newer_copy_path",
)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    schema_version INTEGER NOT NULL,
    checkpoint INTEGER NOT NULL DEFAULT 0 CHECK (checkpoint >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS local_files (
    path TEXT PRIMARY KEY NOT NULL,
    file_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    hash TEXT NOT NULL CHECK (length(hash) = 64),
    size INTEGER NOT NULL CHECK (size >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS remote_files (
    file_id TEXT PRIMARY KEY NOT NULL,
    path TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS pending_operations (
    request_id TEXT PRIMARY KEY NOT NULL,
    action TEXT NOT NULL CHECK (
        action IN (
            'create_remote',
            'update_remote',
            'delete_remote',
            'rename_remote',
            'download',
            'delete_local',
            'rename_local',
            'preserve_local'
        )
    ),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'invalidated')),
    path TEXT NOT NULL,
    file_id TEXT,
    target_path TEXT,
    expected_version INTEGER CHECK (expected_version > 0),
    expected_hash TEXT CHECK (length(expected_hash) = 64),
    snapshot_path TEXT,
    size INTEGER CHECK (size >= 0),
    download_hash TEXT CHECK (length(download_hash) = 64),
    download_size INTEGER CHECK (download_size >= 0),
    newer_copy_path TEXT,
    CHECK (status != 'invalidated' OR action = 'update_remote')
) STRICT;
"""
