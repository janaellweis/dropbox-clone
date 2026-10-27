import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import ChangeJournal, File, FileVersion
from backend.dependencies import get_db, get_storage
from backend.main import app
from backend.storage.local import LocalBlobStorage

TEST_DATABASE_URL = (
    "postgresql+psycopg://dropbox:dropbox_dev@localhost:5432/dropbox_test"
)

test_engine = create_engine(TEST_DATABASE_URL)
TestSessionLocal = sessionmaker(bind=test_engine)


@pytest.fixture
def test_blob_storage(tmp_path):
    return LocalBlobStorage(tmp_path)


@pytest.fixture
def api_client(test_blob_storage):
    def override_get_db():
        with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_storage] = lambda: test_blob_storage

    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def db_session_factory():
    return TestSessionLocal


@pytest.fixture(autouse=True)
def clean_test_database():
    def clean():
        with test_engine.begin() as connection:
            connection.execute(ChangeJournal.__table__.delete())
            connection.execute(FileVersion.__table__.delete())
            connection.execute(File.__table__.delete())

    clean()
    yield
    clean()
