import hashlib
import uuid

from backend.db.models import FileVersion


def test_download_missing_blob_returns_500(
    api_client, test_blob_storage, db_session_factory
):
    content = b"Missing blob test!"

    upload = api_client.post(
        "/files",
        data={
            "key": f"missing-{uuid.uuid4()}.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("test.txt", content)},
    )

    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    with db_session_factory() as session:
        version = (
            session.query(FileVersion).filter_by(file_id=uuid.UUID(file_id)).first()
        )
        assert version is not None
        content_hash = version.content_hash

    blob_path = test_blob_storage.root / content_hash
    blob_path.unlink()

    response = api_client.get(f"/files/{file_id}/download")

    assert response.status_code == 500
    assert response.json()["detail"] == "File content missing from storage"


def test_get_file_metadata(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "notes.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("notes.txt", b"Hello")},
    )
    assert upload.status_code == 200

    file_id = upload.json()["file_id"]

    response = api_client.get(f"/files/{file_id}")

    assert response.status_code == 200
    assert response.json()["file_id"] == file_id
    assert response.json()["key"] == "notes.txt"
    assert response.json()["version"] == 1
    assert response.json()["size"] == 5
    assert response.json()["content_hash"] == hashlib.sha256(b"Hello").hexdigest()


def test_list_file_versions(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "history.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("history.txt", b"Version one")},
    )
    assert upload.status_code == 200

    file_id = upload.json()["file_id"]

    update = api_client.put(
        f"/files/{file_id}",
        data={
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("history.txt", b"Version two")},
    )
    assert update.status_code == 200

    response = api_client.get(f"/files/{file_id}/versions")

    assert response.status_code == 200

    versions = response.json()["versions"]

    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[1]["version"] == 1
    assert versions[0]["size"] == len(b"Version two")
    assert versions[1]["size"] == len(b"Version one")


def test_download_historical_version(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "document.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("document.txt", b"Original content")},
    )
    assert upload.status_code == 200

    file_id = upload.json()["file_id"]

    update = api_client.put(
        f"/files/{file_id}",
        data={
            "expected_version": 1,
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("document.txt", b"Updated content")},
    )
    assert update.status_code == 200

    old_version = api_client.get(f"/files/{file_id}/versions/1")
    latest_version = api_client.get(f"/files/{file_id}/versions/2")

    assert old_version.status_code == 200
    assert old_version.content == b"Original content"

    assert latest_version.status_code == 200
    assert latest_version.content == b"Updated content"
  
def test_download_nonexistent_version(api_client):
    upload = api_client.post(
        "/files",
        data={
            "key": "missing-version.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("missing-version.txt", b"Hello")},
    )
    assert upload.status_code == 200

    file_id = upload.json()["file_id"]

    response = api_client.get(f"/files/{file_id}/versions/99")

    assert response.status_code == 404


def test_upload_rejects_invalid_path(api_client):
    response = api_client.post(
        "/files",
        data={
            "key": "../secret.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("secret.txt", b"secret content")},
    )

    assert response.status_code == 400