from __future__ import annotations

import re


SEMVER_PATTERN = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def is_semver(value: str) -> bool:
    return bool(SEMVER_PATTERN.fullmatch(value))


def is_sha256_hex(value: str) -> bool:
    return bool(SHA256_PATTERN.fullmatch(value))
