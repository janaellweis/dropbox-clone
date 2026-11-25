import uuid

from pydantic import BaseModel, Field, field_validator

from backend.core.chunk_validation import validate_chunk_hashes


class RenameFileRequest(BaseModel):
    expected_key: str
    new_key: str
    expected_version: int = Field(ge=1)
    request_id: uuid.UUID


class UpdateFromChunksRequest(BaseModel):
    expected_version: int = Field(ge=1)
    request_id: uuid.UUID
    chunk_hashes: list[str]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    validate_hashes = field_validator("chunk_hashes")(validate_chunk_hashes)


class CreateFromChunksRequest(BaseModel):
    key: str
    request_id: uuid.UUID
    chunk_hashes: list[str]
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)

    validate_hashes = field_validator("chunk_hashes")(validate_chunk_hashes)


class RestoreVersionRequest(BaseModel):
    expected_version: int = Field(ge=1)
    request_id: uuid.UUID
