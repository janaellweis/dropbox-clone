import hashlib
import os
import tempfile
from pathlib import Path
from uuid import UUID

from client.core.exceptions import FileChangedDuringScanError

SNAPSHOT_DIR = ".sync-pending"
CHUNK_SIZE = 1024 * 1024


def create_snapshot(
    root: Path,
    source: Path,
    request_id: UUID,
) -> tuple[Path, str, int]:
    root = root.resolve()
    source = source.resolve()

    if not source.is_relative_to(root):
        raise ValueError("Source file is outside the sync folder")

    directory = root / SNAPSHOT_DIR
    directory.mkdir(exist_ok=True)

    destination = directory / f"{request_id}.blob"

    if destination.exists():
        raise FileExistsError(f"Snapshot already exists: {destination}")

    temporary = None

    try:
        before = source.stat()
        hasher = hashlib.sha256()
        size = 0

        with source.open("rb") as original:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=directory,
                prefix=".snapshot-",
                delete=False,
            ) as snapshot:
                temporary = Path(snapshot.name)

                for chunk in iter(
                    lambda: original.read(CHUNK_SIZE),
                    b"",
                ):
                    snapshot.write(chunk)
                    hasher.update(chunk)
                    size += len(chunk)

                snapshot.flush()
                os.fsync(snapshot.fileno())

        after = source.stat()

        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
        ):
            raise FileChangedDuringScanError(
                f"File changed while creating snapshot: {source}"
            )

        temporary.replace(destination)

        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

        return destination, hasher.hexdigest(), size

    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
