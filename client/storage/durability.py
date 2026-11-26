import os
from pathlib import Path


def sync_directories(directory: Path, root: Path) -> None:
    """Persist directory entries from the given directory to the sync root."""
    if os.name != "posix":
        return

    directory = directory.resolve()
    root = root.resolve()

    if not directory.is_relative_to(root):
        raise ValueError("Directory is outside the sync root")

    while True:
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

        if directory == root:
            return

        directory = directory.parent


def sync_existing_directories(directory: Path, root: Path) -> None:
    """Persist directory changes even when parent directories were removed."""
    while directory != root and not directory.exists():
        if directory.is_symlink():
            raise ValueError(f"Unsafe directory: {directory}")
        directory = directory.parent

    sync_directories(directory, root)


def sync_file(path: Path) -> None:
    """Persist the contents of an existing file."""
    with path.open("rb") as file:
        os.fsync(file.fileno())
