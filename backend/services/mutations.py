import hashlib
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    FileAlreadyExistsError,
    FileRecordNotFoundError,
    FileVersionConflictError,
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
from backend.services.chunks import (
    attach_version_chunks,
    register_manifest_chunks,
    stage_upload_chunks,
    validate_protected_manifest,
)
from backend.services.file_rules import (
    is_valid_file_key,
    require_latest_version,
)
from backend.services.gc import protect_chunks
from backend.services.outbox import enqueue_event
from backend.storage.local import LocalBlobStorage
from shared.chunking import chunk_file


def validate_existing_request(
    existing_request: OutboxEvent,
    action: ChangeAction,
    fingerprint: str,
    *,
    cause: Exception | None = None,
) -> None:
    if (
        existing_request.action != action
        or existing_request.payload["request_fingerprint"] != fingerprint
    ):
        raise RequestConflictError(
            "Request ID reused with different operation"
        ) from cause


def update_file(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
    content: BinaryIO | None,
    expected_version: int,
    request_id: uuid.UUID,
    *,
    chunk_hashes: list[str] | None = None,
    expected_hash: str | None = None,
    expected_size: int | None = None,
) -> FileVersion:
    fingerprint = None

    try:
        if expected_version < 1:
            raise InvalidFileVersionError("expected_version must be positive")

        fingerprint_prefix = (
            b"UPDATE\0"
            + file_id.bytes
            + b"\0"
            + str(expected_version).encode("ascii")
            + b"\0"
        )

        if chunk_hashes is None and content is None:
            raise ValueError("File content is required")

        if chunk_hashes is not None and (
            expected_hash is None or expected_size is None
        ):
            raise ValueError("Manifest metadata is required")

        if chunk_hashes is None:
            upload = storage.stage_upload(content, fingerprint_prefix)
        else:
            validate_protected_manifest(
                session, storage, chunk_hashes, expected_hash, expected_size
            )

            hasher = hashlib.sha256()
            hasher.update(fingerprint_prefix)

            for data in storage.stream_chunks(chunk_hashes):
                hasher.update(data)

            upload = nullcontext(
                (None, expected_hash, expected_size, hasher.hexdigest())
            )

        with upload as (temp_path, content_hash, size, fingerprint):
            if chunk_hashes is None:
                chunk_hashes = stage_upload_chunks(
                    session, storage, chunk_file(temp_path)
                )

            protect_chunks(session, storage, chunk_hashes)

            existing_request = (
                session.query(OutboxEvent).filter_by(request_id=request_id).first()
            )

            if existing_request is not None:
                validate_existing_request(
                    existing_request, ChangeAction.UPDATE, fingerprint
                )

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
                validate_existing_request(
                    existing_request, ChangeAction.UPDATE, fingerprint
                )

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
            session.flush()

            register_manifest_chunks(session, storage, chunk_hashes)
            attach_version_chunks(session, version.id, chunk_hashes)

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

        if fingerprint is not None:
            existing_request = (
                session.query(OutboxEvent).filter_by(request_id=request_id).first()
            )

            if existing_request is not None:
                validate_existing_request(
                    existing_request,
                    ChangeAction.UPDATE,
                    fingerprint,
                    cause=exc,
                )

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
            validate_existing_request(
                existing_request, ChangeAction.DELETE, fingerprint
            )

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
            validate_existing_request(
                existing_request, ChangeAction.DELETE, fingerprint
            )

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
            validate_existing_request(
                existing_request, ChangeAction.DELETE, fingerprint, cause=exc
            )

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
            validate_existing_request(
                existing_request, ChangeAction.RENAME, fingerprint
            )

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
            validate_existing_request(
                existing_request, ChangeAction.RENAME, fingerprint
            )

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
            validate_existing_request(
                existing_request, ChangeAction.RENAME, fingerprint, cause=exc
            )

            return existing_request.payload["path"]

        raise

    except Exception:
        session.rollback()
        raise


def restore_file_version(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
    source_version: int,
    expected_version: int,
    request_id: uuid.UUID,
) -> FileVersion:
    from backend.services.chunks import get_version_chunk_hashes

    if source_version < 1 or expected_version < 1:
        raise InvalidFileVersionError("Version numbers must be positive")

    fingerprint = hashlib.sha256(
        b"RESTORE\0"
        + file_id.bytes
        + b"\0"
        + str(source_version).encode("ascii")
        + b"\0"
        + str(expected_version).encode("ascii")
    ).hexdigest()

    def previous_result() -> FileVersion | None:
        existing = session.query(OutboxEvent).filter_by(request_id=request_id).first()

        if existing is None:
            return None

        validate_existing_request(existing, ChangeAction.UPDATE, fingerprint)

        if existing.file_id != file_id:
            raise RequestConflictError("Request ID reused for a different file")

        version = (
            session.query(FileVersion)
            .filter_by(
                file_id=file_id,
                version_number=existing.payload["version_number"],
            )
            .one()
        )
        return version

    try:
        existing = previous_result()
        if existing is not None:
            session.rollback()
            return existing

        saved_file = (
            session.query(File)
            .filter(File.id == file_id)
            .with_for_update()
            .one_or_none()
        )

        if saved_file is None:
            raise FileRecordNotFoundError("File not found")

        # Another request may have committed while we waited for the lock.
        existing = previous_result()
        if existing is not None:
            session.rollback()
            return existing

        require_latest_version(session, file_id, expected_version)

        historical = (
            session.query(FileVersion)
            .filter_by(
                file_id=file_id,
                version_number=source_version,
            )
            .one_or_none()
        )

        if historical is None:
            raise FileRecordNotFoundError("Historical version not found")

        hashes = get_version_chunk_hashes(session, historical.id)

        from backend.services.chunks import validate_protected_manifest

        validate_protected_manifest(
            session,
            storage,
            hashes,
            historical.content_hash,
            historical.size,
        )

        restored = FileVersion(
            file_id=file_id,
            version_number=expected_version + 1,
            content_hash=historical.content_hash,
            size=historical.size,
        )
        session.add(restored)
        session.flush()

        attach_version_chunks(session, restored.id, hashes)

        enqueue_event(
            session=session,
            request_id=request_id,
            file_id=file_id,
            action=ChangeAction.UPDATE,
            path=saved_file.key,
            version_number=restored.version_number,
            fingerprint=fingerprint,
        )

        session.commit()
        return restored

    except IntegrityError as exc:
        session.rollback()
        existing = previous_result()
        if existing is not None:
            return existing
        raise exc
    except Exception:
        session.rollback()
        raise
