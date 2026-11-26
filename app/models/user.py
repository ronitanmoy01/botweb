import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    EDITOR = "editor"
    AUTHOR = "author"
    PREMIUM_MEMBER = "premium_member"
    USER = "user"


ADMIN_ROLES = frozenset({Role.SUPER_ADMIN, Role.ADMIN})


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100), default="")
    role: Mapped[str] = mapped_column(String(32), default=Role.USER, server_default=Role.USER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    # When the user clicked their verification link. NULL means unverified; legacy
    # accounts created before verification existed also sit here and are treated as verified.
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Running points totals. The points ledger is the source of truth; these
    # columns exist so the profile and leaderboard can read them in one query.
    points_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    points_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    points_lifetime: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Staff-granted permission: contributions and vault links publish without review.
    can_auto_approve: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
