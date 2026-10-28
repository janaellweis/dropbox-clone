import uuid
from pathlib import Path

import httpx


class DropboxClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def push(
        self,
        local_path: str,
        remote_key: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        path = Path(local_path)
        key = remote_key or path.name
        request_id = request_id or str(uuid.uuid4())

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

    def update_file(
        self,
        file_id: str,
        local_path: str,
        expected_version: int,
        request_id: str | None = None,
    ) -> dict:
        path = Path(local_path)
        request_id = request_id or str(uuid.uuid4())

        with path.open("rb") as file:
            response = httpx.put(
                f"{self.base_url}/files/{file_id}",
                data={
                    "expected_version": expected_version,
                    "request_id": request_id,
                },
                files={"file": (path.name, file)},
                timeout=60,
            )

        response.raise_for_status()
        return response.json()

    def get_updates(self, since: int = 0, limit: int = 100) -> dict:
        response = httpx.get(
            f"{self.base_url}/updates",
            params={"since": since, "limit": limit},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    
    def delete_file(
        self,
        file_id: str,
        expected_version: int,
        request_id: str | None = None,
    ) -> dict:
        request_id = request_id or str(uuid.uuid4())

        response = httpx.delete(
            f"{self.base_url}/files/{file_id}",
            params={
                "expected_version": expected_version,
                "request_id": request_id,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def rename_file(
        self,
        file_id: str,
        expected_key: str,
        new_key: str,
        expected_version: int,
        request_id: str | None = None,
    ) -> dict:
        request_id = request_id or str(uuid.uuid4())

        response = httpx.patch(
            f"{self.base_url}/files/{file_id}/rename",
            json={
                "expected_key": expected_key,
                "new_key": new_key,
                "expected_version": expected_version,
                "request_id": request_id,
            },
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_file_metadata(self, file_id: str) -> dict:
        response = httpx.get(
            f"{self.base_url}/files/{file_id}",
            timeout=30,
        )
        response.raise_for_status()
        return response.json()