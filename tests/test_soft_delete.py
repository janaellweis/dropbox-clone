import io
import uuid
from datetime import datetime, timezone

from backend.db.models import File
from backend.services.files import create_file
from backend.services.mutations import delete_file


def test_soft_delete_scope(db_session_factory):
    with db_session_factory() as session:
        active_file = File(key="active.txt")
        deleted_file = File(
            key="deleted.txt",
            deleted_at=datetime.now(timezone.utc),
        )

        session.add_all([active_file, deleted_file])
        session.commit()

        active_files = session.query(File).all()

        assert len(active_files) == 1
        assert active_files[0].key == "active.txt"

        all_files = (
            session.query(File)
            .execution_options(include_deleted=True)
            .all()
        )

        assert len(all_files) == 2


def test_session_get_can_return_cached_deleted_file(db_session_factory):
    with db_session_factory() as session:
        deleted_file = File(
            key="cached-deleted.txt",
            deleted_at=datetime.now(timezone.utc),
        )
        session.add(deleted_file)
        session.commit()

        file_id = deleted_file.id

        # Explicitly load the deleted record into the session.
        cached_file = (
            session.query(File)
            .execution_options(include_deleted=True)
            .filter(File.id == file_id)
            .one()
        )

        # session.get() can return the cached object without a SELECT.
        result = session.get(File, file_id)

        assert result is cached_file
        assert result.deleted_at is not None





def test_create_retry_after_soft_delete(
    db_session_factory,
    test_blob_storage,
):
    request_id = uuid.uuid4()

    with db_session_factory() as session:
        original = create_file(
            session=session,
            storage=test_blob_storage,
            key="notes.txt",
            content=io.BytesIO(b"hello"),
            request_id=request_id,
        )
        original_id = original.id

        delete_file(
            session=session,
            file_id=original_id,
            expected_version=1,
            request_id=uuid.uuid4(),
        )

        retried = create_file(
            session=session,
            storage=test_blob_storage,
            key="notes.txt",
            content=io.BytesIO(b"hello"),
            request_id=request_id,
        )

        assert retried.id == original_id
        assert retried.deleted_at is not None

        # The retry must not create a second file.
        all_files = (
            session.query(File)
            .execution_options(include_deleted=True)
            .all()
        )
        assert len(all_files) == 1
