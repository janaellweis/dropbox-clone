def is_sha256(value: object) -> bool:
    """Check whether a value is a lowercase SHA-256 hexadecimal digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
