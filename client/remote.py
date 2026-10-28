from client.api import DropboxClient


def fetch_remote_updates(api: DropboxClient, checkpoint: int) -> tuple[list, int]:
    updates = []
    current_checkpoint = checkpoint

    while True:
        response = api.get_updates(since=current_checkpoint)

        updates.extend(response["updates"])
        current_checkpoint = response["next_checkpoint"]

        if not response["has_more"]:
            break

    return updates, current_checkpoint


def build_remote_snapshot(updates: list) -> dict:
    remote_files = {}

    for event in updates:
        file_id = event["file_id"]
        action = event["action"]

        if action == "DELETE":
            remote_files.pop(file_id, None)

        elif action in ("CREATE", "UPDATE", "RENAME"):
            remote_files[file_id] = {
                "path": event["path"],
                "version": event["version_number"],
            }

    return remote_files


