import time

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

from backend.db.database import SessionLocal
from backend.db.models import ChangeJournal, OutboxEvent, OutboxStatus


def process_pending_events(session: Session) -> int:
    """Publish one pending outbox event."""
    earlier_event = aliased(OutboxEvent)
    try:
        acquired = session.execute(
            text("SELECT pg_try_advisory_xact_lock(742002)")
        ).scalar_one()

        if not acquired:
            session.rollback()
            return 0

        event = session.execute(
            select(OutboxEvent)
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
        ).scalar_one_or_none()
        if event is None:
            session.rollback()
            return 0

        journal_entry = ChangeJournal(
            file_id=event.file_id,
            action=event.action,
            path=event.payload["path"],
            old_path=event.payload["old_path"],
            version_number=event.payload["version_number"],
            request_id=event.request_id,
            request_fingerprint=event.payload["request_fingerprint"],
            outbox_event_id=event.id,
        )

        session.add(journal_entry)

        event.status = OutboxStatus.COMPLETED

        session.commit()
        return 1

    except Exception:
        session.rollback()
        raise


def main() -> None:
    print("Outbox worker started. Press Ctrl+C to stop.")

    try:
        while True:
            try:
                with SessionLocal() as session:
                    processed = process_pending_events(session)
            except OperationalError as exc:
                print(f"Database temporarily unavailable: {exc}")
                time.sleep(5)
                continue

            if processed == 0:
                time.sleep(1)

    except KeyboardInterrupt:
        print("\\nOutbox worker stopped.")


if __name__ == "__main__":
    main()
