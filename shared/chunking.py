import hashlib
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from fastcdc import fastcdc

MIN_CHUNK_SIZE = 256 * 1024
AVG_CHUNK_SIZE = 1024 * 1024
MAX_CHUNK_SIZE = 4 * 1024 * 1024


@dataclass(frozen=True)
class Chunk:
    data: bytes
    hash: str
    size: int


def chunk_file(path: Path) -> Iterator[Chunk]:
    """Yield unchanged FastCDC boundaries with bounded lookahead and byte buffers.

    Transient copies also consume memory; the lookahead limit is not a limit on
    total process memory. This iterator retains no whole-file manifest.
    """
    with path.open("rb") as file:
        lookahead = bytearray()
        eof = False
        while True:
            # A short read is not EOF. Fill the window before choosing a cut;
            # otherwise the end of an arbitrary read could become a boundary.
            while not eof and len(lookahead) < MAX_CHUNK_SIZE:
                block = file.read(MAX_CHUNK_SIZE - len(lookahead))
                if not block:
                    eof = True
                    break
                lookahead.extend(block)
                del block

            if not lookahead:
                return

            # FastCDC 1.7.0 examines at most max_size bytes for its first cut.
            # Consume only that cut, then refill the unconsumed suffix. Closing
            # releases the library's memoryview before resizing the bytearray.
            with closing(
                fastcdc(
                    lookahead,
                    min_size=MIN_CHUNK_SIZE,
                    avg_size=AVG_CHUNK_SIZE,
                    max_size=MAX_CHUNK_SIZE,
                )
            ) as boundaries:
                size = next(boundaries).length
            data = bytes(lookahead[:size])
            del lookahead[:size]

            yield Chunk(
                data=data,
                hash=hashlib.sha256(data).hexdigest(),
                size=size,
            )
