import hashlib
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import ChangeAction, ChangeJournal, File, FileVersion
from backend.services.file_rules import lock_journal
from backend.storage.local import LocalBlobStorage


def create_file(
    session: Session,
    storage: LocalBlobStorage,
    key: str,
    content: bytes,
    request_id: uuid.UUID,
) -> File:
    fingerprint = hashlib.sha256(
        b"CREATE\0" + key.encode("utf-8") + b"\0" + content
    ).hexdigest()

    existing_request = (
        session.query(ChangeJournal).filter_by(request_id=request_id).first()
    )

    if existing_request is not None:
        if (
            existing_request.request_fingerprint != fingerprint
            or existing_request.action != ChangeAction.CREATE
        ):
            raise ValueError("Request ID reused with different content")

        return session.get(File, existing_request.file_id)

    content_hash = storage.save(content)

    try:
        lock_journal(session)

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.request_fingerprint != fingerprint
                or existing_request.action != ChangeAction.CREATE
            ):
                raise ValueError("Request ID reused with different content")

            original_file_id = existing_request.file_id
            session.rollback()
            return session.get(File, original_file_id)

        saved_file = File(key=key)
        session.add(saved_file)
        session.flush()

        version = FileVersion(
            file_id=saved_file.id,
            version_number=1,
            content_hash=content_hash,
            size=len(content),
        )
        session.add(version)

        journal = ChangeJournal(
            file_id=saved_file.id,
            action=ChangeAction.CREATE,
            path=key,
            version_number=1,
            request_id=request_id,
            request_fingerprint=fingerprint,
        )
        session.add(journal)

        session.commit()
        return saved_file

    except IntegrityError:
        session.rollback()

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if (
                existing_request.request_fingerprint != fingerprint
                or existing_request.action != ChangeAction.CREATE
            ):
                raise ValueError("Request ID reused with different content")

            return session.get(File, existing_request.file_id)

        raise

    except Exception:
        session.rollback()
        raise


class FileContentMissingError(Exception):
    """File metadata exists, but its content is missing from storage."""


def get_file_content(
    session: Session,
    storage: LocalBlobStorage,
    file_id: uuid.UUID,
) -> tuple[bytes, str]:
    saved_file, version = get_file_metadata(session, file_id)

    try:
        content = storage.read(version.content_hash)
    except FileNotFoundError as exc:
        raise FileContentMissingError("File content missing from storage") from exc

    return content, saved_file.key


def get_file_metadata(
    session: Session,
    file_id: uuid.UUID,
) -> tuple[File, FileVersion]:
    saved_file = session.get(File, file_id)

    if saved_file is None or saved_file.deleted_at is not None:
        raise FileNotFoundError("File not found")

    latest_version = (
        session.query(FileVersion)
        .filter_by(file_id=file_id)
        .order_by(FileVersion.version_number.desc())
        .first()
    )

    if latest_version is None:
        raise FileNotFoundError("File version not found")

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
) -> tuple[bytes, str]:
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
        raise FileNotFoundError("File version not found")

    try:
        content = storage.read(version.content_hash)
    except FileNotFoundError as exc:
        raise FileContentMissingError("File content missing from storage") from exc

    return content, saved_file.key
