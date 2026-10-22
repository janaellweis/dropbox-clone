
# Database operations for the versions module.

from server.models import File, FileVersion


# Find an active file using its UUID.
# Exclude deleted files and folders.
def get_active_file(db, file_id):
    return (
        db.query(File)
        .filter(
            File.id == file_id,
            File.is_deleted.is_(False),
            File.is_folder.is_(False)
        )
        .first()
    )


# Get all versions of a file, starting from version 1.
def get_file_versions(db, file_id):
    return (
        db.query(FileVersion)
        .filter(FileVersion.file_id == file_id)
        .order_by(FileVersion.version_number.asc())
        .all()
    )


# Find a specific version of a file.
def get_file_version(db, file_id, version):
    return (
        db.query(FileVersion)
        .filter(
            FileVersion.file_id == file_id,
            FileVersion.version_number == version
        )
        .first()
    )
