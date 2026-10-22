
# API endpoints for folder operations.
# The controller receives HTTP requests and calls the service.

from fastapi import APIRouter, Form

from server.modules.folders import service

router = APIRouter()


# This endpoint creates an empty folder.
@router.post("/folders")
def create_folder(key: str = Form(...)):

    # Let the service handle validation and folder creation.
    return service.create_folder(key)
