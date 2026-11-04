from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from backend.db.models import (
    ChangeJournal,
    OutboxEvent,
    OutboxStatus,
    PartitionState,
)


def process_pending_events(session: Session) -> int:
    """Publish one pending outbox event."""
    earlier_event = aliased(OutboxEvent)
    candidate_event = aliased(OutboxEvent)

    try:
        partition = session.scalar(
            select(PartitionState)
            .where(
                select(candidate_event.id)
                .where(candidate_event.partition_id == PartitionState.partition_id)
                .where(candidate_event.status == OutboxStatus.PENDING)
                .where(
                    ~select(earlier_event.id)
                    .where(earlier_event.file_id == candidate_event.file_id)
                    .where(
                        earlier_event.event_sequence < candidate_event.event_sequence
                    )
                    .where(earlier_event.status != OutboxStatus.COMPLETED)
                    .exists()
                )
                .exists()
            )
            .order_by(PartitionState.partition_id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )

        if partition is None:
            session.rollback()
            return 0

        # Select the next eligible event in the claimed partition.
        event = session.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.partition_id == partition.partition_id)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
            .where(
                ~select(earlier_event.id)
                .where(earlier_event.file_id == OutboxEvent.file_id)
                .where(earlier_event.event_sequence < OutboxEvent.event_sequence)
                .where(earlier_event.status != OutboxStatus.COMPLETED)
                .exists()
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(1)
            .with_for_update()
        )

        if event is None:
            session.rollback()
            return 0

        next_position = partition.last_position + 1

        journal_entry = ChangeJournal(
            file_id=event.file_id,
            partition_id=partition.partition_id,
            partition_position=next_position,
            action=event.action,
            path=event.payload["path"],
            old_path=event.payload["old_path"],
            version_number=event.payload["version_number"],
            request_id=event.request_id,
            request_fingerprint=event.payload["request_fingerprint"],
            outbox_event_id=event.id,
        )

        session.add(journal_entry)

        partition.last_position = next_position
        event.status = OutboxStatus.COMPLETED
        event.processed_at = datetime.now(timezone.utc)

        session.commit()
        return 1

    except Exception:
        session.rollback()
        raise


def process_event_batch(session: Session, batch_size: int = 100) -> int:
    """Publish up to batch_size pending events."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    processed = 0

    for _ in range(batch_size):
        if process_pending_events(session) == 0:
            break

        processed += 1

    return processed
