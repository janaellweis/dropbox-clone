import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.dependencies import get_db, get_storage
from backend.schemas.files import validate_upload_form
from backend.services.files import (
    create_file,
    get_file_content,
    get_file_metadata,
    get_file_version_content,
    list_file_versions,
)
from backend.storage.local import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


def file_download_response(
    storage: LocalBlobStorage,
    content_hash: str,
    key: str,
) -> StreamingResponse:
    filename = Path(key).name
    disposition = f"attachment; filename*=utf-8''{quote(filename, safe='')}"

    return StreamingResponse(
        storage.stream(content_hash),
        media_type="application/octet-stream",
        headers={"Content-Disposition": disposition},
    )


@router.post("")
def upload_file(
    upload_data: tuple[str, uuid.UUID] = Depends(validate_upload_form),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    key, request_id = upload_data

    saved_file = create_file(
        session=session,
        storage=storage,
        key=key,
        content=file.file,
        request_id=request_id,
    )

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
    content_hash, key = get_file_content(
        session=session,
        storage=storage,
        file_id=file_id,
    )

    return file_download_response(storage, content_hash, key)


@router.get("/{file_id}")
def get_file_metadata_endpoint(
    file_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    saved_file, latest = get_file_metadata(session, file_id)

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
    versions = list_file_versions(session, file_id)

    return {
        "file_id": file_id,
        "versions": [
            {
                "version": version.version_number,
                "content_hash": version.content_hash,
                "size": version.size,
                "created_at": version.created_at.isoformat(),
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
    content_hash, key = get_file_version_content(
        session=session,
        storage=storage,
        file_id=file_id,
        version_number=version_number,
    )

    return file_download_response(storage, content_hash, key)
