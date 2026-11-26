def is_sha256(value: object) -> bool:
    """Check whether a value is a lowercase SHA-256 hexadecimal digest."""
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def is_nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0
