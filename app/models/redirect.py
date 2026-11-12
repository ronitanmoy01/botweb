import uuid

from sqlalchemy import Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SlugRedirect(Base):
    """An old slug that used to belong to a movie or series. Public routes 301 it to the current slug."""

    __tablename__ = "slug_redirects"
    __table_args__ = (Index("ix_slug_redirects_kind_old", "kind", "old_slug", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))  # "movie" or "series"
    old_slug: Mapped[str] = mapped_column(String(255))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # no foreign key: stale rows just resolve to nothing
