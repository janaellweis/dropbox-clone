
# Database operations for the folders module.

from server.models import File, ChangeJournal


# Check if an active file or folder already uses this path.
def get_active_file_by_key(db, key):
    return (
        db.query(File)
        .filter(
            File.key == key,
            File.is_deleted.is_(False)
        )
        .first()
    )


# Create a folder marker without storing file contents.
def create_folder(db, key):
    new_folder = File(
        key=key,
        is_folder=True
    )

    db.add(new_folder)

    # Generate the folder ID before recording the change.
    db.flush()

    return new_folder


# Record the folder creation for future synchronization.
def create_folder_change(db, folder_id, key):
    db.add(
        ChangeJournal(
            file_id=folder_id,
            action="CREATE_FOLDER",
            path=key
        )
    )


# Save the folder and change event together.
def commit(db):
    db.commit()
