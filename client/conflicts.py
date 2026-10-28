from pathlib import Path


def preserve_conflicted_copy(path: Path) -> Path:
    counter = 1

    while True:
        suffix = (
            " (conflicted copy)"
            if counter == 1
            else f" (conflicted copy {counter})"
        )

        conflict_path = path.with_name(
            f"{path.stem}{suffix}{path.suffix}"
        )

        if not conflict_path.exists():
            path.rename(conflict_path)
            return conflict_path

        counter += 1


