
# API endpoints for file operations.
# The controller receives HTTP requests and calls the service.

import uuid

from fastapi import APIRouter, UploadFile, File, Form, Response
from server.modules.files import service

router = APIRouter()


# Upload a new file.
@router.post("/files")
async def upload_file(
    file: UploadFile = File(...),
    key: str = Form(...)
):
    # Read the uploaded file contents.
    content = await file.read()

    # Let the service handle the upload.
    return service.upload_file(content, key)


# Download the latest version of a file.
@router.get("/files/{file_id}/download")
def download_file(file_id: uuid.UUID):

    # Get the file contents from the service.
    content = service.download_file(file_id)

    # Return the contents as a binary response.
    return Response(
        content=content,
        media_type="application/octet-stream"
    )


# Update an existing file.
@router.put("/files/{file_id}")
async def update_file(
    file_id: uuid.UUID,
    file: UploadFile = File(...),
    expected_version: int = Form(...)
):
    # Read the new file contents.
    content = await file.read()

    return service.update_file(file_id, content, expected_version)


# Soft-delete a file.
@router.delete("/files/{file_id}")
def delete_file(file_id: uuid.UUID):
    return service.delete_file(file_id)


# Rename or move a file.
@router.patch("/files/{file_id}")
def rename_file(
    file_id: uuid.UUID,
    new_key: str = Form(...)
):
    return service.rename_file(file_id, new_key)


# List all active files and folders.
@router.get("/files")
def list_files():
    return service.list_files()
