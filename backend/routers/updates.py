from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.services.journal import get_updates

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("")
def get_updates_endpoint(
    since: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    results = get_updates(session, since, limit)
    has_more = len(results) > limit
    events = results[:limit]

    return {
        "updates": [
            {
                "id": event.id,
                "file_id": event.file_id,
                "action": event.action.value,
                "path": event.path,
                "old_path": event.old_path,
                "version_number": event.version_number,
            }
            for event in events
        ],
        "next_checkpoint": events[-1].id if events else since,
        "has_more": has_more,
    }
