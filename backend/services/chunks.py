import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.core.chunk_validation import validate_chunk_hashes
from backend.core.exceptions import FileContentMissingError
from backend.db.models import Chunk, FileVersionChunk
from backend.services.gc import (
    acquire_chunk_shared_locks,
    protect_chunks,
    store_chunk_with_gc_protection,
)
from backend.storage.local import LocalBlobStorage
from shared.chunking import Chunk as ContentChunk


@contextmanager
def stream_chunk(
    storage: LocalBlobStorage, chunk_hash: str
) -> Iterator[Iterator[bytes]]:
    validate_chunk_hashes([chunk_hash])
    with storage.open_verified_chunk(chunk_hash) as content:
        yield iter(lambda: content.read(1024 * 1024), b"")


def store_verified_chunk(
    session: Session,
    storage: LocalBlobStorage,
    chunk_hash: str,
    data: memoryview,
) -> None:
    validate_chunk_hashes([chunk_hash])

    if hashlib.sha256(data).hexdigest() != chunk_hash:
        raise ValueError("Chunk hash mismatch")

    store_chunk_with_gc_protection(session.get_bind(), storage, data)


def save_version_chunks(
    session: Session,
    storage: LocalBlobStorage,
    file_version_id: uuid.UUID,
    chunks: Iterator[ContentChunk],
) -> None:
    hashes = stage_upload_chunks(session, storage, chunks)
    protect_chunks(session, storage, hashes)
    register_manifest_chunks(session, storage, hashes)
    attach_version_chunks(session, file_version_id, hashes)


def stage_upload_chunks(
    session: Session, storage: LocalBlobStorage, chunks: Iterator[ContentChunk]
) -> list[str]:
    return [
        store_chunk_with_gc_protection(session.get_bind(), storage, chunk.data)
        for chunk in chunks
    ]


def get_version_chunk_hashes(
    session: Session,
    file_version_id: uuid.UUID,
) -> list[str]:
    statement = (
        select(FileVersionChunk.chunk_hash)
        .where(FileVersionChunk.file_version_id == file_version_id)
        .order_by(FileVersionChunk.chunk_index)
    )

    return list(session.scalars(statement))


def get_version_chunk_manifest(
    session: Session, file_version_id: uuid.UUID
) -> list[tuple[str, int]]:
    statement = (
        select(FileVersionChunk.chunk_index, Chunk.hash, Chunk.size)
        .select_from(FileVersionChunk)
        .join(Chunk, Chunk.hash == FileVersionChunk.chunk_hash)
        .where(FileVersionChunk.file_version_id == file_version_id)
        .order_by(FileVersionChunk.chunk_index)
    )
    chunks: list[tuple[str, int]] = []
    for expected_index, (index, chunk_hash, size) in enumerate(
        session.execute(statement)
    ):
        if index != expected_index:
            raise FileContentMissingError("File chunk manifest is incomplete")
        chunks.append((chunk_hash, size))
    return chunks


def get_missing_chunks(
    session: Session,
    storage: LocalBlobStorage,
    chunk_hashes: list[str],
) -> list[str]:
    return protect_chunks(session, storage, chunk_hashes, allow_missing=True)


def attach_version_chunks(
    session: Session,
    file_version_id: uuid.UUID,
    chunk_hashes: list[str],
) -> None:
    for index, chunk_hash in enumerate(chunk_hashes):
        session.add(
            FileVersionChunk(
                file_version_id=file_version_id,
                chunk_index=index,
                chunk_hash=chunk_hash,
            )
        )


def validate_chunk_manifest(
    storage: LocalBlobStorage,
    chunk_hashes: list[str],
    expected_hash: str,
    expected_size: int,
) -> None:
    validate_chunk_hashes(chunk_hashes)
    hasher = hashlib.sha256()
    total_size = 0

    for chunk_hash in chunk_hashes:
        try:
            data = storage.read(chunk_hash)
        except FileNotFoundError as exc:
            raise FileContentMissingError(f"Missing chunk: {chunk_hash}") from exc

        if hashlib.sha256(data).hexdigest() != chunk_hash:
            raise ValueError("Chunk integrity check failed")

        hasher.update(data)
        total_size += len(data)

    if total_size != expected_size:
        raise ValueError("File size mismatch")

    if hasher.hexdigest() != expected_hash:
        raise ValueError("File content hash mismatch")


def register_manifest_chunks(
    session: Session,
    storage: LocalBlobStorage,
    chunk_hashes: list[str],
) -> None:
    for chunk_hash in dict.fromkeys(chunk_hashes):
        metadata = storage.file_metadata(chunk_hash)
        if metadata is None:
            raise FileContentMissingError(f"Missing chunk: {chunk_hash}")
        size = metadata.st_size

        session.execute(
            insert(Chunk)
            .values(hash=chunk_hash, size=size)
            .on_conflict_do_nothing(index_elements=["hash"])
        )


def validate_protected_manifest(
    session: Session,
    storage: LocalBlobStorage,
    chunk_hashes: list[str],
    expected_hash: str,
    expected_size: int,
) -> None:
    acquire_chunk_shared_locks(session, chunk_hashes)
    validate_chunk_manifest(storage, chunk_hashes, expected_hash, expected_size)
