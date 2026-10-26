"""The listening model comes down on first use, only after a yes (spec 32,
3): checked against its SHA-256, resumed where it stopped, never used when
it differs; the page offers it only in Italian and where the library is."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from core.voice_download import download_verified
from services.voice_service import VoiceService

_PAYLOAD = b"whisper weights " * 1000
_SHA = hashlib.sha256(_PAYLOAD).hexdigest()


class _Response(io.BytesIO):
    """A streamed HTTP answer: the bytes from *start* on, and the status the
    server would give to a request with or without a Range header.

    Args:
        body: the whole file.
        start: the first byte sent (0 without a Range header).
    """

    def __init__(self, body: bytes, start: int) -> None:
        super().__init__(body[start:])
        self.status_code = 206 if start else 200

    def iter_content(self, chunk_size: int):
        while chunk := self.read(chunk_size):
            yield chunk

    def raise_for_status(self) -> None:
        return None


def _opener(body: bytes, seen: list[int]):
    """A fake GET that serves *body* honouring the Range header.

    Args:
        body: the file the server holds.
        seen: filled with the first byte asked by each request.
    """
    def get(url: str, headers: dict, stream: bool, timeout: int):
        start = int(headers.get("Range", "bytes=0-")[6:].rstrip("-"))
        seen.append(start)
        return _Response(body, start)
    return get


def test_the_model_arrives_whole_and_checked(tmp_path):
    dest = tmp_path / "ggml-small-q5_1.bin"
    steps: list[float] = []
    seen: list[int] = []
    download_verified("https://x/model", dest, _SHA, len(_PAYLOAD), steps.append, _opener(_PAYLOAD, seen))
    assert dest.read_bytes() == _PAYLOAD
    assert steps[-1] == 1.0 and seen == [0]
    assert not dest.with_suffix(".bin.part").exists()


def test_an_interrupted_download_resumes(tmp_path):
    dest = tmp_path / "ggml-small-q5_1.bin"
    dest.with_suffix(".bin.part").write_bytes(_PAYLOAD[:4000])
    seen: list[int] = []
    download_verified("https://x/model", dest, _SHA, len(_PAYLOAD), None, _opener(_PAYLOAD, seen))
    assert seen == [4000] and dest.read_bytes() == _PAYLOAD


def test_a_different_file_is_thrown_away(tmp_path):
    dest = tmp_path / "ggml-small-q5_1.bin"
    with pytest.raises(ValueError):
        download_verified("https://x/model", dest, _SHA, len(_PAYLOAD), None,
                          _opener(b"something else " * 1000, []))
    assert not dest.exists() and not dest.with_suffix(".bin.part").exists()


def _ready_library(monkeypatch, present: bool) -> None:
    """Pretend the whisper.cpp library is, or is not, in the package.

    Args:
        monkeypatch: pytest's monkeypatch fixture.
        present: True when the library can be imported.
    """
    monkeypatch.setattr("services.voice_service._importable", lambda name: present)


@pytest.mark.parametrize("lang, library, offered", [
    ("it", True, True), ("en", True, False), ("it", False, False),
])
def test_the_question_only_in_italian_and_with_the_library(tmp_path, monkeypatch, lang, library, offered):
    _ready_library(monkeypatch, library)
    assert VoiceService(root=tmp_path).can_offer_listening(lang) is offered


def test_no_question_once_the_model_is_there(tmp_path, monkeypatch):
    _ready_library(monkeypatch, True)
    svc = VoiceService(root=tmp_path)
    (tmp_path / "ggml-small-q5_1.bin").write_bytes(b"x")
    assert svc.can_listen() and not svc.can_offer_listening("it")


def test_the_download_writes_where_the_service_listens(tmp_path, monkeypatch):
    _ready_library(monkeypatch, True)
    calls: list[Path] = []
    monkeypatch.setattr("services.voice_service.download_verified",
                        lambda url, dest, sha, size, progress, opener=None: calls.append(dest))
    VoiceService(root=tmp_path).download_listening_model()
    assert calls == [tmp_path / "ggml-small-q5_1.bin"]
