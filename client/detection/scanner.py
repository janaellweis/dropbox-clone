import hashlib
from pathlib import Path

from client.core.exceptions import FileChangedDuringScanError, UnsafeSyncPathError
from client.storage.state import STATE_FILE


def should_ignore(path: Path) -> bool:
    """Apply the shared file-inclusion policy to a path relative to the sync root."""
    return (
        path.as_posix() == STATE_FILE
        or ".sync-pending" in path.parts
        or "__pycache__" in path.parts
        or path.suffix == ".pyc"
        or path.name.startswith((".sync-download-", ".sync-state-", ".snapshot-"))
        or path.name.endswith(".sync-tmp")
    )


def scan_file(root: Path, relative_path: str) -> dict | None:
    root = root.resolve()
    relative = Path(relative_path)

    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise UnsafeSyncPathError(f"Unsafe sync path: {relative_path}")

    if should_ignore(relative):
        return None

    path = root / relative

    if not path.resolve().is_relative_to(root):
        raise UnsafeSyncPathError(f"Path escapes sync folder: {relative_path}")

    try:
        if not path.is_file():
            return None
        before = path.stat()
        hasher = hashlib.sha256()

        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                hasher.update(chunk)

        after = path.stat()

    except FileNotFoundError:
        return None

    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or before.st_ctime_ns != after.st_ctime_ns
    ):
        raise FileChangedDuringScanError(f"File changed during scan: {relative_path}")

    return {
        "hash": hasher.hexdigest(),
        "size": after.st_size,
    }


def scan_folder(folder: str) -> dict:
    root = Path(folder).resolve()
    files = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(root).as_posix()
        metadata = scan_file(root, relative_path)

        if metadata is not None:
            files[relative_path] = metadata

    return files


def update_snapshot(
    root: Path,
    snapshot: dict,
    changed_paths: set[str] | frozenset[str],
) -> dict:
    updated = snapshot.copy()

    for path in changed_paths:
        metadata = scan_file(root, path)

        if metadata is None:
            updated.pop(path, None)
        else:
            updated[path] = metadata

    return updated
