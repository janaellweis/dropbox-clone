
# Business logic for synchronization.

from fastapi import HTTPException

from server.database import SessionLocal
from server.modules.sync import repository


# Return changes that happened after a given journal ID.
def get_updates(since: int = 0):

    # Journal IDs cannot be negative.
    if since < 0:
        raise HTTPException(
            status_code=400,
            detail="since must be 0 or greater"
        )

    with SessionLocal() as db:

        # Get changes after the last journal ID the client received.
        changes = repository.get_changes_since(db, since)

        # Convert database records into JSON responses.
        updates = []

        for change in changes:
            updates.append({
                "id": change.id,
                "file_id": str(change.file_id),
                "action": change.action,
                "path": change.path,
                "old_path": change.old_path,
                "version_number": change.version_number,
            })

        # The client will use this ID in its next request.
        next_since = changes[-1].id if changes else since

        return {
            "updates": updates,
            "next_since": next_since
        }
