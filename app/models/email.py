import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Integer, String, Text, Index, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class EmailStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"


class EmailOutbox(Base):
    """A queued email. Persisted so delivery survives restarts and mail is not
    lost when SMTP is briefly unavailable. Never stores secrets or tokens."""

    __tablename__ = "email_outbox"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    recipient: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(255))
    template_name: Mapped[str] = mapped_column(String(100))
    # JSON-serialised context handed to the email template. Never logged.
    template_data: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default=EmailStatus.PENDING, server_default=EmailStatus.PENDING, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, default=3, server_default="3")
    last_error: Mapped[str] = mapped_column(Text, default="", server_default="")
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Optional idempotency guard: only one un-terminated job per key.
    dedupe_key: Mapped[str | None] = mapped_column(String(191), unique=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = (
        Index("ix_email_outbox_claim", "status", "next_retry_at"),
    )
