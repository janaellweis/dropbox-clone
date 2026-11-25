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
                snapshot = self._require_snapshot(action, snapshot_path)
                self._verify_unchanged(snapshot, expected_hash)
                return self.api.push(
                    str(snapshot),
                    remote_key=action.path,
                    request_id=request_id,
                )

            case SyncAction.UPDATE_REMOTE:
                snapshot = self._require_snapshot(action, snapshot_path)
                self._verify_unchanged(snapshot, expected_hash)
                return self.api.update_file(
                    file_id=self._require_file_id(action),
                    local_path=str(snapshot),
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
                target = self._require_target(action, "Rename")
                safe_local_path(self.root, target)  # validates the path; result unused
                return self.api.rename_file(
                    file_id=self._require_file_id(action),
                    expected_key=action.path,
                    new_key=target,
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
                self._verify_unchanged(destination, expected_hash)
                destination.unlink()
                return {}

            case SyncAction.RENAME_LOCAL:
                target = self._require_target(action, "Rename")
                source = safe_local_path(self.root, action.path)
                destination = safe_local_path(self.root, target)
                self._verify_unchanged(source, expected_hash)
                if destination.exists():
                    raise SyncConflictError(
                        f"Rename destination already exists: {target}"
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                source.rename(destination)
                return {}

            case SyncAction.PRESERVE_LOCAL:
                target = self._require_target(action, "Preservation")
                source = safe_local_path(self.root, action.path)
                destination = safe_local_path(self.root, target)
                if source.is_symlink() or destination.is_symlink():
                    raise SyncConflictError("Cannot preserve a symlink")
                self._verify_unchanged(
                    source if source.exists() else destination, expected_hash
                )
                preserve_conflicted_copy(source, destination)
                return {}

            case SyncAction.CONFLICT:
                raise SyncConflictError(f"Synchronization conflict: {action.path}")

            case _:
                raise ValueError(f"Unsupported executor action: {action.action.value}")

    @staticmethod
    def _verify_unchanged(path: Path, expected_hash: str | None) -> None:
        if expected_hash is None:
            raise ValueError("Local operation requires an expected hash")

        if not path.is_file():
            raise SyncConflictError(f"Local file missing: {path}")

        if file_hash(path) != expected_hash:
            raise SyncConflictError(
                f"Local file changed during synchronization: {path}"
            )

    @staticmethod
    def _require_snapshot(action: PlannedAction, snapshot_path: Path | None) -> Path:
        if snapshot_path is None:
            raise ValueError(f"{action.action.name} requires a snapshot")
        return snapshot_path

    @staticmethod
    def _require_target(action: PlannedAction, operation: str) -> str:
        if action.target_path is None:
            raise ValueError(f"{operation} requires a target path")
        return action.target_path

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
