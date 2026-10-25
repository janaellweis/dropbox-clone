import hashlib
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.models import ChangeAction, ChangeJournal, File, FileVersion
from backend.storage.local import LocalBlobStorage


def create_file(
    session: Session,
    storage: LocalBlobStorage,
    key: str,
    content: bytes,
    request_id: uuid.UUID,
) -> File:
    fingerprint = hashlib.sha256(key.encode("utf-8") + b"\0" + content).hexdigest()

    existing_request = (
        session.query(ChangeJournal).filter_by(request_id=request_id).first()
    )

    if existing_request is not None:
        if existing_request.request_fingerprint != fingerprint:
            raise ValueError("Request ID reused with different content")

        return session.get(File, existing_request.file_id)

    content_hash = storage.save(content)

    try:
        file = File(key=key)

        session.add(file)
        session.flush()

        version = FileVersion(
            file_id=file.id,
            version_number=1,
            content_hash=content_hash,
            size=len(content),
        )
        session.add(version)

        journal = ChangeJournal(
            file_id=file.id,
            action=ChangeAction.CREATE,
            path=key,
            version_number=1,
            request_id=request_id,
            request_fingerprint=fingerprint,
        )
        session.add(journal)

        session.commit()
        return file

    except IntegrityError:
        session.rollback()

        existing_request = (
            session.query(ChangeJournal).filter_by(request_id=request_id).first()
        )

        if existing_request is not None:
            if existing_request.request_fingerprint != fingerprint:
                raise ValueError("Request ID reused with different content")

            return session.get(File, existing_request.file_id)

        # The conflict was caused by something else,
        # such as a duplicate file path.
        raise

    except Exception:
        session.rollback()
        raise
