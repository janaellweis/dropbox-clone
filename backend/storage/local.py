import hashlib
import os
import tempfile
from pathlib import Path


class LocalBlobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes) -> str:
        content_hash = hashlib.sha256(content).hexdigest()
        blob_path = self.root / content_hash

        if blob_path.is_file():
            return content_hash

        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(content)
                temp_file.flush()
                os.fsync(temp_file.fileno())

            try:
                os.link(temp_path, blob_path)
            except FileExistsError:
                pass

            return content_hash

        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def read(self, content_hash: str) -> bytes:
        blob_path = self.root / content_hash
        return blob_path.read_bytes()

    def exists(self, content_hash: str) -> bool:
        blob_path = self.root / content_hash
        return blob_path.is_file()
