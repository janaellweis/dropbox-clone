import uuid

from uuid6 import uuid7

from backend.db.models import ChangeJournal, OutboxEvent, OutboxStatus


def test_create_outbox_journal_flow(
    api_client,
    db_session_factory,
    publish_outbox_events,
):
    request_id = uuid7()

    response = api_client.post(
        "/files",
        data={"key": "integration-test.txt", "request_id": str(request_id)},
        files={"file": ("integration-test.txt", b"hello outbox")},
    )

    assert response.status_code == 200, response.text
    file_id = response.json()["file_id"]

    with db_session_factory() as session:
        outbox = session.query(OutboxEvent).filter_by(request_id=request_id).one()

        assert outbox.status == OutboxStatus.PENDING
        assert str(outbox.file_id) == file_id
        assert session.query(ChangeJournal).count() == 0

    assert publish_outbox_events() == 1

    with db_session_factory() as session:
        outbox = session.query(OutboxEvent).filter_by(request_id=request_id).one()
        journal = session.query(ChangeJournal).filter_by(request_id=request_id).one()

        assert outbox.status == OutboxStatus.COMPLETED
        assert journal.outbox_event_id == outbox.id
        assert journal.file_id == outbox.file_id

    assert publish_outbox_events() == 0

    response = api_client.get("/updates", params={"since": 0})

    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["updates"]) == 1
    assert body["updates"][0]["file_id"] == file_id
    assert body["next_checkpoint"] == body["updates"][0]["id"]
    assert body["has_more"] is False


def test_update_outbox_journal_flow(
    api_client, db_session_factory, publish_outbox_events
):
    create = api_client.post(
        "/files",
        data={"key": "update-flow.txt", "request_id": str(uuid7())},
        files={"file": ("update-flow.txt", b"original")},
    )
    assert create.status_code == 200, create.text
    file_id = create.json()["file_id"]

    update_id = uuid7()
    update = api_client.put(
        f"/files/{file_id}",
        data={"expected_version": 1, "request_id": str(update_id)},
        files={"file": ("update-flow.txt", b"updated")},
    )
    assert update.status_code == 200, update.text
    assert update.json()["version"] == 2

    with db_session_factory() as session:
        event = session.query(OutboxEvent).filter_by(request_id=update_id).one()
        assert event.status == OutboxStatus.PENDING
        assert session.query(ChangeJournal).count() == 0

    assert publish_outbox_events() == 2

    with db_session_factory() as session:
        event = session.query(OutboxEvent).filter_by(request_id=update_id).one()
        journal = session.query(ChangeJournal).filter_by(request_id=update_id).one()

        assert event.status == OutboxStatus.COMPLETED
        assert journal.outbox_event_id == event.id
        assert journal.version_number == 2

    response = api_client.get("/updates", params={"since": 0})
    assert response.status_code == 200
    assert [event["action"] for event in response.json()["updates"]] == [
        "CREATE",
        "UPDATE",
    ]

    download = api_client.get(f"/files/{file_id}/download")
    assert download.status_code == 200
    assert download.content == b"updated"


def test_delete_outbox_journal_flow(
    api_client, db_session_factory, publish_outbox_events
):
    create = api_client.post(
        "/files",
        data={"key": "delete-flow.txt", "request_id": str(uuid7())},
        files={"file": ("delete-flow.txt", b"content")},
    )
    assert create.status_code == 200, create.text
    file_id = create.json()["file_id"]

    delete_id = uuid7()
    deletion = api_client.delete(
        f"/files/{file_id}",
        params={"expected_version": 1, "request_id": str(delete_id)},
    )
    assert deletion.status_code == 200, deletion.text

    with db_session_factory() as session:
        event = session.query(OutboxEvent).filter_by(request_id=delete_id).one()
        assert event.status == OutboxStatus.PENDING
        assert session.query(ChangeJournal).count() == 0

    assert publish_outbox_events() == 2

    with db_session_factory() as session:
        event = session.query(OutboxEvent).filter_by(request_id=delete_id).one()
        journal = session.query(ChangeJournal).filter_by(request_id=delete_id).one()

        assert event.status == OutboxStatus.COMPLETED
        assert journal.outbox_event_id == event.id

    updates = api_client.get("/updates", params={"since": 0})
    assert updates.status_code == 200
    assert [event["action"] for event in updates.json()["updates"]] == [
        "CREATE",
        "DELETE",
    ]

    assert api_client.get(f"/files/{file_id}").status_code == 404
    assert api_client.get(f"/files/{file_id}/download").status_code == 404


def test_concurrent_identical_updates(api_client, db_session_factory):
    from concurrent.futures import ThreadPoolExecutor

    from backend.db.models import FileVersion

    created = api_client.post(
        "/files",
        data={"key": "concurrent-retry.txt", "request_id": str(uuid7())},
        files={"file": ("concurrent-retry.txt", b"original")},
    )
    assert created.status_code == 200, created.text
    file_id = created.json()["file_id"]

    request_id = str(uuid7())

    def update():
        return api_client.put(
            f"/files/{file_id}",
            data={"expected_version": 1, "request_id": request_id},
            files={"file": ("concurrent-retry.txt", b"updated")},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: update(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]

    with db_session_factory() as session:
        versions = (
            session.query(FileVersion).filter_by(file_id=uuid.UUID(file_id)).all()
        )
        assert len(versions) == 2

        events = (
            session.query(OutboxEvent).filter_by(request_id=uuid.UUID(request_id)).all()
        )
        assert len(events) == 1


def test_concurrent_conflicting_updates(api_client, db_session_factory):
    from concurrent.futures import ThreadPoolExecutor

    from backend.db.models import FileVersion

    created = api_client.post(
        "/files",
        data={"key": "concurrent-conflict.txt", "request_id": str(uuid7())},
        files={"file": ("concurrent-conflict.txt", b"original")},
    )
    assert created.status_code == 200, created.text
    file_id = created.json()["file_id"]

    def update(request_id):
        return api_client.put(
            f"/files/{file_id}",
            data={"expected_version": 1, "request_id": str(request_id)},
            files={"file": ("concurrent-conflict.txt", b"updated")},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(update, [uuid7(), uuid7()]))

    assert sorted(response.status_code for response in responses) == [200, 409]

    with db_session_factory() as session:
        versions = (
            session.query(FileVersion).filter_by(file_id=uuid.UUID(file_id)).all()
        )
        assert len(versions) == 2


def test_updates_checkpoint_pagination(api_client, publish_outbox_events):
    for index in range(3):
        response = api_client.post(
            "/files",
            data={
                "key": f"checkpoint-{index}.txt",
                "request_id": str(uuid7()),
            },
            files={"file": (f"checkpoint-{index}.txt", b"content")},
        )
        assert response.status_code == 200, response.text

    assert publish_outbox_events() == 3

    first = api_client.get("/updates", params={"since": 0, "limit": 2})
    assert first.status_code == 200
    first_page = first.json()

    assert len(first_page["updates"]) == 2
    assert first_page["has_more"] is True
    assert first_page["next_checkpoint"] == first_page["updates"][-1]["id"]

    second = api_client.get(
        "/updates",
        params={"since": first_page["next_checkpoint"], "limit": 2},
    )
    assert second.status_code == 200
    second_page = second.json()

    assert len(second_page["updates"]) == 1
    assert second_page["has_more"] is False

    all_ids = [event["id"] for event in first_page["updates"] + second_page["updates"]]
    assert all_ids == sorted(all_ids)
    assert len(set(all_ids)) == 3

    empty = api_client.get(
        "/updates",
        params={"since": second_page["next_checkpoint"]},
    )
    assert empty.status_code == 200
    assert empty.json()["updates"] == []
    assert empty.json()["next_checkpoint"] == second_page["next_checkpoint"]


def test_worker_does_not_publish_duplicate_events(
    api_client, db_session_factory, publish_outbox_events
):
    request_id = uuid7()

    response = api_client.post(
        "/files",
        data={"key": "publish-once.txt", "request_id": str(request_id)},
        files={"file": ("publish-once.txt", b"content")},
    )
    assert response.status_code == 200, response.text

    assert publish_outbox_events() == 1
    assert publish_outbox_events() == 0

    with db_session_factory() as session:
        events = session.query(OutboxEvent).filter_by(request_id=request_id).all()
        journal = session.query(ChangeJournal).filter_by(request_id=request_id).all()

        assert len(events) == 1
        assert len(journal) == 1
        assert events[0].status == OutboxStatus.COMPLETED
        assert journal[0].outbox_event_id == events[0].id


def test_worker_respects_publication_lock(api_client, db_session_factory):
    from sqlalchemy import text

    from backend.workers.outbox_worker import process_pending_events

    response = api_client.post(
        "/files",
        data={"key": "worker-lock.txt", "request_id": str(uuid7())},
        files={"file": ("worker-lock.txt", b"content")},
    )
    assert response.status_code == 200, response.text

    with db_session_factory() as first_session:
        acquired = first_session.execute(
            text("SELECT pg_try_advisory_xact_lock(742002)")
        ).scalar_one()
        assert acquired is True

        try:
            with db_session_factory() as second_session:
                assert process_pending_events(second_session) == 0

            with db_session_factory() as session:
                assert session.query(ChangeJournal).count() == 0
        finally:
            first_session.rollback()

    with db_session_factory() as session:
        assert process_pending_events(session) == 1

    with db_session_factory() as session:
        assert session.query(ChangeJournal).count() == 1


def test_worker_preserves_per_file_event_order(
    api_client, db_session_factory, publish_outbox_events
):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from backend.db.models import ChangeAction

    created = api_client.post(
        "/files",
        data={"key": "ordered.txt", "request_id": str(uuid7())},
        files={"file": ("ordered.txt", b"version one")},
    )
    assert created.status_code == 200, created.text
    file_id = uuid.UUID(created.json()["file_id"])

    updated = api_client.put(
        f"/files/{file_id}",
        data={"expected_version": 1, "request_id": str(uuid7())},
        files={"file": ("ordered.txt", b"version two")},
    )
    assert updated.status_code == 200, updated.text

    renamed = api_client.patch(
        f"/files/{file_id}/rename",
        json={
            "expected_key": "ordered.txt",
            "new_key": "ordered-renamed.txt",
            "expected_version": 2,
            "request_id": str(uuid7()),
        },
    )
    assert renamed.status_code == 200, renamed.text

    with db_session_factory() as session:
        events = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.file_id == file_id)
            .order_by(OutboxEvent.event_sequence)
        ).all()

        assert [event.event_sequence for event in events] == [1, 2, 3]

        events[2].created_at = datetime.now(timezone.utc) - timedelta(days=2)
        session.commit()

    assert publish_outbox_events() == 3

    with db_session_factory() as session:
        journal = session.scalars(
            select(ChangeJournal)
            .where(ChangeJournal.file_id == file_id)
            .order_by(ChangeJournal.id)
        ).all()

        assert [event.action for event in journal] == [
            ChangeAction.CREATE,
            ChangeAction.UPDATE,
            ChangeAction.RENAME,
        ]


def test_concurrent_identical_deletes(api_client, db_session_factory):
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import select

    from backend.db.models import ChangeAction

    created = api_client.post(
        "/files",
        data={"key": "concurrent-delete.txt", "request_id": str(uuid7())},
        files={"file": ("concurrent-delete.txt", b"content")},
    )
    assert created.status_code == 200, created.text
    file_id = uuid.UUID(created.json()["file_id"])
    request_id = uuid7()

    def delete():
        return api_client.delete(
            f"/files/{file_id}",
            params={"expected_version": 1, "request_id": str(request_id)},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: delete(), range(2)))

    assert [response.status_code for response in responses] == [200, 200]

    with db_session_factory() as session:
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.file_id == file_id,
                OutboxEvent.action == ChangeAction.DELETE,
            )
        ).all()
        assert len(events) == 1


def test_failed_update_does_not_commit_outbox(api_client, db_session_factory):
    from sqlalchemy import select

    from backend.db.models import FileVersion

    created = api_client.post(
        "/files",
        data={"key": "rollback-test.txt", "request_id": str(uuid7())},
        files={"file": ("rollback-test.txt", b"original")},
    )
    assert created.status_code == 200, created.text
    file_id = uuid.UUID(created.json()["file_id"])

    failed_request_id = uuid7()
    response = api_client.put(
        f"/files/{file_id}",
        data={
            "expected_version": 99,
            "request_id": str(failed_request_id),
        },
        files={"file": ("rollback-test.txt", b"should not commit")},
    )

    assert response.status_code == 409

    with db_session_factory() as session:
        versions = session.scalars(
            select(FileVersion).where(FileVersion.file_id == file_id)
        ).all()

        failed_events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.request_id == failed_request_id)
        ).all()

        assert len(versions) == 1
        assert failed_events == []
