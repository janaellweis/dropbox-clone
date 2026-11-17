from dataclasses import dataclass
from enum import Enum


class SyncAction(Enum):
    CREATE_REMOTE = "create_remote"
    UPDATE_REMOTE = "update_remote"
    DOWNLOAD = "download"
    DELETE_LOCAL = "delete_local"
    DELETE_REMOTE = "delete_remote"
    RENAME_LOCAL = "rename_local"
    RENAME_REMOTE = "rename_remote"
    PRESERVE_LOCAL = "preserve_local"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class PlannedAction:
    action: SyncAction
    path: str
    file_id: str | None = None
    target_path: str | None = None


def plan_sync(
    local_files: dict,
    remote_files: dict,
    synced_files: dict,
    local_moves: dict[str, str] | None = None,
) -> list[PlannedAction]:
    actions: list[PlannedAction] = []
    local_moves = local_moves or {}

    remote_by_path = {info["path"]: file_id for file_id, info in remote_files.items()}
    synced_file_ids = {saved["file_id"] for saved in synced_files.values()}

    handled_local_paths: set[str] = set()

    for path, saved in synced_files.items():
        file_id = saved["file_id"]
        remote = remote_files.get(file_id)

        moved_path = local_moves.get(path)
        local_path = moved_path if moved_path in local_files else path
        local = local_files.get(local_path)

        if local is None and remote is None:
            continue

        if local is None:
            if remote["version"] != saved["version"] or remote["path"] != path:
                actions.append(PlannedAction(SyncAction.CONFLICT, path, file_id))
            else:
                actions.append(PlannedAction(SyncAction.DELETE_REMOTE, path, file_id))
            continue

        handled_local_paths.add(local_path)

        if remote is None:
            if local_path == path and local["hash"] != saved["hash"]:
                actions.append(PlannedAction(SyncAction.PRESERVE_LOCAL, path, file_id))
            elif local["hash"] != saved["hash"] or local_path != path:
                actions.append(PlannedAction(SyncAction.CONFLICT, local_path, file_id))
            else:
                actions.append(
                    PlannedAction(SyncAction.DELETE_LOCAL, local_path, file_id)
                )
            continue

        local_changed = local["hash"] != saved["hash"]
        remote_changed = remote["version"] != saved["version"]

        local_renamed = local_path != path
        remote_renamed = remote["path"] != path

        if local_renamed and remote_renamed:
            actions.append(PlannedAction(SyncAction.CONFLICT, path, file_id))
            continue

        if local_renamed:
            destination_taken = (
                local_path in remote_by_path and remote_by_path[local_path] != file_id
            )

            if destination_taken or remote_changed:
                actions.append(PlannedAction(SyncAction.CONFLICT, local_path, file_id))
                continue

            actions.append(
                PlannedAction(
                    SyncAction.RENAME_REMOTE,
                    path,
                    file_id,
                    target_path=local_path,
                )
            )

            if local_changed:
                actions.append(
                    PlannedAction(
                        SyncAction.UPDATE_REMOTE,
                        local_path,
                        file_id,
                    )
                )
            continue

        if remote_renamed:
            destination_taken = remote["path"] in local_files and remote["path"] != path

            if destination_taken or local_changed:
                actions.append(PlannedAction(SyncAction.CONFLICT, path, file_id))
                continue

            actions.append(
                PlannedAction(
                    SyncAction.RENAME_LOCAL,
                    path,
                    file_id,
                    target_path=remote["path"],
                )
            )

            if remote_changed:
                actions.append(
                    PlannedAction(
                        SyncAction.DOWNLOAD,
                        remote["path"],
                        file_id,
                    )
                )
            continue

        if local_changed and remote_changed:
            actions.append(PlannedAction(SyncAction.PRESERVE_LOCAL, path, file_id))
        elif local_changed:
            actions.append(PlannedAction(SyncAction.UPDATE_REMOTE, path, file_id))
        elif remote_changed:
            actions.append(PlannedAction(SyncAction.DOWNLOAD, path, file_id))

    for path in local_files:
        if path in synced_files or path in handled_local_paths:
            continue

        if path in remote_by_path:
            actions.append(PlannedAction(SyncAction.CONFLICT, path))
        else:
            actions.append(PlannedAction(SyncAction.CREATE_REMOTE, path))

    for file_id, remote in remote_files.items():
        if file_id in synced_file_ids:
            continue

        if remote["path"] not in local_files:
            actions.append(
                PlannedAction(
                    SyncAction.DOWNLOAD,
                    remote["path"],
                    file_id,
                )
            )

    return actions
