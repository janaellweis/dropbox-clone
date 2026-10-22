# Database operations for the files module.

from server.models import File, FileVersion, ChangeJournal


# Find an active file using its UUID.
# Lock it when we are going to modify it.
def get_active_file(db, file_id, lock=False):
    query = db.query(File).filter(
        File.id == file_id,
        File.is_deleted.is_(False),
        File.is_folder.is_(False)
    )

    if lock:
        query = query.with_for_update()

    return query.first()


# Check whether an active file or folder uses a path.
def get_active_file_by_key(db, key, exclude_id=None):
    query = db.query(File).filter(
        File.key == key,
        File.is_deleted.is_(False)
    )

    if exclude_id is not None:
        query = query.filter(File.id != exclude_id)

    return query.first()


# Get the latest version of a file.
def get_latest_version(db, file_id):
    return (
        db.query(FileVersion)
        .filter(FileVersion.file_id == file_id)
        .order_by(FileVersion.version_number.desc())
        .first()
    )


# Get all active files and folders, ordered by path.
def get_all_active_files(db):
    return (
        db.query(File)
        .filter(File.is_deleted.is_(False))
        .order_by(File.key.asc())
        .all()
    )


# Create a new file metadata record.
def create_file(db, key):
    new_file = File(key=key)
    db.add(new_file)

    # Generate the file ID before creating its version.
    db.flush()

    return new_file


# Create a new version without deleting previous versions.
def create_version(db, file_id, version_number, content_hash, size):
    version = FileVersion(
        file_id=file_id,
        version_number=version_number,
        content_hash=content_hash,
        size=size
    )

    db.add(version)
    return version


# Record a change for future synchronization.
def create_change(
    db, file_id, action, path,
    version_number=None, old_path=None
):
    change = ChangeJournal(
        file_id=file_id,
        action=action,
        path=path,
        version_number=version_number,
        old_path=old_path
    )

    db.add(change)
    return change


# Save all pending database changes together.
def commit(db):
    db.commit()
    