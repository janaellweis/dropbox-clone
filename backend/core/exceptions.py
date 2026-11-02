class AppError(Exception):
    """Base class for expected application errors."""


class FileRecordNotFoundError(AppError):
    """Requested file or version does not exist."""


class FileVersionConflictError(AppError):
    """The client is using an outdated file version."""


class RequestConflictError(AppError):
    """A request ID was reused with different parameters."""


class InvalidFilePathError(AppError):
    """The supplied file path is invalid."""


class FileContentMissingError(AppError):
    """File metadata exists, but its blob is missing."""


class FileAlreadyExistsError(AppError):
    """A file with the requested path already exists."""


class InvalidFileVersionError(AppError):
    pass
