
# Business logic for file version operations.

import uuid
from fastapi import HTTPException

from server.database import SessionLocal
from server.storage import read_file
from server.modules.versions import repository


# Return all saved versions of a file.
def list_file_versions(file_id: uuid.UUID):

    with SessionLocal() as db:

        # Find the file using its UUID.
        existing_file = repository.get_active_file(db, file_id)

        # Return 404 if the file does not exist or was deleted.
        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        # Get all versions of this file, starting from version 1.
        versions = repository.get_file_versions(db, file_id)

        # Convert the database records into JSON.
        results = []

        for version in versions:
            results.append({
                "version": version.version_number,
                "content_hash": version.content_hash,
                "size": version.size,
                "created_at": version.created_at.isoformat()
            })

        return {
            "file_id": str(existing_file.id),
            "key": existing_file.key,
            "versions": results
        }


# Download a specific version of a file.
def download_file_version(file_id: uuid.UUID, version: int):

    # Version numbers must start from 1.
    if version < 1:
        raise HTTPException(
            status_code=400,
            detail="Invalid version number"
        )

    with SessionLocal() as db:

        # Find the file using its UUID.
        existing_file = repository.get_active_file(db, file_id)

        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        # Find the specific version requested.
        file_version = repository.get_file_version(db, file_id, version)

        if not file_version:
            raise HTTPException(
                status_code=404,
                detail="File version not found"
            )

        # Read the stored contents using the version's SHA-256 hash.
        try:
            content = read_file(file_version.content_hash)
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="File contents missing from storage"
            )

        return content
