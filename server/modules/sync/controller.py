
# API endpoints for synchronization.
# The controller receives HTTP requests and calls the service.

from fastapi import APIRouter

from server.modules.sync import service

router = APIRouter()


# Return changes that happened after a given journal ID.
@router.get("/updates")
def get_updates(since: int = 0):

    # Let the service handle validation and retrieving changes.
    return service.get_updates(since)
