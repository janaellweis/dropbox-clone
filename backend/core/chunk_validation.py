import re


def validate_chunk_hashes(chunk_hashes: list[str]) -> list[str]:
    """Check the entire list before any hash can become a storage path."""
    if any(
        not isinstance(chunk_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", chunk_hash) is None
        for chunk_hash in chunk_hashes
    ):
        raise ValueError("Invalid SHA-256 chunk hash")
    return chunk_hashes
