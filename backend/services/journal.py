from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.db.models import ChangeJournal


def get_updates(
    session: Session,
    since: int,
    limit: int = 100,
) -> list[ChangeJournal]:
    if since < 0:
        raise ValueError("Checkpoint cannot be negative")

    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000")

    return list(
        session.scalars(
            select(ChangeJournal)
            .where(ChangeJournal.id > since)
            .order_by(ChangeJournal.id)
            .limit(limit + 1)
        ).all()
    )
