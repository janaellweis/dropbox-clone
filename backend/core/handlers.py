from collections.abc import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from backend.core.exceptions import (
    FileAlreadyExistsError,
    FileContentMissingError,
    FileRecordNotFoundError,
    FileVersionConflictError,
    InvalidFilePathError,
    InvalidFileVersionError,
    RequestConflictError,
)


def make_handler(status_code: int) -> Callable:
    def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"detail": str(exc)},
        )

    return handler


def register_exception_handlers(app: FastAPI) -> None:
    error_statuses = {
        FileRecordNotFoundError: 404,
        FileVersionConflictError: 409,
        RequestConflictError: 409,
        InvalidFilePathError: 400,
        FileContentMissingError: 500,
        FileAlreadyExistsError: 409,
        InvalidFileVersionError: 400,
    }

    for exception_class, status_code in error_statuses.items():
        app.add_exception_handler(
            exception_class,
            make_handler(status_code),
        )