import os
from pathlib import Path

from client.core.exceptions import SyncConflictError


def conflicted_copy_path(path: Path, reserved: set[Path]) -> Path:
    counter = 1

    while True:
        suffix = (
            " (conflicted copy)" if counter == 1 else f" (conflicted copy {counter})"
        )

        conflict_path = path.with_name(f"{path.stem}{suffix}{path.suffix}")

        if not os.path.lexists(conflict_path) and not any(
            conflict_path == entry
            or conflict_path in entry.parents
            or entry in conflict_path.parents
            for entry in reserved
        ):
            return conflict_path

        counter += 1


def preserve_conflicted_copy(path: Path, conflict_path: Path) -> None:
    """Move to a fixed name without overwriting; interrupted links are retryable."""
    if path.is_symlink() or conflict_path.is_symlink():
        raise SyncConflictError("Cannot preserve a symlink as a conflict copy")
    if path.parent != conflict_path.parent or path == conflict_path:
        raise ValueError("Conflict preservation requires a distinct sibling path")
    source_exists = path.exists()
    with (path if source_exists else conflict_path).open("rb") as source:
        os.fsync(source.fileno())
    if source_exists:
        try:
            os.link(path, conflict_path, follow_symlinks=False)
        except FileExistsError:
            if conflict_path.is_symlink() or not path.samefile(conflict_path):
                raise SyncConflictError(
                    f"Conflict destination occupied: {conflict_path}"
                )
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        # Make the new name durable before removing the original name.
        os.fsync(directory_fd)
        if path.exists():
            if (
                path.is_symlink()
                or conflict_path.is_symlink()
                or not path.samefile(conflict_path)
            ):
                raise SyncConflictError(f"Paths changed during preservation: {path}")
            path.unlink()
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
