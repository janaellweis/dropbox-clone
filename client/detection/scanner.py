from pathlib import Path

from client.core.exceptions import FileChangedDuringScanError, UnsafeSyncPathError
from client.storage.filesystem import file_changed, file_hash
from client.storage.schema import DATABASE_FILE, STATE_FILE


def should_ignore(path: Path) -> bool:
    """Apply the shared file-inclusion policy to a path relative to the sync root."""
    return (
        path.as_posix()
        in {
            STATE_FILE,
            DATABASE_FILE,
            f"{DATABASE_FILE}-wal",
            f"{DATABASE_FILE}-shm",
            f"{DATABASE_FILE}-journal",
        }
        or ".sync-pending" in path.parts
        or "__pycache__" in path.parts
        or path.suffix == ".pyc"
        or path.name.startswith((".sync-download-", ".sync-state-", ".snapshot-"))
        or path.name.endswith(".sync-tmp")
    )


def is_unsafe_relative_path(relative: Path) -> bool:
    return relative.is_absolute() or not relative.parts or ".." in relative.parts


def scan_file(root: Path, relative_path: str) -> dict | None:
    root = root.resolve()
    relative = Path(relative_path)

    if is_unsafe_relative_path(relative):
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
        digest = file_hash(path)

        after = path.stat()

    except FileNotFoundError:
        return None

    if file_changed(before, after):
        raise FileChangedDuringScanError(f"File changed during scan: {relative_path}")

    return {"hash": digest, "size": after.st_size}


def scan_tree(root: Path, folder: Path) -> dict:
    """Scan every file under `folder`; keys are paths relative to `root`."""
    files = {}

    for path in folder.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(root).as_posix()
        metadata = scan_file(root, relative_path)

        if metadata is not None:
            files[relative_path] = metadata

    return files


def scan_folder(folder: str) -> dict:
    root = Path(folder).resolve()
    return scan_tree(root, root)


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


def update_directories(
    root: Path,
    snapshot: dict,
    directories: set[str] | frozenset[str],
) -> dict:
    root = root.resolve()
    updated = snapshot.copy()

    for directory in directories:
        relative = Path(directory)

        if is_unsafe_relative_path(relative):
            raise UnsafeSyncPathError(f"Unsafe directory: {directory}")

        if should_ignore(relative):
            continue

        target = root / relative
        if not target.resolve().is_relative_to(root):
            raise UnsafeSyncPathError(f"Directory escapes sync folder: {directory}")

        relative_path = relative.as_posix()
        prefix = relative_path.rstrip("/") + "/"

        for name in list(updated):
            if name == relative_path or name.startswith(prefix):
                updated.pop(name)

        if not target.is_dir():
            metadata = scan_file(root, relative_path)
            if metadata is not None:
                updated[relative_path] = metadata
            continue

        updated.update(scan_tree(root, target))

    return updated
