
from pathlib import Path

from client.api import DropboxClient
from client.changes import (
    detect_local_renames,
)
from client.conflicts import preserve_conflicted_copy
from client.downloads import download_verified, safe_local_path
from client.remote import build_remote_snapshot, fetch_remote_updates
from client.scanner import scan_folder
from client.state import load_state, save_state


def reconcile_existing_files(api, folder, root, state, current, remote, remote_by_path):
    for path, saved in list(state["files"].items()):
        local = current.get(path)
        remote_file = remote.get(saved["file_id"])

        if local is None:
            if remote_file is None:
                del state["files"][path]
                save_state(folder, state)
                continue

            if remote_file["path"] != path:
                raise RuntimeError(
                    f"Rename conflict for {path}: remote path changed"
                )

            api.delete_file(saved["file_id"], saved["version"])

            # Update our in-memory remote snapshot too.
            remote.pop(saved["file_id"], None)
            remote_by_path.pop(path, None)

            del state["files"][path]
            save_state(folder, state)

            print(f"Deleted remotely: {path}")
            continue

        local_changed = local["hash"] != saved["hash"]

        if remote_file is None:
            if local_changed:
                raise RuntimeError(
                    f"Conflict: {path} changed locally but was deleted remotely"
                )

            (root / path).unlink()
            del state["files"][path]
            save_state(folder, state)
            print(f"Deleted locally: {path}")
            continue

        remote_changed = remote_file["version"] != saved["version"]
        remote_renamed = remote_file["path"] != path

        if local_changed and (remote_changed or remote_renamed):
            conflict_path = preserve_conflicted_copy(root / path)

            print(
                f"Conflict detected: {path}. "
                f"Preserved local changes as {conflict_path.name}"
            )

            # Restore the server version of the original file.
            remote_path = remote_file["path"]
            destination = root / remote_path

            if destination.exists():
                raise FileExistsError(
                    f"Conflict: destination already exists: {remote_path}"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)

                        # Download and verify the server version.
            downloaded = download_verified(
                api,
                root,
                remote_path,
                saved["file_id"],
                remote_file["version"],
            )

            # Register the downloaded server version.
            state["files"].pop(path)
            state["files"][remote_path] = downloaded

            save_state(folder, state)

            print(f"Restored server version: {remote_path}")
            continue

        if remote_renamed:
            new_path = remote_file["path"]
            destination = root / new_path

            if destination.exists():
                raise FileExistsError(
                    f"Rename conflict: {new_path} already exists locally"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            (root / path).rename(destination)

            state["files"][new_path] = state["files"].pop(path)
            state["files"][new_path]["version"] = remote_file["version"]

            save_state(folder, state)
            print(f"Renamed locally: {path} -> {new_path}")
            continue

        if local_changed:
            result = api.update_file(
                saved["file_id"],
                str(root / path),
                saved["version"],
            )

            state["files"][path] = {
                "file_id": saved["file_id"],
                "version": result["version"],
                "hash": result["content_hash"],
                "size": result["size"],
            }
            save_state(folder, state)
            print(f"Uploaded update: {path}")

        elif remote_changed:
            downloaded = download_verified(
                api,
                root,
                path,
                saved["file_id"],
                remote_file["version"],
            )

            state["files"][path] = downloaded
            save_state(folder, state)

            print(f"Downloaded update: {path}")

def sync_folder(api: DropboxClient, folder: str) -> None:
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=True)

    state = load_state(folder)

    # 1. Fetch the complete remote snapshot.
    events, checkpoint = fetch_remote_updates(api, 0)
    remote = build_remote_snapshot(events)

    remote_by_path = {
        metadata["path"]: {
            "file_id": file_id,
            **metadata,
        }
        for file_id, metadata in remote.items()
    }

    # 2. Scan the local folder.
    current = scan_folder(folder)
        # Handle local renames before deletions or uploads.
    renames = detect_local_renames(current, state["files"])

    for old_path, new_path in renames:
        saved = state["files"][old_path]
        remote_file = remote.get(saved["file_id"])

        if remote_file is None:
            raise RuntimeError(
                f"Rename conflict: {old_path} was deleted remotely"
            )

        if (
            remote_file["path"] != old_path
            or remote_file["version"] != saved["version"]
        ):
            raise RuntimeError(
                f"Rename conflict: {old_path} changed remotely"
            )

        api.rename_file(
            file_id=saved["file_id"],
            expected_key=old_path,
            new_key=new_path,
            expected_version=saved["version"],
        )

        state["files"][new_path] = state["files"].pop(old_path)
        remote[saved["file_id"]]["path"] = new_path
        remote_by_path.pop(old_path, None)
        remote_by_path[new_path] = {
            "file_id": saved["file_id"],
            **remote[saved["file_id"]],
        }

        save_state(folder, state)
        print(f"Renamed remotely: {old_path} -> {new_path}")

    # 3. Process files we have synchronized before.
    reconcile_existing_files(
        api, folder, root, state, current, remote, remote_by_path
    )

    # 4. Handle files not yet registered in this client.
    current = scan_folder(folder)

    for path, local in current.items():
        if path in state["files"]:
            continue

        if path in remote_by_path:
            raise RuntimeError(
                f"Path conflict: {path} exists locally and remotely"
            )

        result = api.push(str(root / path), path)
        metadata = api.get_file_metadata(result["file_id"])

        state["files"][path] = {
            "file_id": result["file_id"],
            "version": metadata["version"],
            "hash": metadata["content_hash"],
            "size": metadata["size"],
        }
        save_state(folder, state)
        print(f"Uploaded new file: {path}")

    # 5. Download remote files this client has never seen.
    current = scan_folder(folder)

    known_ids = {
        metadata["file_id"]
        for metadata in state["files"].values()
    }

    for file_id, info in remote.items():
        if file_id in known_ids:
            continue

        path = info["path"]

        if path in current:
            raise RuntimeError(f"Path conflict: {path}")

        destination = safe_local_path(root, path)

        if destination.exists():
            raise FileExistsError(
                f"Destination already exists: {path}"
            )

        downloaded = download_verified(
            api,
            root,
            path,
            file_id,
            info["version"],
        )

        state["files"][path] = downloaded
        save_state(folder, state)

        print(f"Downloaded new file: {path}")

    # 6. Record the processed journal checkpoint.
    state["checkpoint"] = checkpoint
    save_state(folder, state)

    print("Sync completed.")