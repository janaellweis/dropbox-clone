
# Database operations for the synchronization module.

from server.models import ChangeJournal


# Get changes that happened after the given journal ID.
def get_changes_since(db, since: int):

    return (
        db.query(ChangeJournal)
        .filter(ChangeJournal.id > since)
        .order_by(ChangeJournal.id.asc())
        .limit(100)
        .all()
    )
