
# API endpoints for file version operations.
# The controller receives HTTP requests and calls the service.

import uuid

from fastapi import APIRouter, Response
from server.modules.versions import service

router = APIRouter()


# Return all saved versions of a file.
@router.get("/files/{file_id}/versions")
def list_file_versions(file_id: uuid.UUID):

    return service.list_file_versions(file_id)


# Download a specific version of a file.
@router.get("/files/{file_id}/versions/{version}")
def download_file_version(file_id: uuid.UUID, version: int):

    # Get the requested version's contents from the service.
    content = service.download_file_version(file_id, version)

    # Return the contents as a binary response.
    return Response(
        content=content,
        media_type="application/octet-stream"
    )
