"""Canonical hashing for manifest and smoke fingerprints.

Git checkouts may convert text files to CRLF while the working copy is LF (or the
reverse). Fingerprints are therefore computed over LF-normalized bytes so the
same commit validates on either checkout.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def canonical_sha256(path: Path | str) -> str:
    data = Path(path).read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()
