import uuid

from pydantic import BaseModel, Field, field_validator

from backend.services.chunks import validate_chunk_hashes


class MissingChunksRequest(BaseModel):
    hashes: list[str] = Field(max_length=1000)

    validate_hashes = field_validator("hashes")(validate_chunk_hashes)


class CreateFromChunksRequest(BaseModel):
    key: str
    request_id: uuid.UUID
    chunk_hashes: list[str]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    validate_hashes = field_validator("chunk_hashes")(validate_chunk_hashes)
