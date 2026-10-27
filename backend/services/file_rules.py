import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.db.models import FileVersion


class FileVersionConflictError(Exception):
    """The client's expected version is not the current server version."""


def lock_journal(session: Session) -> None:
    """Serialize journal writes until the current transaction completes."""
    session.execute(text("SELECT pg_advisory_xact_lock(742001)"))


def require_latest_version(
    session: Session,
    file_id: uuid.UUID,
    expected_version: int,
) -> FileVersion:
    """Return the latest version if it matches the client's expectation."""
    latest = (
        session.query(FileVersion)
        .filter_by(file_id=file_id)
        .order_by(FileVersion.version_number.desc())
        .first()
    )

    if latest is None:
        raise FileNotFoundError("File version not found")

    if latest.version_number != expected_version:
        raise FileVersionConflictError("File version is outdated")

    return latest


def is_valid_file_key(key: str) -> bool:
    """Accept relative, slash-separated file paths without traversal."""
    return (
        bool(key)
        and "\\" not in key
        and all(part not in ("", ".", "..") for part in key.split("/"))
    )
