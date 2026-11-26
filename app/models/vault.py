"""Secure vault links: watch/download links attached to a title.

Each link is one row on a movie or series. They go through moderation like
contributions: a member's links are pending until an admin approves them, while
auto-approve users and staff publish instantly. Approval credits points exactly
once via the points ledger's idempotency key.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VaultStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class VaultKind(StrEnum):
    IMDB = "imdb"
    FILE = "file"
    STREAM = "stream"


class VaultLink(Base):
    __tablename__ = "vault_links"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Exactly one of movie_id / series_id is set.
    movie_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("movies.id", ondelete="CASCADE"), index=True, default=None
    )
    series_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True, default=None
    )
    url: Mapped[str] = mapped_column(String(500))
    label: Mapped[str] = mapped_column(String(100), default="", server_default="")
    kind: Mapped[str] = mapped_column(String(16), default=VaultKind.FILE, server_default=VaultKind.FILE)
    added_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(
        String(16), default=VaultStatus.PENDING, server_default=VaultStatus.PENDING, index=True
    )
    points_reward: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    review_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
