import hashlib
import re
import uuid
from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from backend.core.exceptions import FileContentMissingError
from backend.db.models import Chunk, FileVersionChunk
from backend.services.gc import install_chunk, protect_chunks
from backend.storage.local import LocalBlobStorage
from shared.chunking import Chunk as ContentChunk


def validate_chunk_hashes(chunk_hashes: list[str]) -> list[str]:
    """Check the entire list before any hash can become a storage path."""
    if any(
        not isinstance(chunk_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", chunk_hash) is None
        for chunk_hash in chunk_hashes
    ):
        raise ValueError("Invalid SHA-256 chunk hash")
    return chunk_hashes


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
    return [install_chunk(session.get_bind(), storage, chunk.data) for chunk in chunks]


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
