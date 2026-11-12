from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _drop_asyncpg_unknown_params(url: str) -> str:
    """Strip query params asyncpg's connect() does not accept.

    asyncpg's connect() has no `sslmode` or `channel_binding` keyword (no
    **kwargs catch-all either). `sslmode` is read from the PGSSLMODE env var
    or defaults to 'prefer' for TCP hosts — TLS is negotiated on upgrade, so
    Neon's required-TLS works without it. `channel_binding` is libpq/psycopg
    only. Drop both so SQLAlchemy stops forwarding them as keywords.
    """
    marker = "?"
    if marker not in url:
        return url
    base, query = url.split(marker, 1)
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k not in ("sslmode", "channel_binding")]
    return f"{base}?{urlencode(kept)}" if kept else base


def get_database_url() -> str:
    """Return the sanitized database URL for alembic + app use."""
    return _drop_asyncpg_unknown_params(get_settings().database_url)


_url = get_database_url()
# shortcut: SQLite uses NullPool (no pooled connections across event loops), PostgreSQL uses a pool; revisit if SQLite serves real traffic
engine = create_async_engine(_url, **({"poolclass": NullPool} if _url.startswith("sqlite") else {"pool_pre_ping": True}))
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
