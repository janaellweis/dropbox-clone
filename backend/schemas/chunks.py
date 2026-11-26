from pydantic import BaseModel, Field, field_validator

from backend.core.chunk_validation import validate_chunk_hashes


class MissingChunksRequest(BaseModel):
    hashes: list[str] = Field(max_length=1000)

    validate_hashes = field_validator("hashes")(validate_chunk_hashes)
