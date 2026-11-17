from pathlib import Path
from typing import Any

from client.api import DropboxClient
from client.core.exceptions import SyncConflictError
from client.services.conflicts import preserve_conflicted_copy
from client.services.planner import PlannedAction, SyncAction
from client.storage.downloads import download_verified, file_hash, safe_local_path


class SyncExecutor:
    def __init__(self, api: DropboxClient, root: Path) -> None:
        self.api = api
        self.root = root.resolve()

    def execute(
        self,
        action: PlannedAction,
        expected_version: int | None = None,
        expected_hash: str | None = None,
        request_id: str | None = None,
        snapshot_path: Path | None = None,
        download_hash: str | None = None,
        download_size: int | None = None,
    ) -> dict[str, Any]:
        match action.action:
            case SyncAction.CREATE_REMOTE:
                if snapshot_path is None:
                    raise ValueError("CREATE_REMOTE requires a snapshot")

                self._verify_hash(snapshot_path, self._require_hash(expected_hash))

                return self.api.push(
                    str(snapshot_path),
                    remote_key=action.path,
                    request_id=request_id,
                )
            case SyncAction.UPDATE_REMOTE:
                if snapshot_path is None:
                    raise ValueError("UPDATE_REMOTE requires a snapshot")

                self._verify_hash(snapshot_path, self._require_hash(expected_hash))

                return self.api.update_file(
                    file_id=self._require_file_id(action),
                    local_path=str(snapshot_path),
                    expected_version=self._require_version(expected_version),
                    request_id=request_id,
                )

            case SyncAction.DELETE_REMOTE:
                return self.api.delete_file(
                    file_id=self._require_file_id(action),
                    expected_version=self._require_version(expected_version),
                    request_id=request_id,
                )

            case SyncAction.RENAME_REMOTE:
                if action.target_path is None:
                    raise ValueError("Rename requires a target path")

                safe_local_path(self.root, action.target_path)

                return self.api.rename_file(
                    file_id=self._require_file_id(action),
                    expected_key=action.path,
                    new_key=action.target_path,
                    expected_version=self._require_version(expected_version),
                    request_id=request_id,
                )

            case SyncAction.DOWNLOAD:
                return download_verified(
                    self.api,
                    self.root,
                    action.path,
                    self._require_file_id(action),
                    self._require_version(expected_version),
                    expected_local_hash=expected_hash,
                    expected_content_hash=download_hash,
                    expected_size=download_size,
                )

            case SyncAction.DELETE_LOCAL:
                destination = safe_local_path(self.root, action.path)
                self._verify_hash(
                    destination,
                    self._require_hash(expected_hash),
                )

                destination.unlink()
                return {}

            case SyncAction.RENAME_LOCAL:
                if action.target_path is None:
                    raise ValueError("Rename requires a target path")

                source = safe_local_path(self.root, action.path)
                destination = safe_local_path(
                    self.root,
                    action.target_path,
                )

                self._verify_hash(
                    source,
                    self._require_hash(expected_hash),
                )

                if destination.exists():
                    raise SyncConflictError(
                        f"Rename destination already exists: {action.target_path}"
                    )

                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
                return {}

            case SyncAction.PRESERVE_LOCAL:
                if action.target_path is None:
                    raise ValueError("Preservation requires a target path")
                source = safe_local_path(self.root, action.path)
                destination = safe_local_path(self.root, action.target_path)
                if source.is_symlink() or destination.is_symlink():
                    raise SyncConflictError("Cannot preserve a symlink")
                self._verify_hash(
                    source if source.exists() else destination,
                    self._require_hash(expected_hash),
                )
                preserve_conflicted_copy(source, destination)
                return {}

            case SyncAction.CONFLICT:
                raise SyncConflictError(f"Synchronization conflict: {action.path}")

            case _:
                raise ValueError(f"Unsupported executor action: {action.action.value}")

    @staticmethod
    def _verify_hash(path: Path, expected_hash: str | None) -> None:
        if not path.is_file():
            raise SyncConflictError(f"Local file missing: {path}")

        if expected_hash is None:
            return

        if file_hash(path) != expected_hash:
            raise SyncConflictError(
                f"Local file changed during synchronization: {path}"
            )

    @staticmethod
    def _require_file_id(action: PlannedAction) -> str:
        if action.file_id is None:
            raise ValueError(f"{action.action.value} requires a file ID")
        return action.file_id

    @staticmethod
    def _require_version(version: int | None) -> int:
        if version is None:
            raise ValueError("Operation requires an expected version")
        return version

    @staticmethod
    def _require_hash(value: str | None) -> str:
        if value is None:
            raise ValueError("Local operation requires an expected hash")
        return value
