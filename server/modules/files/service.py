
# Business logic for file operations.

import uuid
from fastapi import HTTPException

from server.database import SessionLocal
from server.storage.local import save_file, read_file
from server.modules.files import repository


# Check that a file path is valid.
def validate_file_path(key: str):
    if not key or key.startswith("/") or key.endswith("/") or ".." in key.split("/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file path"
        )


# Upload a new file.
def upload_file(content: bytes, key: str):
    validate_file_path(key)

    with SessionLocal() as db:

        # Check whether an active file or folder already uses this path.
        existing = repository.get_active_file_by_key(db, key)

        if existing:
            raise HTTPException(
                status_code=409,
                detail="An active file already exists at this path"
            )

        # Save the actual bytes to disk and obtain their hash.
        content_hash = save_file(content)

        # Create the file metadata record.
        new_file = repository.create_file(db, key)

        # Record the initial file version.
        repository.create_version(
            db, new_file.id, 1, content_hash, len(content)
        )

        # Record a CREATE event for future synchronization.
        repository.create_change(
            db, new_file.id, "CREATE", key, version_number=1
        )

        # Commit all database records in one transaction.
        repository.commit(db)

        return {
            "id": str(new_file.id),
            "key": key,
            "version": 1,
            "content_hash": content_hash
        }


# Download the latest version of a file.
def download_file(file_id: uuid.UUID):
    with SessionLocal() as db:

        # Find the file and make sure it is not deleted or a folder.
        existing_file = repository.get_active_file(db, file_id)

        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        # Find the most recent version.
        latest_version = repository.get_latest_version(db, file_id)

        if not latest_version:
            raise HTTPException(
                status_code=404,
                detail="File version not found"
            )

        # Read the actual file bytes from disk.
        try:
            content = read_file(latest_version.content_hash)
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="File contents missing from storage"
            )

        return content


# Update an existing file by creating a new version.
def update_file(file_id: uuid.UUID, content: bytes, expected_version: int):
    with SessionLocal() as db:

        # Lock the file to prevent simultaneous modifications.
        existing_file = repository.get_active_file(db, file_id, lock=True)

        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        latest_version = repository.get_latest_version(db, file_id)

        if not latest_version:
            raise HTTPException(
                status_code=404,
                detail="File version not found"
            )

        if expected_version < 1:
            raise HTTPException(
                status_code=400,
                detail="Invalid expected version"
            )

        # Prevent overwriting a newer version.
        if expected_version != latest_version.version_number:
            raise HTTPException(
                status_code=409,
                detail="File was modified by another client"
            )

        # Save the updated contents using SHA-256 hashing.
        content_hash = save_file(content)
        new_version_number = latest_version.version_number + 1

        # Keep the previous versions.
        repository.create_version(
            db,
            existing_file.id,
            new_version_number,
            content_hash,
            len(content)
        )

        # Record an UPDATE event for synchronization.
        repository.create_change(
            db,
            existing_file.id,
            "UPDATE",
            existing_file.key,
            version_number=new_version_number
        )

        repository.commit(db)

        return {
            "id": str(existing_file.id),
            "key": existing_file.key,
            "version": new_version_number,
            "content_hash": content_hash
        }


# Soft-delete a file without removing its previous versions.
def delete_file(file_id: uuid.UUID):
    with SessionLocal() as db:

        existing_file = repository.get_active_file(db, file_id, lock=True)

        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        latest_version = repository.get_latest_version(db, file_id)

        if not latest_version:
            raise HTTPException(
                status_code=404,
                detail="File version not found"
            )

        # Mark the file as deleted.
        existing_file.is_deleted = True

        # Record the deletion for future synchronization.
        repository.create_change(
            db,
            existing_file.id,
            "DELETE",
            existing_file.key,
            version_number=latest_version.version_number
        )

        repository.commit(db)

        return {
            "id": str(existing_file.id),
            "key": existing_file.key,
            "is_deleted": True,
            "message": "File deleted successfully"
        }


# Rename or move a file without changing its contents.
def rename_file(file_id: uuid.UUID, new_key: str):
    validate_file_path(new_key)

    with SessionLocal() as db:

        existing_file = repository.get_active_file(db, file_id, lock=True)

        if not existing_file:
            raise HTTPException(
                status_code=404,
                detail="File not found"
            )

        # Check that another active file or folder does not use this path.
        duplicate = repository.get_active_file_by_key(
            db, new_key, exclude_id=file_id
        )

        if duplicate:
            raise HTTPException(
                status_code=409,
                detail="An active file already exists at this path"
            )

        old_key = existing_file.key

        # Change the path without changing the file UUID.
        existing_file.key = new_key

        # Record the previous and new paths.
        repository.create_change(
            db,
            existing_file.id,
            "RENAME",
            new_key,
            old_path=old_key
        )

        repository.commit(db)

        return {
            "id": str(existing_file.id),
            "old_key": old_key,
            "new_key": new_key,
            "message": "File renamed successfully"
        }


# List all active files and folders.
def list_files():
    with SessionLocal() as db:

        files = repository.get_all_active_files(db)
        results = []

        for file in files:
            latest_version = repository.get_latest_version(db, file.id)

            results.append({
                "id": str(file.id),
                "key": file.key,
                "version": latest_version.version_number if latest_version else None,
                "content_hash": latest_version.content_hash if latest_version else None,
                "is_folder": file.is_folder,
                "size": latest_version.size if latest_version else None
            })

        return {"files": results}
