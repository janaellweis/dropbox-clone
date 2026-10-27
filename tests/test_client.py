import httpx

from client.api import DropboxClient


def test_client_push(tmp_path, monkeypatch):
    path = tmp_path / "notes.txt"
    path.write_bytes(b"Hello client!")

    def mock_post(url, **kwargs):
        assert url == "http://127.0.0.1:8000/files"
        assert kwargs["data"]["key"] == "notes.txt"
        assert kwargs["files"]["file"][1].read() == b"Hello client!"

        return httpx.Response(
            200,
            json={"file_id": "test-id", "key": "notes.txt"},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", mock_post)

    client = DropboxClient()
    result = client.push(str(path))

    assert result["file_id"] == "test-id"


def test_client_pull(tmp_path, monkeypatch):
    content = b"Downloaded content!"

    def mock_get(url, **kwargs):
        assert url == "http://127.0.0.1:8000/files/test-id/download"

        return httpx.Response(
            200,
            content=content,
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx, "get", mock_get)

    destination = tmp_path / "downloads" / "notes.txt"

    client = DropboxClient()
    client.pull("test-id", str(destination))

    assert destination.read_bytes() == content
