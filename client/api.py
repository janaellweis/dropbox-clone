import uuid
from pathlib import Path

import httpx


class DropboxClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def push(self, local_path: str, remote_key: str | None = None) -> dict:
        path = Path(local_path)
        key = remote_key or path.name
        request_id = str(uuid.uuid4())

        with path.open("rb") as file:
            response = httpx.post(
                f"{self.base_url}/files",
                data={"key": key, "request_id": request_id},
                files={"file": (path.name, file)},
                timeout=30,
            )

        response.raise_for_status()
        return response.json()

    def pull(self, file_id: str, destination: str) -> None:
        response = httpx.get(
            f"{self.base_url}/files/{file_id}/download",
            timeout=30,
        )
        response.raise_for_status()

        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)
