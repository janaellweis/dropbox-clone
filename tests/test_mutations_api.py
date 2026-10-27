import uuid

from backend.db.models import ChangeAction, ChangeJournal, FileVersion


def test_update_file_success(api_client, db_session_factory):
    request_id = str(uuid.uuid4())

    create_response = api_client.post(
        "/files",
        data={
            "key": f"update-{uuid.uuid4()}.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("update-test.txt", b"Original content")},
    )
    assert create_response.status_code == 200

    file_id = create_response.json()["file_id"]

    response = api_client.put(
        f"/files/{file_id}",
        data={
            "expected_version": 1,
            "request_id": request_id,
        },
        files={"file": ("update-test.txt", b"Updated content")},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2

    with db_session_factory() as session:
        version = (
            session.query(FileVersion)
            .filter_by(file_id=uuid.UUID(file_id), version_number=2)
            .one()
        )
        assert version.size == len(b"Updated content")

        journal = (
            session.query(ChangeJournal)
            .filter_by(request_id=uuid.UUID(request_id))
            .one()
        )
        assert journal.action == ChangeAction.UPDATE
        assert journal.version_number == 2

    download = api_client.get(f"/files/{file_id}/download")
    assert download.status_code == 200
    assert download.content == b"Updated content"


def test_update_file_idempotent_retry(api_client, db_session_factory):
    create_response = api_client.post(
        "/files",
        data={
            "key": f"retry-{uuid.uuid4()}.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("retry-test.txt", b"Original")},
    )
    assert create_response.status_code == 200
    file_id = create_response.json()["file_id"]

    request_id = str(uuid.uuid4())

    def send_update(content):
        return api_client.put(
            f"/files/{file_id}",
            data={
                "expected_version": 1,
                "request_id": request_id,
            },
            files={"file": ("retry-test.txt", content)},
        )

    first = send_update(b"Updated")
    second = send_update(b"Updated")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["version"] == 2
    assert second.json()["version"] == 2

    changed_content = send_update(b"Different content")
    assert changed_content.status_code == 409

    with db_session_factory() as session:
        versions = (
            session.query(FileVersion).filter_by(file_id=uuid.UUID(file_id)).all()
        )
        assert len(versions) == 2


def test_delete_file_success(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "delete-me.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("delete-me.txt", b"Original content")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    response = api_client.delete(
        f"/files/{file_id}",
        params={
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 200
    assert response.json()["deleted"] is True

    # Deleted files must no longer be downloadable.
    assert api_client.get(f"/files/{file_id}/download").status_code == 404

    # Deleted files must also be hidden from metadata queries.
    assert api_client.get(f"/files/{file_id}").status_code == 404


def test_delete_file_idempotent_retry(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "retry-delete.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("retry-delete.txt", b"Hello")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    request_id = str(uuid.uuid4())
    params = {
        "expected_version": 1,
        "request_id": request_id,
    }

    first = api_client.delete(f"/files/{file_id}", params=params)
    retry = api_client.delete(f"/files/{file_id}", params=params)

    assert first.status_code == 200
    assert retry.status_code == 200

    # Reusing the request ID for a different operation must fail.
    changed_request = api_client.delete(
        f"/files/{file_id}",
        params={
            "expected_version": 2,
            "request_id": request_id,
        },
    )
    assert changed_request.status_code == 409


def test_deleted_path_can_be_reused(api_client):
    first_upload = api_client.post(
        "/files",
        data={
            "key": "reusable.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("reusable.txt", b"Old content")},
    )
    assert first_upload.status_code == 200

    old_file_id = first_upload.json()["file_id"]

    deletion = api_client.delete(
        f"/files/{old_file_id}",
        params={
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )
    assert deletion.status_code == 200

    second_upload = api_client.post(
        "/files",
        data={
            "key": "reusable.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("reusable.txt", b"New content")},
    )

    assert second_upload.status_code == 200
    assert second_upload.json()["file_id"] != old_file_id


def test_rename_file_success(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "rename-original.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("rename-original.txt", b"Hello")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    response = api_client.patch(
        f"/files/{file_id}/rename",
        json={
            "expected_key": "rename-original.txt",
            "new_key": "documents/renamed.txt",
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 200
    assert response.json()["key"] == "documents/renamed.txt"

    metadata = api_client.get(f"/files/{file_id}")
    assert metadata.status_code == 200
    assert metadata.json()["key"] == "documents/renamed.txt"
    assert metadata.json()["version"] == 1

    download = api_client.get(f"/files/{file_id}/download")
    assert download.status_code == 200
    assert download.content == b"Hello"


def test_rename_file_stale_path(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "before-rename.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("before-rename.txt", b"Hello")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    first = api_client.patch(
        f"/files/{file_id}/rename",
        json={
            "expected_key": "before-rename.txt",
            "new_key": "after-rename.txt",
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )
    assert first.status_code == 200

    stale = api_client.patch(
        f"/files/{file_id}/rename",
        json={
            "expected_key": "before-rename.txt",
            "new_key": "another-name.txt",
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )

    assert stale.status_code == 409
    assert api_client.get(f"/files/{file_id}").json()["key"] == "after-rename.txt"


def test_rename_file_duplicate_path(api_client):
    def upload(key):
        response = api_client.post(
            "/files",
            data={
                "key": key,
                "request_id": str(uuid.uuid4()),
            },
            files={"file": (key, b"Hello")},
        )
        assert response.status_code == 200
        return response.json()["file_id"]

    first_id = upload("rename-source.txt")
    upload("rename-destination.txt")

    response = api_client.patch(
        f"/files/{first_id}/rename",
        json={
            "expected_key": "rename-source.txt",
            "new_key": "rename-destination.txt",
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 409
    assert api_client.get(f"/files/{first_id}").json()["key"] == "rename-source.txt"


def test_rename_file_idempotent_retry(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "rename-retry.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("rename-retry.txt", b"Hello")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    request_id = str(uuid.uuid4())
    body = {
        "expected_key": "rename-retry.txt",
        "new_key": "renamed-retry.txt",
        "expected_version": 1,
        "request_id": request_id,
    }

    first = api_client.patch(f"/files/{file_id}/rename", json=body)
    retry = api_client.patch(f"/files/{file_id}/rename", json=body)

    assert first.status_code == 200
    assert retry.status_code == 200
    assert retry.json()["key"] == "renamed-retry.txt"


def test_rename_file_invalid_destination(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "valid-name.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("valid-name.txt", b"Hello")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    response = api_client.patch(
        f"/files/{file_id}/rename",
        json={
            "expected_key": "valid-name.txt",
            "new_key": "../outside.txt",
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 400
    assert api_client.get(f"/files/{file_id}").json()["key"] == "valid-name.txt"
