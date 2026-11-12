import asyncio
from pathlib import Path

import pytest

from app.services.media import save_upload

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 16


class FakeUpload:
    def __init__(self, filename: str, content: bytes, size: int | None = None):
        self.filename = filename
        self._content = content
        self.size = len(content) if size is None else size

    async def read(self, n: int = -1) -> bytes:
        return self._content if n < 0 else self._content[:n]


def run_upload(tmp_path: Path, upload: FakeUpload, max_bytes: int = 1024) -> str:
    return asyncio.run(save_upload(upload, tmp_path, max_bytes))


def test_accepts_real_png(tmp_path):
    path = run_upload(tmp_path, FakeUpload("poster.png", PNG))

    assert path.startswith("uploads/") and path.endswith(".png")
    assert (tmp_path / path.removeprefix("uploads/")).read_bytes() == PNG


def test_accepts_jpeg_and_webp(tmp_path):
    assert run_upload(tmp_path, FakeUpload("a.jpg", JPEG)).endswith(".jpg")
    assert run_upload(tmp_path, FakeUpload("b.webp", WEBP)).endswith(".webp")


@pytest.mark.parametrize("name", ["page.html", "logo.svg", "run.js", "noext"])
def test_rejects_non_image_extensions(tmp_path, name):
    with pytest.raises(ValueError, match="Only JPG"):
        run_upload(tmp_path, FakeUpload(name, b"<script>alert(1)</script>"))


def test_rejects_html_renamed_to_png(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        run_upload(tmp_path, FakeUpload("evil.png", b"<html><script>alert(1)</script>"))


def test_rejects_webp_without_webp_marker(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        run_upload(tmp_path, FakeUpload("x.webp", b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 16))


def test_rejects_oversized_file_even_without_reported_size(tmp_path):
    big = PNG + b"\x00" * 2000
    with pytest.raises(ValueError, match="too large"):
        run_upload(tmp_path, FakeUpload("big.png", big, size=None), max_bytes=1024)


def test_nothing_written_on_rejection(tmp_path):
    with pytest.raises(ValueError):
        run_upload(tmp_path, FakeUpload("evil.png", b"<html>"))
    assert list(tmp_path.iterdir()) == []
