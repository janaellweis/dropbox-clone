import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.dependencies import get_db, get_storage
from backend.services.file_rules import FileVersionConflictError
from backend.services.mutations import delete_file, rename_file, update_file
from backend.storage.local import LocalBlobStorage

router = APIRouter(prefix="/files", tags=["files"])


class RenameFileRequest(BaseModel):
    expected_key: str
    new_key: str
    expected_version: int
    request_id: uuid.UUID


@router.put("/{file_id}")
def update_file_endpoint(
    file_id: uuid.UUID,
    expected_version: int = Form(...),
    request_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    content = file.file.read()

    try:
        version = update_file(
            session=session,
            storage=storage,
            file_id=file_id,
            content=content,
            expected_version=expected_version,
            request_id=request_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Update conflict",
        ) from exc

    return {
        "file_id": file_id,
        "version": version.version_number,
        "content_hash": version.content_hash,
        "size": version.size,
    }


@router.delete("/{file_id}")
def delete_file_endpoint(
    file_id: uuid.UUID,
    expected_version: int,
    request_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    try:
        delete_file(
            session=session,
            file_id=file_id,
            expected_version=expected_version,
            request_id=request_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"file_id": file_id, "deleted": True}


@router.patch("/{file_id}/rename")
def rename_file_endpoint(
    file_id: uuid.UUID,
    request: RenameFileRequest,
    session: Session = Depends(get_db),
):
    try:
        result_key = rename_file(
            session=session,
            file_id=file_id,
            expected_key=request.expected_key,
            new_key=request.new_key,
            expected_version=request.expected_version,
            request_id=request.request_id,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileVersionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="Destination path already exists",
        ) from exc
    except ValueError as exc:
        status_code = 400 if str(exc) == "Invalid destination path" else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    return {
        "file_id": file_id,
        "key": result_key,
    }
