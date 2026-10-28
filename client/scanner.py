
import hashlib
from pathlib import Path


def scan_folder(folder: str) -> dict:
    root = Path(folder)
    files = {}

    for path in root.rglob("*"):
        if not path.is_file():
            continue

        relative_path = path.relative_to(root).as_posix()

        if (
            relative_path == ".sync_state.json"
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or path.name.startswith(".sync-download-")
            or path.name.endswith(".sync-tmp")
        ):
            continue

        hasher = hashlib.sha256()

        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                hasher.update(chunk)

        files[relative_path] = {
            "hash": hasher.hexdigest(),
            "size": path.stat().st_size,
        }

    return files
