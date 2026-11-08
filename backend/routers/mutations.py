import uuid

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.dependencies import get_db, get_storage
from backend.services.mutations import delete_file, rename_file, update_file
from backend.storage.local import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


class RenameFileRequest(BaseModel):
    expected_key: str
    new_key: str
    expected_version: int = Field(ge=1)
    request_id: uuid.UUID


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
