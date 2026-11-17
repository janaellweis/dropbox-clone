import hashlib
import uuid
from contextlib import nullcontext
from typing import BinaryIO

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.core.exceptions import (
    FileAlreadyExistsError,
    FileContentMissingError,
    FileRecordNotFoundError,
    InvalidFilePathError,
    RequestConflictError,
)
from backend.db.models import ChangeAction, File, FileVersion, OutboxEvent
from backend.services.chunks import (
    attach_version_chunks,
    get_version_chunk_hashes,
    get_version_chunk_manifest,
    register_manifest_chunks,
    stage_upload_chunks,
    validate_chunk_manifest,
)
from backend.services.file_rules import is_valid_file_key
from backend.services.gc import protect_chunks
from backend.services.outbox import enqueue_event
from backend.storage.local import LocalBlobStorage
from shared.chunking import chunk_file


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
    content: BinaryIO | None,
    request_id: uuid.UUID,
    *,
    chunk_hashes: list[str] | None = None,
    expected_hash: str | None = None,
    expected_size: int | None = None,
) -> File:
    if not is_valid_file_key(key):
        raise InvalidFilePathError("Invalid file path")

    fingerprint_prefix = b"CREATE\0" + key.encode("utf-8") + b"\0"

    if chunk_hashes is None:
        if content is None:
            raise ValueError("File content is required")

        upload = storage.stage_upload(content, fingerprint_prefix)
    else:
        if expected_hash is None or expected_size is None:
            raise ValueError("Manifest metadata is required")

        validate_chunk_manifest(storage, chunk_hashes, expected_hash, expected_size)

        hasher = hashlib.sha256()
        hasher.update(fingerprint_prefix)

        for data in storage.stream_chunks(chunk_hashes):
            hasher.update(data)

        upload = nullcontext((None, expected_hash, expected_size, hasher.hexdigest()))

    with upload as (temp_path, content_hash, size, fingerprint):
        if chunk_hashes is None:
            chunk_hashes = stage_upload_chunks(session, storage, chunk_file(temp_path))
        existing_file = find_existing_create(session, request_id, fingerprint)

        if existing_file is not None:
            return existing_file

        try:
            protect_chunks(session, storage, chunk_hashes)
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
            session.flush()

            register_manifest_chunks(session, storage, chunk_hashes)
            attach_version_chunks(session, version.id, chunk_hashes)

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
) -> tuple[list[str], str]:
    saved_file, version = get_file_metadata(session, file_id)

    chunk_hashes = get_version_chunk_hashes(session, version.id)

    if version.size > 0 and not chunk_hashes:
        raise FileContentMissingError("File chunk manifest missing")

    if any(not storage.exists(h) for h in chunk_hashes):
        raise FileContentMissingError("File chunk missing from storage")

    return chunk_hashes, saved_file.key


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
) -> tuple[list[str], str]:
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

    chunk_hashes = get_version_chunk_hashes(session, version.id)

    if version.size > 0 and not chunk_hashes:
        raise FileContentMissingError("File chunk manifest missing")

    if any(not storage.exists(h) for h in chunk_hashes):
        raise FileContentMissingError("File chunk missing from storage")

    return chunk_hashes, saved_file.key


def get_file_version_manifest(
    session: Session, file_id: uuid.UUID, version_number: int
) -> dict[str, object]:
    # File is used only for the existing active-file access policy. All content
    # identity comes from the requested immutable version, never the latest one.
    version = (
        session.query(FileVersion)
        .join(File, File.id == FileVersion.file_id)
        .filter(
            File.id == file_id,
            File.deleted_at.is_(None),
            FileVersion.version_number == version_number,
        )
        .one_or_none()
    )
    if version is None:
        raise FileRecordNotFoundError("File version not found")

    chunks = get_version_chunk_manifest(session, version.id)
    if sum(size for _, size in chunks) != version.size:
        raise FileContentMissingError("File chunk manifest size mismatch")
    return {
        "file_id": version.file_id,
        "version": version.version_number,
        "content_hash": version.content_hash,
        "size": version.size,
        "chunks": [{"hash": chunk_hash, "size": size} for chunk_hash, size in chunks],
    }
