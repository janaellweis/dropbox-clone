import hashlib
import uuid

from sqlalchemy.orm import Session

from backend.db.models import (
    ChangeAction,
    File,
    OutboxEvent,
    OutboxStatus,
)

PARTITION_COUNT = 4


def get_partition_id(file_id: uuid.UUID) -> int:
    digest = hashlib.sha256(file_id.bytes).digest()
    return int.from_bytes(digest[:8], "big") % PARTITION_COUNT


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
        partition_id=get_partition_id(file_id),
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
