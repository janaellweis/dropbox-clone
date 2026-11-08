import uuid
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    FileAlreadyExistsError,
    FileContentMissingError,
    FileRecordNotFoundError,
    RequestConflictError,
    InvalidFilePathError,
)
from backend.db.models import ChangeAction, File, FileVersion, OutboxEvent
from backend.services.outbox import enqueue_event
from backend.storage.local import LocalBlobStorage
from backend.services.file_rules import is_valid_file_key

def find_existing_create(
    session: Session,
    request_id: uuid.UUID,
    fingerprint: str,
) -> File | None:
    existing_request = (
        session.query(OutboxEvent).filter_by(request_id=request_id).first()
    )

    if existing_request is None:
        return None

    if (
        existing_request.payload["request_fingerprint"] != fingerprint
        or existing_request.action != ChangeAction.CREATE
    ):
        raise RequestConflictError("Request ID reused with different content")

    return (
        session.query(File)
        .execution_options(include_deleted=True)
        .filter(File.id == existing_request.file_id)
        .one()
    )


def create_file(
    session: Session,
    storage: LocalBlobStorage,
    key: str,
    content: BinaryIO,
    request_id: uuid.UUID,
) -> File:
    if not is_valid_file_key(key):
        raise InvalidFilePathError("Invalid file path")

    fingerprint_prefix = b"CREATE\0" + key.encode("utf-8") + b"\0"

    content_hash, size, fingerprint = storage.save(
        content,
        fingerprint_prefix,
    )

    existing_file = find_existing_create(session, request_id, fingerprint)

    if existing_file is not None:
        return existing_file

    try:
        saved_file = File(key=key)
        session.add(saved_file)
        session.flush()

        version = FileVersion(
            file_id=saved_file.id,
            version_number=1,
            content_hash=content_hash,
            size=size,
        )
        session.add(version)

        enqueue_event(
            session=session,
            request_id=request_id,
            file_id=saved_file.id,
            action=ChangeAction.CREATE,
            path=key,
            version_number=1,
            fingerprint=fingerprint,
        )

        session.commit()
        return saved_file

    except IntegrityError as exc:
        session.rollback()

        existing_file = find_existing_create(session, request_id, fingerprint)

        if existing_file is not None:
            return existing_file
        constraint_name = getattr(
            getattr(exc.orig, "diag", None),
            "constraint_name",
            None,
        )

        if constraint_name == "uq_files_active_key":
            raise FileAlreadyExistsError("File path already exists") from exc

        raise

    except Exception:
        session.rollback()
        raise


def get_file_content(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
) -> tuple[str, str]:
    saved_file, version = get_file_metadata(session, file_id)

    if not storage.exists(version.content_hash):
        raise FileContentMissingError("File content missing from storage")

    return version.content_hash, saved_file.key


def get_file_metadata(
    session: Session,
    file_id: uuid.UUID,
) -> tuple[File, FileVersion]:
    saved_file = session.query(File).filter(File.id == file_id).one_or_none()

    if saved_file is None:
        raise FileRecordNotFoundError("File not found")

    latest_version = (
        session.query(FileVersion)
        .filter_by(file_id=file_id)
        .order_by(FileVersion.version_number.desc())
        .limit(1)
        .one()
    )

    return saved_file, latest_version


def list_file_versions(
    session: Session,
    file_id: uuid.UUID,
) -> list[FileVersion]:
    get_file_metadata(session, file_id)

    return (
        session.query(FileVersion)
        .filter_by(file_id=file_id)
        .order_by(FileVersion.version_number.desc())
        .all()
    )


def get_file_version_content(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
    version_number: int,
) -> tuple[str, str]:
    saved_file, _ = get_file_metadata(session, file_id)

    version = (
        session.query(FileVersion)
        .filter_by(
            file_id=file_id,
            version_number=version_number,
        )
        .one_or_none()
    )

    if version is None:
        raise FileRecordNotFoundError("File version not found")

    if not storage.exists(version.content_hash):
        raise FileContentMissingError("File content missing from storage")

    return version.content_hash, saved_file.key
