import hashlib
import uuid
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    FileAlreadyExistsError,
    FileRecordNotFoundError,
    InvalidFilePathError,
    InvalidFileVersionError,
    RequestConflictError,
)
from backend.db.models import (
    ChangeAction,
    File,
    FileVersion,
    OutboxEvent,
)
from backend.services.file_rules import (
    FileVersionConflictError,
    is_valid_file_key,
    require_latest_version,
)
from backend.services.outbox import enqueue_event
from backend.storage.local import LocalBlobStorage


def update_file(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
    content: BinaryIO,
    expected_version: int,
    request_id: uuid.UUID,
) -> FileVersion:
    if expected_version < 1:
        raise InvalidFileVersionError("expected_version must be positive")

    fingerprint_prefix = (
        b"UPDATE\0"
        + file_id.bytes
        + b"\0"
        + str(expected_version).encode("ascii")
        + b"\0"
    )

    content_hash, size, fingerprint = storage.save(
        content,
        fingerprint_prefix,
    )
    try:
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if existing_request.payload["request_fingerprint"] != fingerprint:
                raise RequestConflictError("Request ID reused with different content")

            if existing_request.action != ChangeAction.UPDATE:
                raise RequestConflictError("Request ID belongs to another operation")

            version = (
                session.query(FileVersion)
                .filter_by(
                    file_id=existing_request.file_id,
                    version_number=existing_request.payload["version_number"],
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
        if saved_file is None:
            raise FileRecordNotFoundError("File not found")

        # The request may have committed while waiting for the file lock.
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.payload["request_fingerprint"] != fingerprint
                or existing_request.action != ChangeAction.UPDATE
            ):
                raise RequestConflictError("Request ID reused with different operation")

            version = (
                session.query(FileVersion)
                .filter_by(
                    file_id=existing_request.file_id,
                    version_number=existing_request.payload["version_number"],
                )
                .one()
            )
            session.rollback()
            return version

        require_latest_version(session, file_id, expected_version)

        version = FileVersion(
            file_id=file_id,
            version_number=expected_version + 1,
            content_hash=content_hash,
            size=size,
        )
        session.add(version)

        enqueue_event(
            session=session,
            request_id=request_id,
            file_id=file_id,
            action=ChangeAction.UPDATE,
            path=saved_file.key,
            version_number=version.version_number,
            fingerprint=fingerprint,
        )

        session.commit()
        return version

    except IntegrityError as exc:
        session.rollback()

        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )
        if existing_request is not None:
            if (
                existing_request.action != ChangeAction.UPDATE
                or existing_request.payload["request_fingerprint"] != fingerprint
            ):
                raise RequestConflictError(
                    "Request ID reused with different operation"
                ) from exc

            return (
                session.query(FileVersion)
                .filter_by(
                    file_id=existing_request.file_id,
                    version_number=existing_request.payload["version_number"],
                )
                .one()
            )

        raise

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
        raise InvalidFileVersionError("expected_version must be positive")

    fingerprint = hashlib.sha256(
        b"DELETE\0" + file_id.bytes + b"\0" + str(expected_version).encode("ascii")
    ).hexdigest()

    try:
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.payload["request_fingerprint"] != fingerprint
                or existing_request.action != ChangeAction.DELETE
            ):
                raise RequestConflictError("Request ID reused with different operation")

            session.rollback()
            return

        saved_file = (
            session.query(File)
            .execution_options(include_deleted=True)
            .filter_by(id=file_id)
            .with_for_update()
            .one_or_none()
        )

        if saved_file is None:
            raise FileRecordNotFoundError("File not found")

        # A concurrent retry may have committed while waiting for the lock.
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.payload["request_fingerprint"] != fingerprint
                or existing_request.action != ChangeAction.DELETE
            ):
                raise RequestConflictError("Request ID reused with different operation")

            session.rollback()
            return

        if saved_file.deleted_at is not None:
            raise FileRecordNotFoundError("File already deleted")

        require_latest_version(session, file_id, expected_version)

        saved_file.deleted_at = datetime.now(timezone.utc)

        enqueue_event(
            session=session,
            request_id=request_id,
            file_id=file_id,
            action=ChangeAction.DELETE,
            path=saved_file.key,
            version_number=expected_version,
            fingerprint=fingerprint,
        )

        session.commit()

    except IntegrityError as exc:
        session.rollback()

        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )
        if existing_request is not None:
            if (
                existing_request.action != ChangeAction.DELETE
                or existing_request.payload["request_fingerprint"] != fingerprint
            ):
                raise RequestConflictError(
                    "Request ID reused with different operation"
                ) from exc

            return

        raise

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
        raise InvalidFileVersionError("Expected version must be positive")

    if not is_valid_file_key(new_key):
        raise InvalidFilePathError("Invalid destination path")
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
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.payload["request_fingerprint"] != fingerprint
                or existing_request.action != ChangeAction.RENAME
            ):
                raise RequestConflictError("Request ID reused with different operation")

            original_result = existing_request.payload["path"]
            session.rollback()
            return original_result

        saved_file = (
            session.query(File).filter_by(id=file_id).with_for_update().one_or_none()
        )

        if saved_file is None:
            raise FileRecordNotFoundError("File not found")

        # A concurrent retry may have committed while waiting for the lock.
        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.payload["request_fingerprint"] != fingerprint
                or existing_request.action != ChangeAction.RENAME
            ):
                raise RequestConflictError("Request ID reused with different operation")

            original_result = existing_request.payload["path"]
            session.rollback()
            return original_result

        if saved_file.key != expected_key:
            raise FileVersionConflictError("File path has changed since the last sync")

        latest = require_latest_version(session, file_id, expected_version)

        if new_key == expected_key:
            raise RequestConflictError("New path must be different")

        saved_file.key = new_key

        enqueue_event(
            session=session,
            request_id=request_id,
            file_id=file_id,
            action=ChangeAction.RENAME,
            path=new_key,
            old_path=expected_key,
            version_number=latest.version_number,
            fingerprint=fingerprint,
        )

        session.commit()
        return new_key
    except IntegrityError as exc:
        session.rollback()

        constraint_name = getattr(
            getattr(exc.orig, "diag", None),
            "constraint_name",
            None,
        )

        if constraint_name == "uq_files_active_key":
            raise FileAlreadyExistsError("Destination path already exists") from exc

        existing_request = (
            session.query(OutboxEvent).filter_by(request_id=request_id).first()
        )
        if existing_request is not None:
            if (
                existing_request.action != ChangeAction.RENAME
                or existing_request.payload["request_fingerprint"] != fingerprint
            ):
                raise RequestConflictError(
                    "Request ID reused with different operation"
                ) from exc

            return existing_request.payload["path"]

        raise

    except Exception:
        session.rollback()
        raise
