import uuid

from sqlalchemy.orm import Session

from backend.db.models import (
    ChangeAction,
    File,
    OutboxEvent,
    OutboxStatus,
)


def enqueue_event(
    session: Session,
    request_id: uuid.UUID,
    file_id: uuid.UUID,
    action: ChangeAction,
    path: str,
    version_number: int,
    fingerprint: str,
    old_path: str | None = None,
) -> OutboxEvent:
    saved_file = session.get(File, file_id)

    if saved_file is None:
        raise ValueError("File not found when creating outbox event")

    saved_file.event_sequence += 1

    event = OutboxEvent(
        request_id=request_id,
        file_id=file_id,
        event_sequence=saved_file.event_sequence,
        action=action,
        payload={
            "path": path,
            "old_path": old_path,
            "version_number": version_number,
            "request_fingerprint": fingerprint,
        },
        status=OutboxStatus.PENDING,
    )

    session.add(event)
    return event
