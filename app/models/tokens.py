import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class _TokenMixin:
    """Shared shape for single-use, hashed, expiring link tokens.

    Only a SHA-256 hash of the token is stored, so a database leak never leaks
    a usable link. ``used_at`` enforces single-use; ``expires_at`` enforces the
    lifetime window.
    """

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class PasswordResetToken(_TokenMixin, Base):
    """A link that lets its owner choose a new password. Single-use, ~30 minutes."""

    __tablename__ = "password_reset_tokens"


class EmailVerificationToken(_TokenMixin, Base):
    """A link that confirms a user owns their email address. Single-use, ~24 hours."""

    __tablename__ = "email_verification_tokens"
