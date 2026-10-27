import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, text

from backend.db.models import ChangeAction, ChangeJournal
from backend.services.files import create_file
from backend.services.journal import get_updates
from backend.storage.local import LocalBlobStorage


def test_journal_checkpoint_does_not_skip_concurrent_writes(
    db_session_factory, tmp_path
):
    storage = LocalBlobStorage(tmp_path)
    first_key = f"journal-concurrent-first-{uuid.uuid4()}.txt"
    second_key = f"journal-concurrent-second-{uuid.uuid4()}.txt"

    writer_has_lock = threading.Event()
    release_writer = threading.Event()

    def first_writer():
        with db_session_factory() as session:
            session.execute(text("SELECT pg_advisory_xact_lock(742001)"))
            writer_has_lock.set()

            if not release_writer.wait(timeout=5):
                raise TimeoutError("Timed out waiting to release first writer")

            create_file(
                session,
                storage,
                first_key,
                b"First",
                uuid.uuid4(),
            )

    def second_writer():
        with db_session_factory() as session:
            create_file(
                session,
                storage,
                second_key,
                b"Second",
                uuid.uuid4(),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_writer)

        assert writer_has_lock.wait(timeout=5)

        second_future = executor.submit(second_writer)

        # Release the first transaction before the second can commit.
        release_writer.set()

        first_future.result(timeout=10)
        second_future.result(timeout=10)

    with db_session_factory() as session:
        events = session.scalars(
            select(ChangeJournal)
            .where(
                ChangeJournal.path.in_([first_key, second_key]),
                ChangeJournal.action == ChangeAction.CREATE,
            )
            .order_by(ChangeJournal.id)
        ).all()

        assert len(events) == 2
        assert [event.path for event in events] == [first_key, second_key]

        # The client can safely advance from one checkpoint to the next.
        first_page = get_updates(session, since=events[0].id - 1, limit=1)
        second_page = get_updates(session, since=events[0].id, limit=1)

        assert first_page[0].id == events[0].id
        assert second_page[0].id == events[1].id
