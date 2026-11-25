import hashlib
from collections.abc import Generator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import httpx
from uuid6 import uuid7

from shared.chunking import Chunk, chunk_file


class DropboxClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        http_client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._owns_http_client = http_client is None
        self.http = (
            http_client
            if http_client is not None
            else httpx.Client(base_url=self.base_url, timeout=30)
        )

    def close(self) -> None:
        if self._owns_http_client:
            self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def push(
        self,
        local_path: str,
        remote_key: str | None = None,
        request_id: str | None = None,
    ) -> dict:
        path = Path(local_path)
        key = remote_key or path.name
        request_id = request_id or str(uuid7())

        manifest = self._prepare_chunks(path)

        response = self.http.post(
            f"{self.base_url}/files/from-chunks",
            json={
                "key": key,
                "request_id": request_id,
                **manifest,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_file_manifest(self, file_id: str, version: int) -> dict:
        response = self.http.get(
            f"{self.base_url}/files/{file_id}/versions/{version}/manifest", timeout=30
        )
        response.raise_for_status()
        return response.json()

    def iter_chunk_bytes(self, chunk_hash: str) -> Generator[bytes, None, None]:
        with self.http.stream(
            "GET", f"{self.base_url}/chunks/{chunk_hash}", timeout=30
        ) as response:
            response.raise_for_status()
            yield from response.iter_bytes(chunk_size=64 * 1024)

    def update_file(
        self,
        file_id: str,
        local_path: str,
        expected_version: int,
        request_id: str | None = None,
    ) -> dict:
        path = Path(local_path)
        request_id = request_id or str(uuid7())

        manifest = self._prepare_chunks(path)

        response = self.http.put(
            f"{self.base_url}/files/{file_id}/from-chunks",
            json={
                "expected_version": expected_version,
                "request_id": request_id,
                **manifest,
            },
            timeout=60,
        )
        response.raise_for_status()
        return response.json()

    def get_updates(self, since: int = 0, limit: int = 100) -> dict:
        response = self.http.get(
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
        request_id = request_id or str(uuid7())

        response = self.http.delete(
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
        request_id = request_id or str(uuid7())

        response = self.http.patch(
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
        response = self.http.get(
            f"{self.base_url}/files/{file_id}",
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def get_missing_chunks(self, chunk_hashes: list[str]) -> list[str]:
        missing = []

        for start in range(0, len(chunk_hashes), 1000):
            batch = chunk_hashes[start : start + 1000]

            response = self.http.post(
                f"{self.base_url}/chunks/missing",
                json={"hashes": batch},
                timeout=30,
            )
            response.raise_for_status()
            missing.extend(response.json()["missing"])

        return missing

    def upload_chunk(self, chunk: Chunk) -> None:
        response = self.http.put(
            f"{self.base_url}/chunks/{chunk.hash}",
            content=chunk.data,
            timeout=60,
        )
        response.raise_for_status()

    def _prepare_chunks(self, path: Path) -> dict:
        """Prepare the file manifest and upload missing chunks concurrently."""
        chunk_hashes = []
        content_hash = hashlib.sha256()
        size = 0

        for chunk in chunk_file(path):
            chunk_hashes.append(chunk.hash)
            content_hash.update(chunk.data)
            size += chunk.size

        manifest = {
            "chunk_hashes": chunk_hashes,
            "content_hash": content_hash.hexdigest(),
            "size": size,
        }

        missing = set(self.get_missing_chunks(list(set(chunk_hashes))))

        if not missing:
            return manifest

        max_workers = 4
        pending = set()
        second_pass_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for chunk in chunk_file(path):
                if (
                    second_pass_count >= len(chunk_hashes)
                    or chunk.hash != chunk_hashes[second_pass_count]
                ):
                    raise OSError("File changed during chunking")

                second_pass_count += 1

                if chunk.hash not in missing:
                    continue

                pending.add(pool.submit(self.upload_chunk, chunk))
                missing.remove(chunk.hash)

                if len(pending) >= max_workers:
                    done, pending = wait(
                        pending,
                        return_when=FIRST_COMPLETED,
                    )

                    for future in done:
                        future.result()

            for future in pending:
                future.result()

        if second_pass_count != len(chunk_hashes):
            raise OSError("File changed during chunking")

        return manifest
