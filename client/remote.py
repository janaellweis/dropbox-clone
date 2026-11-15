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
