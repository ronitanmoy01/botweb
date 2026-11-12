import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class View(Base):
    """One row per recorded content view. Visitor identity is a daily-rotated hash, never a raw IP."""

    __tablename__ = "views"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # shortcut: daily-rotated hash, not a persistent identifier; rotate key or drop rows for GDPR erasure
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)
    movie_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), default=None, index=True)
    series_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), default=None, index=True)
    episode_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("episodes.id", ondelete="CASCADE"), default=None, index=True)
    user_agent: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
