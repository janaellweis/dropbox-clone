import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.db.models import ChangeAction, ChangeJournal, File, FileVersion
from backend.services.file_rules import (
    FileVersionConflictError,
    is_valid_file_key,
    lock_journal,
    require_latest_version,
)
from backend.storage.local import LocalBlobStorage


def update_file(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
    content: bytes,
    expected_version: int,
    request_id: uuid.UUID,
) -> FileVersion:
    if expected_version < 1:
        raise ValueError("expected_version must be positive")

    fingerprint = hashlib.sha256(
        b"UPDATE\0"
        + file_id.bytes
        + b"\0"
        + str(expected_version).encode("ascii")
        + b"\0"
        + content
    ).hexdigest()

    try:
        lock_journal(session)

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )
        if existing_request is not None:
            if existing_request.request_fingerprint != fingerprint:
                raise ValueError("Request ID reused with different content")
            if existing_request.action != ChangeAction.UPDATE:
                raise ValueError("Request ID belongs to another operation")

            version = (
                session.query(FileVersion)
                .filter_by(
                    file_id=existing_request.file_id,
                    version_number=existing_request.version_number,
                )
                .one()
            )
            session.rollback()
            return version

        saved_file = (
            session.query(File)
            .filter(File.id == file_id)
            .with_for_update()
            .one_or_none()
        )
        if saved_file is None or saved_file.deleted_at is not None:
            raise FileNotFoundError("File not found")

        require_latest_version(session, file_id, expected_version)

        content_hash = storage.save(content)

        version = FileVersion(
            file_id=file_id,
            version_number=expected_version + 1,
            content_hash=content_hash,
            size=len(content),
        )
        session.add(version)

        session.add(
            ChangeJournal(
                file_id=file_id,
                action=ChangeAction.UPDATE,
                path=saved_file.key,
                version_number=version.version_number,
                request_id=request_id,
                request_fingerprint=fingerprint,
            )
        )

        session.commit()
        return version

    except Exception:
        session.rollback()
        raise


def delete_file(
    session: Session,
    file_id: uuid.UUID,
    expected_version: int,
    request_id: uuid.UUID,
) -> None:
    if expected_version < 1:
        raise ValueError("Expected version must be positive")

    fingerprint = hashlib.sha256(
        b"DELETE\0" + file_id.bytes + b"\0" + str(expected_version).encode("ascii")
    ).hexdigest()

    try:
        lock_journal(session)

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.request_fingerprint != fingerprint
                or existing_request.action != ChangeAction.DELETE
            ):
                raise ValueError("Request ID reused with different operation")

            session.rollback()
            return

        saved_file = (
            session.query(File).filter_by(id=file_id).with_for_update().one_or_none()
        )

        if saved_file is None or saved_file.deleted_at is not None:
            raise FileNotFoundError("File not found")

        require_latest_version(session, file_id, expected_version)

        saved_file.deleted_at = datetime.now(timezone.utc)

        session.add(
            ChangeJournal(
                file_id=file_id,
                action=ChangeAction.DELETE,
                path=saved_file.key,
                version_number=expected_version,
                request_id=request_id,
                request_fingerprint=fingerprint,
            )
        )

        session.commit()

    except Exception:
        session.rollback()
        raise


def rename_file(
    session: Session,
    file_id: uuid.UUID,
    expected_key: str,
    new_key: str,
    expected_version: int,
    request_id: uuid.UUID,
) -> str:
    if expected_version < 1:
        raise ValueError("Expected version must be positive")

    if not is_valid_file_key(new_key):
        raise ValueError("Invalid destination path")
    fingerprint = hashlib.sha256(
        b"RENAME\0"
        + file_id.bytes
        + b"\0"
        + expected_key.encode("utf-8")
        + b"\0"
        + new_key.encode("utf-8")
        + b"\0"
        + str(expected_version).encode("ascii")
    ).hexdigest()

    try:
        lock_journal(session)

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.request_fingerprint != fingerprint
                or existing_request.action != ChangeAction.RENAME
            ):
                raise ValueError("Request ID reused with different operation")

            original_result = existing_request.path
            session.rollback()
            return original_result

        saved_file = (
            session.query(File).filter_by(id=file_id).with_for_update().one_or_none()
        )

        if saved_file is None or saved_file.deleted_at is not None:
            raise FileNotFoundError("File not found")

        if saved_file.key != expected_key:
            raise FileVersionConflictError("File path has changed since the last sync")

        latest = require_latest_version(session, file_id, expected_version)

        if new_key == expected_key:
            raise ValueError("New path must be different")

        saved_file.key = new_key

        session.add(
            ChangeJournal(
                file_id=file_id,
                action=ChangeAction.RENAME,
                path=new_key,
                old_path=expected_key,
                version_number=latest.version_number,
                request_id=request_id,
                request_fingerprint=fingerprint,
            )
        )

        session.commit()
        return new_key

    except Exception:
        session.rollback()
        raise
