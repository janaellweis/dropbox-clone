import uuid
from pathlib import PurePosixPath

from sqlalchemy.orm import Session

from backend.core.exceptions import FileRecordNotFoundError, FileVersionConflictError
from backend.db.models import FileVersion


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
        raise FileRecordNotFoundError("File version not found")

    if latest.version_number != expected_version:
        raise FileVersionConflictError("File version is outdated")

    return latest


def is_valid_file_key(key: str) -> bool:
    """Validate a relative, slash-separated file key."""
    if not key or len(key) > 1024:
        return False

    if "\\" in key:
        return False

    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        return False

    path = PurePosixPath(key)

    return (
        not path.is_absolute()
        and str(path) == key
        and all(part != ".." for part in path.parts)
    )