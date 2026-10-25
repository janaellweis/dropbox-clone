import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import ChangeJournal, File, FileVersion
from backend.main import app, get_db
from backend.storage.local import LocalBlobStorage

TEST_DATABASE_URL = (
    "postgresql+psycopg://dropbox:dropbox_dev@localhost:5432/dropbox_test"
)

test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=test_engine)


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    def override_get_db():
        with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    test_storage = LocalBlobStorage(tmp_path)
    monkeypatch.setattr("backend.main.storage", test_storage)

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()

        with test_engine.begin() as connection:
            connection.execute(ChangeJournal.__table__.delete())
            connection.execute(FileVersion.__table__.delete())
            connection.execute(File.__table__.delete())


def test_upload_endpoint(api_client):
    response = api_client.post(
        "/files",
        data={
            "key": f"api-{uuid.uuid4()}.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("test.txt", b"Hello API!")},
    )

    assert response.status_code == 200
    assert "file_id" in response.json()


def test_download_endpoint(api_client):
    content = b"Download test!"

    upload = api_client.post(
        "/files",
        data={
            "key": f"download-{uuid.uuid4()}.txt",
            "request_id": str(uuid.uuid4()),
        },
        files={"file": ("test.txt", content)},
    )

    assert upload.status_code == 200
    file_id = upload.json()["file_id"]

    response = api_client.get(f"/files/{file_id}/download")

    assert response.status_code == 200
    assert response.content == content
