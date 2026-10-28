import hashlib
import os
import tempfile
from pathlib import Path

from client.api import DropboxClient


def safe_local_path(root: Path, relative_path: str) -> Path:
    path = Path(relative_path)

    if (
        path.is_absolute()
        or not relative_path
        or ".." in path.parts
        or path == Path(".")
    ):
        raise ValueError(f"Unsafe sync path: {relative_path}")

    destination = root / path

    if not destination.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Path escapes sync folder: {relative_path}")

    return destination


def download_verified(
    api: DropboxClient,
    root: Path,
    relative_path: str,
    file_id: str,
    expected_version: int,
) -> dict:
    destination = safe_local_path(root, relative_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fd, temporary_name = tempfile.mkstemp(
        prefix=".sync-download-",
        dir=destination.parent,
    )
    os.close(fd)

    temporary = Path(temporary_name)

    try:
        api.pull(file_id, str(temporary))

        metadata = api.get_file_metadata(file_id)

        downloaded_hash = hashlib.sha256(
            temporary.read_bytes()
        ).hexdigest()

        if downloaded_hash != metadata["content_hash"]:
            raise RuntimeError(
                f"Downloaded content hash mismatch: {relative_path}"
            )

        if metadata["version"] != expected_version:
            raise RuntimeError(
                f"Remote file changed during download: {relative_path}"
            )

        if metadata["key"] != relative_path:
            raise RuntimeError(
                f"Remote file renamed during download: {relative_path}"
            )

        temporary.replace(destination)

        return {
            "file_id": file_id,
            "version": metadata["version"],
            "hash": metadata["content_hash"],
            "size": metadata["size"],
        }

    finally:
        temporary.unlink(missing_ok=True)


