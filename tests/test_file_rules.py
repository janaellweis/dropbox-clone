import pytest

from backend.services.file_rules import is_valid_file_key


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("notes.txt", True),
        ("documents/notes.txt", True),
        ("a/b/c.txt", True),
        ("../secret.txt", False),
        ("folder/../secret.txt", False),
        ("/absolute.txt", False),
        ("folder//file.txt", False),
        ("folder/./file.txt", False),
        ("folder/", False),
        ("", False),
        ("folder\\file.txt", False),
        ("file\nname.txt", False),
        ("a" * 1025, False),
    ],
)
def test_is_valid_file_key(key, expected):
    assert is_valid_file_key(key) is expected
