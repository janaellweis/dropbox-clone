class SyncError(Exception):
    """Base exception for client synchronization errors."""


class SyncConflictError(SyncError):
    """A synchronization conflict that requires resolution."""


class DownloadVerificationError(SyncError):
    """Downloaded content or metadata failed verification."""


class UnsafeSyncPathError(SyncError, ValueError):
    """A path is unsafe for local synchronization."""


class FileChangedDuringScanError(SyncError):
    """A file changed while its contents were being scanned."""
