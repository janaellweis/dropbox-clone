import hashlib
import os
import stat
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

    def iter_files(self) -> Iterator[tuple[str, int, int]]:
        """List regular file names, sizes, and mtimes without opening or following links."""
        with os.scandir(self.root) as entries:
            for entry in entries:
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISREG(metadata.st_mode):
                    yield entry.name, metadata.st_size, metadata.st_mtime_ns

    def file_metadata(self, content_hash: str) -> os.stat_result | None:
        try:
            metadata = (self.root / content_hash).lstat()
        except FileNotFoundError:
            return None
        return metadata if stat.S_ISREG(metadata.st_mode) else None

    def renew_chunk(self, content_hash: str) -> bool:
        try:
            fd = os.open(
                self.root / content_hash, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
        except FileNotFoundError:
            return False
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("Chunk is not a regular file")
            os.utime(fd, None)
            os.fsync(fd)
        finally:
            os.close(fd)
        return True

    def unlink_chunk(
        self, content_hash: str, expected: os.stat_result, cutoff_ns: int
    ) -> bool:
        current = self.file_metadata(content_hash)
        if current is None or current.st_mtime_ns > cutoff_ns:
            return False
        if any(
            getattr(current, field) != getattr(expected, field)
            for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        ):
            return False
        try:
            (self.root / content_hash).unlink()
        except FileNotFoundError:
            return False
        self._sync_directory()
        return True

    def _sync_directory(self) -> None:
        directory_fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def save_chunk(self, data: bytes | memoryview) -> str:
        content_hash = hashlib.sha256(data).hexdigest()
        blob_path = self.root / content_hash

        if self.renew_chunk(content_hash):
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
                if not self.renew_chunk(content_hash):
                    raise OSError("Chunk disappeared during installation")

        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

        self._sync_directory()
        return content_hash

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
