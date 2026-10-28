from client.scanner import scan_folder
from client.state import load_state


def detect_local_changes(folder: str) -> dict:
    current_files = scan_folder(folder)
    previous_files = load_state(folder)["files"]

    added = []
    modified = []
    deleted = []

    for path, metadata in current_files.items():
        if path not in previous_files:
            added.append(path)
        elif metadata["hash"] != previous_files[path]["hash"]:
            modified.append(path)

    for path in previous_files:
        if path not in current_files:
            deleted.append(path)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
    }


def compare_file_versions(
    local_hash: str,
    saved_hash: str,
    saved_version: int,
    remote_version: int,
) -> str:
    local_changed = local_hash != saved_hash
    remote_changed = remote_version != saved_version

    if local_changed and remote_changed:
        return "conflict"

    if local_changed:
        return "upload"

    if remote_changed:
        return "download"

    return "unchanged"


def detect_local_renames(current: dict, previous: dict) -> list:
    added = {
        path: info
        for path, info in current.items()
        if path not in previous
    }

    deleted = {
        path: info
        for path, info in previous.items()
        if path not in current
    }

    renames = []

    for old_path, old_info in deleted.items():
        matches = [
            path
            for path, info in added.items()
            if info["hash"] == old_info["hash"]
        ]

        if len(matches) == 1:
            new_path = matches[0]

            renames.append((old_path, new_path))
            del added[new_path]

    return renames
