
# Business logic for folder operations.

from fastapi import HTTPException

from server.database import SessionLocal
from server.modules.folders import repository


# Create an empty folder.
def create_folder(key: str):

    # Folder paths must end with a slash.
    if not key or not key.endswith("/"):
        raise HTTPException(
            status_code=400,
            detail="Folder path must end with /"
        )

    # Check that the path is relative and does not contain parent navigation.
    if key.startswith("/") or ".." in key.split("/"):
        raise HTTPException(
            status_code=400,
            detail="Invalid folder path"
        )

    with SessionLocal() as db:

        # Check if an active file or folder already uses this path.
        existing = repository.get_active_file_by_key(db, key)

        if existing:
            raise HTTPException(
                status_code=409,
                detail="A file or folder already exists at this path"
            )

        # Create a folder marker without storing file contents.
        new_folder = repository.create_folder(db, key)

        # Record the folder creation for future synchronization.
        repository.create_folder_change(
            db,
            new_folder.id,
            key
        )

        # Save the folder and change event together.
        repository.commit(db)

        return {
            "id": str(new_folder.id),
            "key": new_folder.key,
            "is_folder": True,
            "message": "Folder created successfully"
        }
