from pathlib import Path

from backend.db.database import SessionLocal
from backend.storage.local import LocalBlobStorage

storage = LocalBlobStorage(Path("data/blobs"))


def get_db():
    with SessionLocal() as session:
        yield session


def get_storage() -> LocalBlobStorage:
    return storage
