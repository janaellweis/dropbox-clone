from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.db.models import ChangeJournal

PARTITION_COUNT = 4


def get_updates(
    session: Session,
    checkpoints: dict[int, int],
    limit: int = 100,
) -> list[ChangeJournal]:
    if set(checkpoints) != set(range(PARTITION_COUNT)):
        raise ValueError("Checkpoints must cover all four partitions")

    if any(position < 0 for position in checkpoints.values()):
        raise ValueError("Checkpoint cannot be negative")

    if not 1 <= limit <= 1000:
        raise ValueError("Limit must be between 1 and 1000")

    conditions = [
        and_(
            ChangeJournal.partition_id == partition_id,
            ChangeJournal.partition_position > position,
        )
        for partition_id, position in checkpoints.items()
    ]

    return list(
        session.scalars(
            select(ChangeJournal)
            .where(or_(*conditions))
            .order_by(
                ChangeJournal.partition_id,
                ChangeJournal.partition_position,
            )
            .limit(limit + 1)
        ).all()
    )
