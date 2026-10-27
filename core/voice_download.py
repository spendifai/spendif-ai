"""Download of the listening model (spec 32, 3): one file, checked.

The model is downloaded on first use, after the user's yes, and only a file
with the expected SHA-256 is kept: a file that differs is deleted, never
loaded. An interrupted download goes on from the bytes already there.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

_CHUNK = 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download_verified(url: str, dest: Path, sha256: str, size: int,
                      progress: Callable[[float], None] | None = None,
                      opener: Callable | None = None) -> None:
    """Download *url* to *dest*, resuming a partial download, and keep it
    only when its SHA-256 is *sha256*.

    Args:
        url: where the file is published.
        dest: the final path; the bytes collect in ``<dest>.part`` until
            they are checked.
        sha256: the expected SHA-256, lowercase hex.
        size: the expected size in bytes, for the progress.
        progress: called with the fraction done, 0.0 to 1.0; None for no
            progress.
        opener: a ``requests.get``-like callable; None uses requests.

    Raises:
        ValueError: the file downloaded is not the expected one (deleted).
    """
    if opener is None:
        import requests
        opener = requests.get
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    response = opener(url, headers=headers, stream=True, timeout=30)
    response.raise_for_status()
    if have and response.status_code != 206:      # the server ignored the range: start over
        have = 0
    with open(part, "ab" if have else "wb") as f:
        for chunk in response.iter_content(chunk_size=_CHUNK):
            f.write(chunk)
            have += len(chunk)
            if progress and size:
                progress(min(have / size, 0.99))
    if _sha256(part) != sha256:
        part.unlink()
        raise ValueError(f"{dest.name}: the file downloaded is not the expected one")
    part.replace(dest)
    if progress:
        progress(1.0)
