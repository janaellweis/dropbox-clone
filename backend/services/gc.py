import hashlib
import logging
import os
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from itertools import batched
from time import monotonic

from sqlalchemy import func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from backend.core.chunk_validation import validate_chunk_hashes
from backend.core.exceptions import FileContentMissingError
from backend.db.models import FileVersion, FileVersionChunk
from backend.storage.local import LocalBlobStorage

GC_GRACE_PERIOD = timedelta(days=30)

logger = logging.getLogger(__name__)


def resolve_grace_period(value: timedelta | None = None) -> timedelta:
    if value is None:
        value = timedelta(
            days=int(os.environ.get("GC_GRACE_DAYS", str(GC_GRACE_PERIOD.days)))
        )
    if value <= timedelta(0):
        raise ValueError("GC grace period must be positive")
    return value


def chunk_lock_key(chunk_hash: str) -> int:
    """Generate a stable PostgreSQL advisory lock key from a chunk hash."""
    return int.from_bytes(
        bytes.fromhex(chunk_hash[:16]),
        byteorder="big",
        signed=True,
    )


def acquire_chunk_shared_locks(
    session: Session,
    hashes: list[str],
) -> None:
    """Hold shared locks until the transaction commits or rolls back."""
    validate_chunk_hashes(hashes)

    keys = sorted({chunk_lock_key(digest) for digest in hashes})

    with session.no_autoflush:
        for key in keys:
            session.execute(select(func.pg_advisory_xact_lock_shared(key)))


def store_chunk_with_gc_protection(
    bind: Engine | Connection,
    storage: LocalBlobStorage,
    data: bytes | memoryview,
) -> str:
    digest = hashlib.sha256(data).hexdigest()

    with Session(bind=bind) as session, session.begin():
        acquire_chunk_shared_locks(session, [digest])
        return storage.save_chunk(data)


def protect_chunks(
    session: Session,
    storage: LocalBlobStorage,
    hashes: list[str],
    *,
    allow_missing: bool = False,
) -> list[str]:
    acquire_chunk_shared_locks(session, hashes)

    missing = []
    for digest in dict.fromkeys(hashes):
        if not storage.renew_chunk(digest):
            if not allow_missing:
                raise FileContentMissingError(f"Missing chunk: {digest}")
            missing.append(digest)

    return missing


def referenced_hashes(session: Session, hashes: list[str]) -> set[str]:
    # No File join: historical and soft-deleted versions retain their content.
    return set(
        session.scalars(
            select(FileVersionChunk.chunk_hash)
            .where(FileVersionChunk.chunk_hash.in_(hashes))
            .union(
                select(FileVersion.content_hash).where(
                    FileVersion.content_hash.in_(hashes)
                )
            )
        )
    )


def collect_garbage(
    session_factory: Callable[[], Session],
    storage: LocalBlobStorage,
    *,
    grace_period: timedelta | None = None,
    batch_size: int = 100,
    max_files: int = 10000,
    max_deletions: int = 1000,
    max_seconds: float = 60,
) -> dict:

    grace_period = resolve_grace_period(grace_period)
    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    if max_files <= 0 or max_deletions <= 0 or max_seconds <= 0:
        raise ValueError("GC work limits must be positive")
    cutoff_ns = int((datetime.now(UTC) - grace_period).timestamp() * 1_000_000_000)
    deadline = monotonic() + max_seconds
    summary = {
        "complete": False,
        "scanned_file_count": 0,
        "deleted_count": 0,
        "deleted_bytes": 0,
        "busy_count": 0,
    }
    with session_factory() as session, session.no_autoflush:
        validate_gc_schema(session)
    try:
        for batch in batched(storage.iter_files(), batch_size):
            for digest, _, mtime_ns in batch:
                if (
                    summary["scanned_file_count"] >= max_files
                    or summary["deleted_count"] >= max_deletions
                    or monotonic() >= deadline
                ):
                    return summary
                summary["scanned_file_count"] += 1
                if mtime_ns > cutoff_ns:
                    continue
                try:
                    validate_chunk_hashes([digest])
                except ValueError:
                    continue
                with session_factory() as session, session.begin():
                    if session.connection().get_isolation_level() != "READ COMMITTED":
                        raise ValueError("GC requires READ COMMITTED isolation")

                    if not session.scalar(
                        select(func.pg_try_advisory_xact_lock(chunk_lock_key(digest)))
                    ):
                        summary["busy_count"] += 1
                        continue

                    metadata = storage.file_metadata(digest)
                    if metadata is None or metadata.st_mtime_ns > cutoff_ns:
                        continue
                    if referenced_hashes(session, [digest]):
                        continue
                    if storage.unlink_chunk(digest, metadata, cutoff_ns):
                        summary["deleted_count"] += 1
                        summary["deleted_bytes"] += metadata.st_size
        summary["complete"] = summary["busy_count"] == 0
        return summary
    except (SQLAlchemyError, OSError, ValueError):
        logger.exception("GC stopped with incomplete progress: %s", summary)
        raise


def validate_gc_schema(session: Session) -> None:
    """Check both reference sources even when storage has no old files."""
    for column in (FileVersion.content_hash, FileVersionChunk.chunk_hash):
        try:
            session.execute(select(column).limit(0))
        except SQLAlchemyError as exc:
            raise ValueError(
                f"GC schema check failed: cannot read {column}; report incomplete"
            ) from exc


def iter_gc_report(
    session: Session,
    storage: LocalBlobStorage,
    *,
    batch_size: int = 100,
    now: datetime | None = None,
    grace_period: timedelta | None = None,
) -> Iterator[dict]:
    """Observe only; age-eligible means a canonical regular file past the cutoff."""

    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")
    grace_period = resolve_grace_period(grace_period)
    cutoff = (now or datetime.now(UTC)) - grace_period
    cutoff_ns = int(cutoff.timestamp() * 1_000_000_000)
    scanned = age_eligible = eligible = total_size = 0
    with session.no_autoflush:
        validate_gc_schema(session)
        for batch in batched(storage.iter_files(), batch_size):
            scanned += len(batch)
            candidates = {}
            for name, size, mtime_ns in batch:
                if mtime_ns > cutoff_ns:
                    continue
                try:
                    validate_chunk_hashes([name])
                except ValueError:
                    continue
                candidates[name] = size
            age_eligible += len(candidates)
            if not candidates:
                continue
            hashes = list(candidates)
            protected = referenced_hashes(session, hashes)
            for digest, size in candidates.items():
                if digest not in protected:
                    eligible += 1
                    total_size += size
                    yield {"type": "candidate", "hash": digest, "size_bytes": size}
    yield {
        "type": "summary",
        "complete": True,
        "grace_days": grace_period.total_seconds() / 86400,
        "scanned_file_count": scanned,
        "age_eligible_count": age_eligible,
        "eligible_count": eligible,
        "total_size_bytes": total_size,
    }


def cleanup_orphan_chunk_metadata(
    session_factory: Callable[[], Session],
    storage: LocalBlobStorage,
    *,
    batch_size: int = 100,
) -> dict:
    """Remove unreferenced chunk metadata only after its file is gone."""
    from sqlalchemy import delete

    from backend.db.models import Chunk

    if type(batch_size) is not int or not 1 <= batch_size <= 1000:
        raise ValueError("batch_size must be between 1 and 1000")

    deleted = 0
    busy = 0
    last_hash = None

    while True:
        with session_factory() as session:
            statement = select(Chunk.hash).order_by(Chunk.hash).limit(batch_size)

            if last_hash is not None:
                statement = statement.where(Chunk.hash > last_hash)

            hashes = list(session.scalars(statement))

        if not hashes:
            break

        for digest in hashes:
            with session_factory() as session, session.begin():
                if session.connection().get_isolation_level() != "READ COMMITTED":
                    raise ValueError("Cleanup requires READ COMMITTED isolation")

                locked = session.scalar(
                    select(func.pg_try_advisory_xact_lock(chunk_lock_key(digest)))
                )

                if not locked:
                    busy += 1
                    continue

                if referenced_hashes(session, [digest]):
                    continue

                if storage.file_metadata(digest) is not None:
                    continue

                result = session.execute(delete(Chunk).where(Chunk.hash == digest))
                deleted += result.rowcount

        last_hash = hashes[-1]

    return {"deleted_count": deleted, "busy_count": busy}
