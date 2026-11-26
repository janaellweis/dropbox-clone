import hashlib
import os
from pathlib import Path

from client.core.exceptions import UnsafeSyncPathError


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

    for parent in destination.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise UnsafeSyncPathError(f"Symbolic link in sync path: {relative_path}")

    return destination


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def file_changed(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    )
