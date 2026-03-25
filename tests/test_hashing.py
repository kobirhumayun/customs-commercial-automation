from pathlib import Path

from customs_automation.core.hashing import (
    SHA256_ALGORITHM,
    sha256_hex_from_bytes,
    sha256_hex_from_file,
    sha256_hex_from_text,
)


def test_sha256_constant() -> None:
    assert SHA256_ALGORITHM == "sha256"


def test_sha256_helpers_return_consistent_values(tmp_path: Path) -> None:
    content = "deterministic-content"
    file_path = tmp_path / "payload.txt"
    file_path.write_text(content, encoding="utf-8")

    text_hash = sha256_hex_from_text(content)
    file_hash = sha256_hex_from_file(file_path)
    bytes_hash = sha256_hex_from_bytes(content.encode("utf-8"))

    assert text_hash == file_hash == bytes_hash
    assert len(text_hash) == 64
