import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.dependencies import get_db, get_storage
from backend.services.file_rules import is_valid_file_key
from backend.services.files import (
    FileContentMissingError,
    create_file,
    get_file_content,
    get_file_metadata,
    get_file_version_content,
    list_file_versions,
)
from backend.storage.local import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


@router.post("")
def upload_file(
    key: str = Form(...),
    request_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    if not is_valid_file_key(key):
        raise HTTPException(status_code=400, detail="Invalid file path")

    content = file.file.read()

    try:
        saved_file = create_file(
            session=session,
            storage=storage,
            key=key,
            content=content,
            request_id=request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="File path already exists",
        ) from exc

    return {
        "file_id": saved_file.id,
        "key": saved_file.key,
    }


@router.get("/{file_id}/download")
def download_file(
    file_id: uuid.UUID,
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    try:
        content, key = get_file_content(
            session=session,
            storage=storage,
            file_id=file_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileContentMissingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{Path(key).name}"'},
    )


@router.get("/{file_id}")
def get_file_metadata_endpoint(
    file_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    try:
        saved_file, latest = get_file_metadata(session, file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "file_id": saved_file.id,
        "key": saved_file.key,
        "version": latest.version_number,
        "content_hash": latest.content_hash,
        "size": latest.size,
        "created_at": saved_file.created_at,
        "updated_at": saved_file.updated_at,
    }


@router.get("/{file_id}/versions")
def list_file_versions_endpoint(
    file_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    try:
        versions = list_file_versions(session, file_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "file_id": file_id,
        "versions": [
            {
                "version": version.version_number,
                "content_hash": version.content_hash,
                "size": version.size,
            }
            for version in versions
        ],
    }


@router.get("/{file_id}/versions/{version_number}")
def download_file_version_endpoint(
    file_id: uuid.UUID,
    version_number: int,
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    try:
        content, key = get_file_version_content(
            session=session,
            storage=storage,
            file_id=file_id,
            version_number=version_number,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileContentMissingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{Path(key).name}"'},
    )
