from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.dependencies import get_db
from backend.services.journal import get_updates

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("")
def get_updates_endpoint(
    since_0: int = Query(default=0, ge=0),
    since_1: int = Query(default=0, ge=0),
    since_2: int = Query(default=0, ge=0),
    since_3: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_db),
):
    checkpoints = {
        0: since_0,
        1: since_1,
        2: since_2,
        3: since_3,
    }

    results = get_updates(session, checkpoints, limit)
    has_more = len(results) > limit
    events = results[:limit]

    next_checkpoints = checkpoints.copy()

    for event in events:
        next_checkpoints[event.partition_id] = event.partition_position

    return {
        "updates": [
            {
                "id": event.id,
                "file_id": event.file_id,
                "partition_id": event.partition_id,
                "partition_position": event.partition_position,
                "action": event.action.value,
                "path": event.path,
                "old_path": event.old_path,
                "version_number": event.version_number,
            }
            for event in events
        ],
        "next_checkpoints": next_checkpoints,
        "has_more": has_more,
    }
