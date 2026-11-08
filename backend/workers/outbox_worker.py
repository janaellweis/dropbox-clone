
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased
from uuid6 import uuid7

from backend.db.models import ChangeJournal, OutboxEvent, OutboxStatus


@dataclass(frozen=True)
class ClaimedBatch:
    claim_id: UUID
    event_ids: tuple[UUID, ...]


def claim_batch(
    session: Session,
    batch_size: int = 100,
    lease_seconds: int = 60,
) -> ClaimedBatch | None:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be positive")

    claim_id = uuid7()
    now = session.scalar(select(func.clock_timestamp()))
    earlier = aliased(OutboxEvent)

    try:
        events = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.status == OutboxStatus.PENDING)
            .where(
                or_(
                    OutboxEvent.claimed_until.is_(None),
                    OutboxEvent.claimed_until <= now,
                )
            )
            .where(
                ~select(earlier.id)
                .where(earlier.file_id == OutboxEvent.file_id)
                .where(
                    earlier.event_sequence < OutboxEvent.event_sequence
                )
                .where(earlier.status != OutboxStatus.COMPLETED)
                .exists()
            )
            .order_by(OutboxEvent.created_at, OutboxEvent.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True, of=OutboxEvent)
        ).all()

        if not events:
            session.rollback()
            return None

        for event in events:
            event.claimed_by = claim_id
            event.claimed_until = now + timedelta(seconds=lease_seconds)

        event_ids = tuple(event.id for event in events)
        session.commit()

        return ClaimedBatch(
            claim_id=claim_id,
            event_ids=event_ids,
        )

    except Exception:
        session.rollback()
        raise

PUBLICATION_LOCK_ID = 742002


def try_acquire_publication_lock(session: Session) -> bool:
    return bool(
        session.scalar(
            select(func.pg_try_advisory_xact_lock(PUBLICATION_LOCK_ID))
        )
    )



def publish_batch(session: Session, batch: ClaimedBatch) -> int:
    try:
        if not try_acquire_publication_lock(session):
            session.rollback()
            return 0

        events = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.id.in_(batch.event_ids))
            .order_by(OutboxEvent.id)
            .with_for_update(nowait=True)
        ).all()

        now = session.scalar(select(func.clock_timestamp()))

        if len(events) != len(batch.event_ids):
            session.rollback()
            return 0

        for event in events:
            if (
                event.status != OutboxStatus.PENDING
                or event.claimed_by != batch.claim_id
                or event.claimed_until is None
                or event.claimed_until <= now
            ):
                session.rollback()
                return 0

        for event in events:
            session.add(
                ChangeJournal(
                    file_id=event.file_id,
                    action=event.action,
                    path=event.payload["path"],
                    old_path=event.payload.get("old_path"),
                    version_number=event.payload.get("version_number"),
                    request_id=event.request_id,
                    request_fingerprint=event.payload["request_fingerprint"],
                    outbox_event_id=event.id,
                )
            )

            event.status = OutboxStatus.COMPLETED
            event.processed_at = now
            event.claimed_by = None
            event.claimed_until = None

        session.commit()
        return len(events)

    except OperationalError as exc:
        session.rollback()

        # PostgreSQL: lock_not_available
        if getattr(exc.orig, "pgcode", None) == "55P03" or getattr(
            exc.orig, "sqlstate", None
        ) == "55P03":
            return 0

        raise

    except Exception:
        session.rollback()
        raise

def process_event_batch(session: Session, batch_size: int = 100) -> int:
    batch = claim_batch(session, batch_size=batch_size)

    if batch is None:
        return 0

    return publish_batch(session, batch)