from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from project.utils.json import canonical_json_dumps


HASH_ALGORITHM = "sha256"
HEX_DIGEST_LENGTH = 64


def sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_hex_text(text: str) -> str:
    return sha256_hex_bytes(text.encode("utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_hash(payload: Any) -> str:
    return sha256_hex_text(canonical_json_dumps(payload))
