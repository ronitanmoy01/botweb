# Import every model here so Alembic autogenerate and Base.metadata see all tables.
from app.models.analytics import View
from app.models.audit import AuditLog
from app.models.catalog import Category, Country, Genre, Language, Movie
from app.models.redirect import SlugRedirect
from app.models.user import User

__all__ = ["AuditLog", "Category", "Country", "Genre", "Language", "Movie", "SlugRedirect", "User", "View"]
