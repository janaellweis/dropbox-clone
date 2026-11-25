from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from backend.core.chunk_validation import validate_chunk_hashes
from backend.dependencies import get_db, get_storage
from backend.schemas.chunks import MissingChunksRequest
from backend.services.chunks import (
    get_missing_chunks,
    store_verified_chunk,
    stream_chunk,
)
from backend.storage.local import LocalBlobStorage
from shared.chunking import MAX_CHUNK_SIZE

router = APIRouter(prefix="/chunks", tags=["chunks"])


def _chunk_stream(
    chunk_hash: str,
    storage: LocalBlobStorage = Depends(get_storage),
) -> Iterator[Iterator[bytes]]:
    try:
        with stream_chunk(storage, chunk_hash) as content:
            yield content
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid chunk hash") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Chunk not found") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Chunk storage failure") from exc


@router.get("/{chunk_hash}")
def download_chunk(
    content: Iterator[bytes] = Depends(_chunk_stream),
) -> StreamingResponse:
    # The dependency keeps the verified file open until streaming finishes.
    return StreamingResponse(
        content,
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
    try:
        validate_chunk_hashes([chunk_hash])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid chunk hash") from exc

    size = 0
    # A fixed buffer avoids a second full chunk copy; ASGI owns incoming pieces.
    with memoryview(bytearray(MAX_CHUNK_SIZE)) as data:
        async for piece in request.stream():
            end = size + len(piece)
            if end > MAX_CHUNK_SIZE:
                raise HTTPException(status_code=413, detail="Chunk too large")
            data[size:end] = piece
            size = end

        try:
            await run_in_threadpool(
                store_verified_chunk,
                session,
                storage,
                chunk_hash,
                data[:size],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"hash": chunk_hash}
