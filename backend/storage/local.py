import hashlib
import os
import tempfile
from collections.abc import Generator, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


class LocalBlobStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, content_hash: str) -> bytes:
        blob_path = self.root / content_hash
        return blob_path.read_bytes()

    @contextmanager
    def open_verified_chunk(self, content_hash: str) -> Iterator[BinaryIO]:
        """Verify before response headers, then keep the same file open to stream."""
        with (self.root / content_hash).open("rb") as content:
            hasher = hashlib.sha256()
            for block in iter(lambda: content.read(1024 * 1024), b""):
                hasher.update(block)
            if hasher.hexdigest() != content_hash:
                raise OSError(f"Chunk integrity check failed: {content_hash}")
            content.seek(0)
            yield content

    def exists(self, content_hash: str) -> bool:
        blob_path = self.root / content_hash
        return blob_path.is_file()

    def save_chunk(self, data: bytes | memoryview) -> str:
        content_hash = hashlib.sha256(data).hexdigest()
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
                temp_file.write(data)
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

    def stream_chunks(self, chunk_hashes: Iterable[str]) -> Iterator[bytes]:
        for chunk_hash in chunk_hashes:
            data = self.read(chunk_hash)

            actual_hash = hashlib.sha256(data).hexdigest()
            if actual_hash != chunk_hash:
                raise OSError(f"Chunk integrity check failed: {chunk_hash}")

            yield data

    @contextmanager
    def stage_upload(
        self,
        content: BinaryIO,
        fingerprint_prefix: bytes,
    ) -> Generator[tuple[Path, str, int, str], None, None]:
        hasher = hashlib.sha256()
        fingerprint_hasher = hashlib.sha256()
        fingerprint_hasher.update(fingerprint_prefix)

        size = 0

        with tempfile.NamedTemporaryFile(
            dir=self.root,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)

        try:
            with temp_path.open("wb") as temp_file:
                while chunk := content.read(1024 * 1024):
                    hasher.update(chunk)
                    fingerprint_hasher.update(chunk)
                    size += len(chunk)
                    temp_file.write(chunk)

                temp_file.flush()
                os.fsync(temp_file.fileno())

            yield (
                temp_path,
                hasher.hexdigest(),
                size,
                fingerprint_hasher.hexdigest(),
            )
        finally:
            temp_path.unlink(missing_ok=True)
