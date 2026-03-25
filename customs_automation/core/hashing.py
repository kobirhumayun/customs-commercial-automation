from __future__ import annotations

import hashlib
from pathlib import Path


SHA256_ALGORITHM = "sha256"


def sha256_hex_from_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex_from_text(text: str) -> str:
    return sha256_hex_from_bytes(text.encode("utf-8"))


def sha256_hex_from_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_handle:
        for chunk in iter(lambda: file_handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
