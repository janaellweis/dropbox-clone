import uuid

from fastapi import Form, HTTPException
from pydantic import BaseModel, ValidationError, field_validator

from backend.services.file_rules import is_valid_file_key


class UploadFileRequest(BaseModel):
    key: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not is_valid_file_key(value):
            raise ValueError("Invalid file path")
        return value


def validate_upload_form(
    key: str = Form(...),
    request_id: uuid.UUID = Form(...),
) -> tuple[str, uuid.UUID]:
    try:
        validated = UploadFileRequest(key=key)
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail="Invalid file path",
        ) from exc

    return validated.key, request_id
