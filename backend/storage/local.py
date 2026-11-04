import hashlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO


class LocalBlobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        content: BinaryIO,
        fingerprint_prefix: bytes,
    ) -> tuple[str, int, str]:
        hasher = hashlib.sha256()
        fingerprint_hasher = hashlib.sha256()
        fingerprint_hasher.update(fingerprint_prefix)

        size = 0
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)

                while chunk := content.read(1024 * 1024):
                    hasher.update(chunk)
                    fingerprint_hasher.update(chunk)
                    size += len(chunk)
                    temp_file.write(chunk)

                temp_file.flush()
                os.fsync(temp_file.fileno())

            content_hash = hasher.hexdigest()
            fingerprint = fingerprint_hasher.hexdigest()
            blob_path = self.root / content_hash

            try:
                os.link(temp_path, blob_path)
            except FileExistsError:
                pass

            return content_hash, size, fingerprint

        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def stream(self, content_hash: str) -> Iterator[bytes]:
        blob_path = self.root / content_hash

        with blob_path.open("rb") as file:
            while chunk := file.read(1024 * 1024):
                yield chunk

    def read(self, content_hash: str) -> bytes:
        blob_path = self.root / content_hash
        return blob_path.read_bytes()

    def exists(self, content_hash: str) -> bool:
        blob_path = self.root / content_hash
        return blob_path.is_file()
