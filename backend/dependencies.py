import os
from pathlib import Path

from backend.db.database import SessionLocal
from backend.storage.local import LocalBlobStorage

BLOB_STORAGE_PATH = Path(os.environ.get("BLOB_STORAGE_PATH", "data/blobs"))

storage = LocalBlobStorage(BLOB_STORAGE_PATH)


def get_db():
    with SessionLocal() as session:
        yield session


def get_storage() -> LocalBlobStorage:
    return storage
