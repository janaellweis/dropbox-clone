import uuid
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.db.database import SessionLocal
from backend.db.models import File as FileModel
from backend.db.models import FileVersion
from backend.services.files import create_file
from backend.storage.local import LocalBlobStorage

app = FastAPI(title="Dropbox Clone")

storage = LocalBlobStorage(Path("data/blobs"))


def get_db():
    with SessionLocal() as session:
        yield session


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/files")
def upload_file(
    key: str = Form(...),
    request_id: uuid.UUID = Form(...),
    file: UploadFile = File(...),
    session: Session = Depends(get_db),
):
    if not key or key.startswith("/") or ".." in Path(key).parts:
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

        return {
            "file_id": saved_file.id,
            "key": saved_file.key,
        }

    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail="File path already exists",
        ) from exc


@app.get("/files/{file_id}/download")
def download_file(
    file_id: uuid.UUID,
    session: Session = Depends(get_db),
):
    saved_file = session.get(FileModel, file_id)

    if saved_file is None or saved_file.is_deleted:
        raise HTTPException(status_code=404, detail="File not found")

    version = session.scalar(
        select(FileVersion)
        .where(FileVersion.file_id == file_id)
        .order_by(FileVersion.version_number.desc())
        .limit(1)
    )

    if version is None:
        raise HTTPException(status_code=404, detail="File version not found")

    try:
        content = storage.read(version.content_hash)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500,
            detail="File content missing from storage",
        ) from exc

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{Path(saved_file.key).name}"'
        },
    )
