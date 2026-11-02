import io

from sqlalchemy import select
from uuid6 import uuid7

from backend.db.models import ChangeAction, ChangeJournal
from backend.services.files import create_file
from backend.services.journal import get_updates
from backend.storage.local import LocalBlobStorage


def test_journal_checkpoint_returns_consecutive_published_events(
    db_session_factory, tmp_path, publish_outbox_events
):
    storage = LocalBlobStorage(tmp_path)

    with db_session_factory() as session:
        for index in range(2):
            create_file(
                session,
                storage,
                f"journal-checkpoint-{index}.txt",
                io.BytesIO(b"content"),
                uuid7(),
            )

    assert publish_outbox_events() == 2

    with db_session_factory() as session:
        events = session.scalars(
            select(ChangeJournal)
            .where(ChangeJournal.action == ChangeAction.CREATE)
            .order_by(ChangeJournal.id)
        ).all()

        assert len(events) == 2

        first_page = get_updates(session, since=0, limit=1)
        second_page = get_updates(session, since=first_page[0].id, limit=1)

        assert [event.id for event in first_page] == [
            events[0].id,
            events[1].id,
        ]
        assert [event.id for event in second_page] == [events[1].id]
        assert get_updates(session, since=events[1].id, limit=1) == []
