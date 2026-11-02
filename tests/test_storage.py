import hashlib
import io
from concurrent.futures import ThreadPoolExecutor

from backend.storage.local import LocalBlobStorage


def test_save_and_read(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Hello Dropbox!"
    prefix = b"CREATE\0test.txt\0"

    content_hash, size, fingerprint = storage.save(
        io.BytesIO(content),
        prefix,
    )

    assert content_hash == hashlib.sha256(content).hexdigest()
    assert size == len(content)
    assert fingerprint == hashlib.sha256(prefix + content).hexdigest()
    assert storage.exists(content_hash)
    assert storage.read(content_hash) == content


def test_concurrent_uploads(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Concurrent upload test!"
    prefix = b"CREATE\0concurrent.txt\0"

    def upload(_):
        return storage.save(io.BytesIO(content), prefix)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(upload, range(10)))

    hashes = [result[0] for result in results]

    assert len(set(hashes)) == 1
    assert all(result[1] == len(content) for result in results)
    assert all(
        result[2] == hashlib.sha256(prefix + content).hexdigest()
        for result in results
    )
    assert storage.read(hashes[0]) == content
    assert len(list(tmp_path.iterdir())) == 1


def test_stream_reads_file_in_chunks(tmp_path):
    import io

    storage = LocalBlobStorage(tmp_path)
    content = b"A" * (2 * 1024 * 1024 + 100)

    content_hash, _, _ = storage.save(
        io.BytesIO(content),
        b"CREATE\0large.txt\0",
    )

    chunks = list(storage.stream(content_hash))

    assert len(chunks) == 3
    assert len(chunks[0]) == 1024 * 1024
    assert len(chunks[1]) == 1024 * 1024
    assert len(chunks[2]) == 100
    assert b"".join(chunks) == content
