import uuid

import pytest
from sqlalchemy import event, select

from backend.db.models import ChangeAction, ChangeJournal, File, FileVersion
from backend.services.file_rules import FileVersionConflictError
from backend.services.files import create_file
from backend.services.mutations import delete_file, rename_file, update_file
from backend.storage.local import LocalBlobStorage


def test_delete_file_preserves_history(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"delete-history-{uuid.uuid4()}.txt",
            b"Original content",
            uuid.uuid4(),
        )
        file_id = saved_file.id

        delete_file(
            session=session,
            file_id=file_id,
            expected_version=1,
            request_id=request_id,
        )

        deleted_file = session.get(File, file_id)

        assert deleted_file is not None
        assert deleted_file.deleted_at is not None

        versions = session.scalars(
            select(FileVersion).where(FileVersion.file_id == file_id)
        ).all()

        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert storage.read(versions[0].content_hash) == b"Original content"

        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )

        assert journal is not None
        assert journal.action == ChangeAction.DELETE
        assert journal.file_id == file_id
        assert journal.version_number == 1


def test_delete_file_stale_version_has_no_effect(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"delete-stale-{uuid.uuid4()}.txt",
            b"Hello",
            uuid.uuid4(),
        )
        file_id = saved_file.id

        update_file(
            session,
            storage,
            file_id,
            b"Updated",
            1,
            uuid.uuid4(),
        )

        with pytest.raises(FileVersionConflictError):
            delete_file(session, file_id, 1, request_id)

        saved_file = session.get(File, file_id)

        assert saved_file.deleted_at is None

        events = session.scalars(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        ).all()

        assert events == []


def test_delete_file_rolls_back_on_journal_failure(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"delete-rollback-{uuid.uuid4()}.txt",
            b"Hello",
            uuid.uuid4(),
        )
        file_id = saved_file.id

        def fail_delete_journal(mapper, connection, target):
            if target.action == ChangeAction.DELETE:
                raise RuntimeError("Simulated delete journal failure")

        event.listen(
            ChangeJournal,
            "before_insert",
            fail_delete_journal,
        )

        try:
            with pytest.raises(
                RuntimeError,
                match="Simulated delete journal failure",
            ):
                delete_file(session, file_id, 1, request_id)
        finally:
            event.remove(
                ChangeJournal,
                "before_insert",
                fail_delete_journal,
            )

        saved_file = session.get(File, file_id)
        assert saved_file.deleted_at is None

        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )
        assert journal is None


def test_rename_preserves_version_and_records_journal(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"rename-original-{uuid.uuid4()}.txt",
            b"Original content",
            uuid.uuid4(),
        )
        file_id = saved_file.id
        original_key = saved_file.key
        new_key = f"documents/renamed-{uuid.uuid4()}.txt"

        renamed = rename_file(
            session,
            file_id,
            original_key,
            new_key,
            1,
            request_id,
        )

        assert renamed == new_key

        versions = session.scalars(
            select(FileVersion).where(FileVersion.file_id == file_id)
        ).all()

        assert len(versions) == 1
        assert versions[0].version_number == 1
        assert storage.read(versions[0].content_hash) == b"Original content"

        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )

        assert journal is not None
        assert journal.action == ChangeAction.RENAME
        assert journal.old_path == original_key
        assert journal.path == new_key
        assert journal.version_number == 1


def test_rename_stale_path_does_not_create_event(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    rejected_request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"rename-stale-{uuid.uuid4()}.txt",
            b"Hello",
            uuid.uuid4(),
        )
        file_id = saved_file.id
        original_key = saved_file.key
        first_new_key = f"first-rename-{uuid.uuid4()}.txt"

        rename_file(
            session,
            file_id,
            original_key,
            first_new_key,
            1,
            uuid.uuid4(),
        )

        with pytest.raises(FileVersionConflictError):
            rename_file(
                session,
                file_id,
                original_key,
                f"second-rename-{uuid.uuid4()}.txt",
                1,
                rejected_request_id,
            )

        assert session.get(File, file_id).key == first_new_key

        rejected_events = session.scalars(
            select(ChangeJournal).where(ChangeJournal.request_id == rejected_request_id)
        ).all()

        assert rejected_events == []


def test_rename_rolls_back_on_journal_failure(db_session_factory, tmp_path):
    storage = LocalBlobStorage(tmp_path)
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        saved_file = create_file(
            session,
            storage,
            f"rename-rollback-{uuid.uuid4()}.txt",
            b"Hello",
            uuid.uuid4(),
        )
        file_id = saved_file.id
        original_key = saved_file.key

        def fail_rename_journal(mapper, connection, target):
            if target.action == ChangeAction.RENAME:
                raise RuntimeError("Simulated rename journal failure")

        event.listen(
            ChangeJournal,
            "before_insert",
            fail_rename_journal,
        )

        try:
            with pytest.raises(
                RuntimeError,
                match="Simulated rename journal failure",
            ):
                rename_file(
                    session,
                    file_id,
                    original_key,
                    f"failed-rename-{uuid.uuid4()}.txt",
                    1,
                    request_id,
                )
        finally:
            event.remove(
                ChangeJournal,
                "before_insert",
                fail_rename_journal,
            )

        assert session.get(File, file_id).key == original_key

        journal = session.scalar(
            select(ChangeJournal).where(ChangeJournal.request_id == request_id)
        )

        assert journal is None
