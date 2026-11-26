# Import every model here so Alembic autogenerate and Base.metadata see all tables.
from app.models.analytics import View
from app.models.audit import AuditLog
from app.models.catalog import Category, Country, Genre, Language, Movie
from app.models.community import (
    Contribution,
    Invitation,
    PointsLedger,
    RegistrationApplication,
)
from app.models.email import EmailOutbox
from app.models.redirect import SlugRedirect
from app.models.tokens import EmailVerificationToken, PasswordResetToken
from app.models.user import User
from app.models.vault import VaultLink

__all__ = [
    "AuditLog",
    "Category",
    "Contribution",
    "Country",
    "EmailOutbox",
    "EmailVerificationToken",
    "Genre",
    "Invitation",
    "Language",
    "Movie",
    "PasswordResetToken",
    "PointsLedger",
    "RegistrationApplication",
    "SlugRedirect",
    "User",
    "VaultLink",
    "View",
]
