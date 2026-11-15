import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from backend.core.exceptions import FileContentMissingError
from backend.dependencies import get_db, get_storage
from backend.schemas.mutations import (
    RenameFileRequest,
    UpdateFromChunksRequest,
)
from backend.services.mutations import delete_file, rename_file, update_file
from backend.storage.local import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


@router.put("/{file_id}")
def update_file_endpoint(
    file_id: uuid.UUID,
    expected_version: int = Form(..., ge=1),
    request_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):

    version = update_file(
        session=session,
        storage=storage,
        file_id=file_id,
        content=file.file,
        expected_version=expected_version,
        request_id=request_id,
    )
    return {
        "file_id": file_id,
        "version": version.version_number,
        "content_hash": version.content_hash,
        "size": version.size,
    }


@router.put("/{file_id}/from-chunks")
def update_file_from_chunks(
    file_id: uuid.UUID,
    request: UpdateFromChunksRequest,
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    try:
        version = update_file(
            session=session,
            storage=storage,
            file_id=file_id,
            content=None,
            expected_version=request.expected_version,
            request_id=request.request_id,
            chunk_hashes=request.chunk_hashes,
            expected_hash=request.content_hash,
            expected_size=request.size,
        )
    except (FileContentMissingError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "file_id": file_id,
        "version": version.version_number,
        "content_hash": version.content_hash,
        "size": version.size,
    }


@router.delete("/{file_id}")
def delete_file_endpoint(
    file_id: uuid.UUID,
    request_id: uuid.UUID,
    expected_version: int = Query(ge=1),
    session: Session = Depends(get_db),
):
    delete_file(
        session=session,
        file_id=file_id,
        expected_version=expected_version,
        request_id=request_id,
    )

    return {"file_id": file_id, "deleted": True}


@router.patch("/{file_id}/rename")
def rename_file_endpoint(
    file_id: uuid.UUID,
    request: RenameFileRequest,
    session: Session = Depends(get_db),
):
    result_key = rename_file(
        session=session,
        file_id=file_id,
        expected_key=request.expected_key,
        new_key=request.new_key,
        expected_version=request.expected_version,
        request_id=request.request_id,
    )
    return {
        "file_id": file_id,
        "key": result_key,
    }
