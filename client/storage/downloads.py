import hashlib
import os
import tempfile
from contextlib import ExitStack, closing
from pathlib import Path
from typing import BinaryIO

from client.api import DropboxClient
from client.core.exceptions import (
    DownloadVerificationError,
    SyncConflictError,
    UnsafeSyncPathError,
)
from shared.chunking import MAX_CHUNK_SIZE, chunk_file


def download_verified(
    api: DropboxClient,
    root: Path,
    relative_path: str,
    file_id: str,
    expected_version: int,
    expected_local_hash: str | None = None,
    expected_content_hash: str | None = None,
    expected_size: int | None = None,
) -> dict:
    destination = safe_local_path(root, relative_path)

    verify_destination(destination, expected_local_hash)
    content_hash, size, chunks = _validate_manifest(
        api.get_file_manifest(file_id, expected_version),
        file_id,
        expected_version,
        expected_content_hash,
        expected_size,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=".sync-download-",
        dir=destination.parent,
    )
    os.close(fd)

    temporary = Path(temporary_name)

    try:
        _assemble_download(api, destination, temporary, chunks)
        if temporary.stat().st_size != size or file_hash(temporary) != content_hash:
            raise DownloadVerificationError(
                f"Assembled file hash or size mismatch: {relative_path}"
            )
        metadata = api.get_file_metadata(file_id)

        if metadata["file_id"] != file_id:
            raise DownloadVerificationError(
                f"Unexpected downloaded file identity: {relative_path}"
            )

        if metadata["content_hash"] != content_hash or metadata["size"] != size:
            raise DownloadVerificationError(
                f"Downloaded content differs from saved target: {relative_path}"
            )

        if metadata["version"] != expected_version:
            raise DownloadVerificationError(
                f"Remote file changed during download: {relative_path}"
            )

        if metadata["key"] != relative_path:
            raise DownloadVerificationError(
                f"Remote file renamed during download: {relative_path}"
            )

        verify_destination(destination, expected_local_hash)
        temporary.replace(destination)

        if os.name == "posix":
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)

        return {
            "file_id": file_id,
            "version": metadata["version"],
            "hash": metadata["content_hash"],
            "size": metadata["size"],
        }

    finally:
        temporary.unlink(missing_ok=True)


def safe_local_path(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)

    if (
        path.is_absolute()
        or not relative_path
        or ".." in path.parts
        or path == Path(".")
    ):
        raise UnsafeSyncPathError(f"Unsafe sync path: {relative_path}")

    destination = root / path

    if not destination.resolve().is_relative_to(root.resolve()):
        raise UnsafeSyncPathError(f"Path escapes sync folder: {relative_path}")

    return destination


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def verify_destination(
    destination: Path,
    expected_local_hash: str | None,
) -> None:
    if expected_local_hash is None:
        if destination.exists() or destination.is_symlink():
            raise SyncConflictError(
                f"Download destination already exists: {destination}"
            )
        return

    if not destination.is_file() or destination.is_symlink():
        raise SyncConflictError(f"Expected synchronized file is missing: {destination}")

    if file_hash(destination) != expected_local_hash:
        raise SyncConflictError(f"Local file changed during download: {destination}")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_manifest(
    manifest: dict,
    file_id: str,
    expected_version: int,
    expected_hash: str | None,
    expected_size: int | None,
) -> tuple[str, int, list[tuple[str, int]]]:
    if (
        not isinstance(manifest, dict)
        or manifest.get("file_id") != file_id
        or type(manifest.get("version")) is not int
        or manifest["version"] != expected_version
        or not _is_sha256(manifest.get("content_hash"))
        or type(manifest.get("size")) is not int
        or manifest["size"] < 0
        or not isinstance(manifest.get("chunks"), list)
    ):
        raise DownloadVerificationError("Invalid download manifest identity or content")
    if (expected_hash is not None and manifest["content_hash"] != expected_hash) or (
        expected_size is not None and manifest["size"] != expected_size
    ):
        raise DownloadVerificationError("Download manifest differs from saved target")

    chunks: list[tuple[str, int]] = []
    sizes: dict[str, int] = {}
    total = 0
    for entry in manifest["chunks"]:
        if (
            not isinstance(entry, dict)
            or not _is_sha256(entry.get("hash"))
            or type(entry.get("size")) is not int
            or not 0 <= entry["size"] <= MAX_CHUNK_SIZE
        ):
            raise DownloadVerificationError("Invalid download manifest chunk")
        digest, size = entry["hash"], entry["size"]
        if digest in sizes and sizes[digest] != size:
            raise DownloadVerificationError("Inconsistent repeated chunk sizes")
        sizes[digest] = size
        total += size
        chunks.append((digest, size))
    if total != manifest["size"]:
        raise DownloadVerificationError("Download manifest total size mismatch")
    return manifest["content_hash"], manifest["size"], chunks


def _read_verified_range(
    source: BinaryIO, offset: int, size: int, digest: str
) -> bytes | None:
    source.seek(offset)
    data = source.read(size)
    if len(data) == size and hashlib.sha256(data).hexdigest() == digest:
        return data
    return None


def _assemble_download(
    api: DropboxClient,
    destination: Path,
    temporary: Path,
    chunks: list[tuple[str, int]],
) -> None:
    """Content buffers are bounded; manifests and range indexes grow with chunks."""
    local_ranges: dict[str, tuple[int, int]] = {}
    if chunks and destination.is_file():
        offset = 0
        for chunk in chunk_file(destination):
            local_ranges.setdefault(chunk.hash, (offset, chunk.size))
            offset += chunk.size

    assembled_ranges: dict[str, tuple[int, int]] = {}
    with ExitStack() as stack:
        source = stack.enter_context(destination.open("rb")) if local_ranges else None
        output = stack.enter_context(temporary.open("w+b"))
        for digest, size in chunks:
            offset = output.tell()
            data = None
            if digest in assembled_ranges:
                previous_offset, previous_size = assembled_ranges[digest]
                data = _read_verified_range(
                    output, previous_offset, previous_size, digest
                )
                output.seek(offset)
            elif source is not None and digest in local_ranges:
                local_offset, local_size = local_ranges[digest]
                if local_size == size:
                    data = _read_verified_range(
                        source, local_offset, local_size, digest
                    )
            if data is not None:
                output.write(data)
            else:
                count = 0
                hasher = hashlib.sha256()
                with closing(api.iter_chunk_bytes(digest)) as blocks:
                    for block in blocks:
                        count += len(block)
                        if count > size:
                            raise DownloadVerificationError(
                                "Chunk exceeds manifest size"
                            )
                        hasher.update(block)
                        output.write(block)
                if count != size or hasher.hexdigest() != digest:
                    raise DownloadVerificationError("Chunk size or SHA-256 mismatch")
            assembled_ranges[digest] = (offset, size)
        output.flush()
        os.fsync(output.fileno())
