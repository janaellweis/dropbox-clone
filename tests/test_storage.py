import hashlib
from concurrent.futures import ThreadPoolExecutor

from backend.storage.local import LocalBlobStorage


def test_save_and_read(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Hello Dropbox!"

    content_hash = storage.save(content)

    assert storage.exists(content_hash)
    assert storage.read(content_hash) == content


def test_sha256_hash(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Hello Dropbox!"

    content_hash = storage.save(content)
    expected_hash = hashlib.sha256(content).hexdigest()

    assert content_hash == expected_hash


def test_deduplication(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Hello Dropbox!"

    first_hash = storage.save(content)
    second_hash = storage.save(content)

    assert first_hash == second_hash
    assert len(list(tmp_path.iterdir())) == 1


def test_concurrent_uploads(tmp_path):
    storage = LocalBlobStorage(tmp_path)
    content = b"Concurrent upload test!"

    with ThreadPoolExecutor(max_workers=10) as executor:
        hashes = list(executor.map(storage.save, [content] * 10))

    assert len(set(hashes)) == 1
    assert storage.read(hashes[0]) == content
    assert len(list(tmp_path.iterdir())) == 1
