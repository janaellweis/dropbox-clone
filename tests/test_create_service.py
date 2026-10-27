import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError

from backend.db.models import ChangeAction, ChangeJournal, File, FileVersion
from backend.services.files import create_file
from backend.storage.local import LocalBlobStorage


def test_create_file(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    session = db_session_factory()

    key = f"test-{uuid.uuid4()}.txt"
    content = b"Hello from the service!"
    request_id = uuid.uuid4()

    try:
        file = create_file(
            session=session,
            storage=storage,
            key=key,
            content=content,
            request_id=request_id,
        )

        # 1. Verify the file record
        saved_file = session.get(File, file.id)

        assert saved_file is not None
        assert saved_file.key == key
        assert saved_file.deleted_at is None

        # 2. Verify version 1
        version = session.scalar(
            select(FileVersion).where(FileVersion.file_id == file.id)
        )

        assert version is not None
        assert version.version_number == 1
        assert version.size == len(content)

        # 3. Verify the blob
        assert storage.read(version.content_hash) == content

        # 4. Verify the journal entry
        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )

        assert journal is not None
        assert journal.file_id == file.id
        assert journal.action == ChangeAction.CREATE
        assert journal.version_number == 1

    finally:
        session.close()


def test_create_file_rejects_changed_request(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    session = db_session_factory()

    key = f"conflict-{uuid.uuid4()}.txt"
    request_id = uuid.uuid4()

    try:
        original_file = create_file(session, storage, key, b"Hello", request_id)

        with pytest.raises(
            ValueError, match="Request ID reused with different content"
        ):
            create_file(session, storage, key, b"Goodbye", request_id)

        # The original file should remain unchanged.
        versions = session.scalars(
            select(FileVersion).where(FileVersion.file_id == original_file.id)
        ).all()

        assert len(versions) == 1
        assert storage.read(versions[0].content_hash) == b"Hello"

    finally:
        session.close()


def test_create_file_rolls_back_on_failure(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    session = db_session_factory()

    key = f"rollback-{uuid.uuid4()}.txt"
    request_id = uuid.uuid4()

    def fail_journal_insert(mapper, connection, target):
        raise RuntimeError("Simulated journal failure")

    event.listen(ChangeJournal, "before_insert", fail_journal_insert)

    try:
        with pytest.raises(RuntimeError, match="Simulated journal failure"):
            create_file(
                session,
                storage,
                key,
                b"Rollback test!",
                request_id,
            )

        # No file should remain in PostgreSQL.
        saved_file = session.scalar(select(File).where(File.key == key))
        assert saved_file is None

        # No journal entry should remain.
        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )
        assert journal is None

    finally:
        event.remove(ChangeJournal, "before_insert", fail_journal_insert)
        session.close()


def test_create_file_concurrent_requests(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    key = f"concurrent-{uuid.uuid4()}.txt"

    def upload_file():
        session = db_session_factory()

        try:
            file = create_file(
                session,
                storage,
                key,
                b"Concurrent content",
                uuid.uuid4(),
            )
            return ("success", file.id)

        except IntegrityError:
            return ("conflict", None)

        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: upload_file(), range(2)))

    successes = [result for result in results if result[0] == "success"]
    conflicts = [result for result in results if result[0] == "conflict"]

    assert len(successes) == 1
    assert len(conflicts) == 1

    with db_session_factory() as session:
        files = session.scalars(select(File).where(File.key == key)).all()

        assert len(files) == 1


def test_create_file_concurrent_same_request(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)

    key = f"same-request-{uuid.uuid4()}.txt"
    content = b"Same upload!"
    request_id = uuid.uuid4()

    def upload_file():
        session = db_session_factory()

        try:
            file = create_file(
                session,
                storage,
                key,
                content,
                request_id,
            )
            return file.id
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        file_ids = list(executor.map(lambda _: upload_file(), range(2)))

    # Both requests should return the same file.
    assert file_ids[0] == file_ids[1]

    with db_session_factory() as session:
        files = session.scalars(select(File).where(File.key == key)).all()

        journals = session.scalars(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        ).all()

        versions = session.scalars(
            select(FileVersion).where(FileVersion.file_id == file_ids[0])
        ).all()

        assert len(files) == 1
        assert len(journals) == 1
        assert len(versions) == 1
