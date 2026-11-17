import hashlib
from collections.abc import Iterator
from typing import BinaryIO

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.dependencies import get_db, get_storage
from backend.schemas.chunks import MissingChunksRequest
from backend.services.chunks import get_missing_chunks
from backend.services.gc import install_chunk
from backend.storage.local import LocalBlobStorage
from shared.chunking import MAX_CHUNK_SIZE

router = APIRouter(prefix="/chunks", tags=["chunks"])


def _validate_chunk_hash(chunk_hash: str) -> None:
    if len(chunk_hash) != 64 or any(
        character not in "0123456789abcdef" for character in chunk_hash
    ):
        raise HTTPException(status_code=422, detail="Invalid chunk hash")


def _open_chunk(
    chunk_hash: str,
    storage: LocalBlobStorage = Depends(get_storage),
) -> Iterator[BinaryIO]:
    _validate_chunk_hash(chunk_hash)
    try:
        with storage.open_verified_chunk(chunk_hash) as content:
            yield content
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chunk not found") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Chunk storage failure") from exc


@router.get("/{chunk_hash}")
def download_chunk(content: BinaryIO = Depends(_open_chunk)) -> StreamingResponse:
    # The dependency keeps the verified file open until streaming finishes.
    return StreamingResponse(
        iter(lambda: content.read(1024 * 1024), b""),
        media_type="application/octet-stream",
    )


@router.post("/missing")
def find_missing_chunks(
    request: MissingChunksRequest,
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    return {"missing": get_missing_chunks(session, storage, request.hashes)}


@router.put("/{chunk_hash}", status_code=201)
async def upload_chunk(
    chunk_hash: str,
    request: Request,
    session: Session = Depends(get_db),
    storage: LocalBlobStorage = Depends(get_storage),
):
    _validate_chunk_hash(chunk_hash)

    size = 0
    # A fixed buffer avoids a second full chunk copy; ASGI owns incoming pieces.
    with memoryview(bytearray(MAX_CHUNK_SIZE)) as data:
        async for piece in request.stream():
            end = size + len(piece)
            if end > MAX_CHUNK_SIZE:
                raise HTTPException(status_code=413, detail="Chunk too large")
            data[size:end] = piece
            size = end

        if hashlib.sha256(data[:size]).hexdigest() != chunk_hash:
            raise HTTPException(status_code=422, detail="Chunk hash mismatch")

        await run_in_threadpool(install_chunk, session.get_bind(), storage, data[:size])

    return {"hash": chunk_hash}
