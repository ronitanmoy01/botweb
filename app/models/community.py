"""Community tables: the points ledger, invitations, registration applications
and member contributions.

Every points movement is written to ``points_ledger`` with an idempotency key, so
a balance can always be rebuilt from the ledger and a retried request can never
double-charge or double-credit. The ``users`` table keeps the running totals for
fast reads; the ledger is the source of truth.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ApplicationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ContributionStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PointsReason(StrEnum):
    INVITATION_CHARGE = "invitation_charge"
    INVITATION_REFUND = "invitation_refund"
    INVITEE_JOINED = "invitee_joined"
    CONTRIBUTION_REWARD = "contribution_reward"


class PointsLedger(Base):
    """One immutable row per points balance change.

    ``idempotency_key`` is unique, so the same logical movement can never be
    recorded twice even if the request is retried.
    """

    __tablename__ = "points_ledger"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Signed: positive credits the balance, negative debits it.
    delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(64))
    reference_type: Mapped[str] = mapped_column(String(32), default="", server_default="")
    reference_id: Mapped[uuid.UUID | None] = mapped_column(default=None)
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Invitation(Base):
    """An invite a member spends points on. The code is stored hashed, like the
    email tokens, so a database leak cannot be used to sign up."""

    __tablename__ = "invitations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    invited_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    invitee_email: Mapped[str] = mapped_column(String(320), default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default=InvitationStatus.PENDING, server_default=InvitationStatus.PENDING, index=True)
    # Points held from the inviter while the invite is pending.
    points_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class RegistrationApplication(Base):
    """A request to join. Approved by an admin, which creates the account."""

    __tablename__ = "registration_applications"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    # Set when the applicant used an invite; the invite is charged on approval.
    invite_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"), default=None)
    status: Mapped[str] = mapped_column(String(16), default=ApplicationStatus.PENDING, server_default=ApplicationStatus.PENDING, index=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    decision_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # The account created when the application was approved.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Contribution(Base):
    """A member's proposed catalogue addition or correction."""

    __tablename__ = "contributions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    # JSON-encoded proposed fields, e.g. {"movie_id": "...", "field": "plot", "value": "..."}
    payload: Mapped[str] = mapped_column(Text, default="", server_default="")
    status: Mapped[str] = mapped_column(String(16), default=ContributionStatus.PENDING, server_default=ContributionStatus.PENDING, index=True)
    points_reward: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    review_note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
