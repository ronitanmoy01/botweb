import re
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


def slugify(text: str) -> str:
    """Convert text to URL-safe slug: lowercase, alphanumeric + hyphens only."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


async def unique_slug(session: AsyncSession, model, base_slug: str, exclude_id=None) -> str:
    """Generate a unique slug by appending a counter if needed."""
    slug = base_slug
    counter = 1
    while True:
        stmt = select(model.id).where(model.slug == slug)
        if exclude_id is not None:
            stmt = stmt.where(model.id != exclude_id)
        existing = await session.scalar(stmt)
        if existing is None:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


_IMAGE_SIGNATURES = {
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".webp": (b"RIFF",),  # RIFF container; "WEBP" at offset 8 is checked in _is_image
}


def _is_image(ext: str, head: bytes) -> bool:
    if not any(head.startswith(sig) for sig in _IMAGE_SIGNATURES[ext]):
        return False
    return ext != ".webp" or head[8:12] == b"WEBP"


async def save_upload(file, uploads_dir: Path, max_bytes: int) -> str:
    """
    Save an uploaded image to uploads_dir/<random-name>.ext.
    Returns the relative path "uploads/<name>.ext".
    Raises ValueError if the file is too large, has no allowed image extension,
    or its content is not that image type. Files are served from /uploads, so
    HTML/SVG/script content must never be stored.
    """
    if file.size and file.size > max_bytes:
        raise ValueError(f"File too large (max {max_bytes // 1024 // 1024} MB)")
    ext = Path(file.filename).suffix.lower() if file.filename else ""
    if ext not in _IMAGE_SIGNATURES:
        raise ValueError("Only JPG, PNG, GIF or WEBP images are allowed")
    # Read one byte past the limit so an oversized body is caught even when file.size is missing.
    content = await file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError(f"File too large (max {max_bytes // 1024 // 1024} MB)")
    if not _is_image(ext, content[:16]):
        raise ValueError("File content does not match its image type")
    uploads_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{secrets.token_urlsafe(16)}{ext}"
    (uploads_dir / filename).write_bytes(content)
    return f"uploads/{filename}"
