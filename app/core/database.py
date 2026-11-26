from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode

from sqlalchemy import event
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
# pool_timeout only applies to a real pool; NullPool (SQLite) rejects it.
_engine_kwargs = {"poolclass": NullPool} if _url.startswith("sqlite") else {"pool_pre_ping": True, "connect_args": {"ssl": "require"}, "pool_timeout": 30}
engine = create_async_engine(_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)

_CONNECT_ATTEMPTS = 3
_CONNECT_PAUSE_SECONDS = 0.5


async def _ping(session: AsyncSession) -> None:
    """Force a live connection. Only a reachable database returns normally."""
    from sqlalchemy import text

    await session.execute(text("SELECT 1"))


# SQLite async engine uses a sync connection pool internally; the "connect" event
# fires on the underlying sync DBAPI connection, so this works for aiosqlite too.
@event.listens_for(engine.sync_engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_conn, connection_record):
    """SQLite does not enforce foreign keys by default; turn them on so ON DELETE
    CASCADE works for dev and tests. PostgreSQL enforces FKs natively.

    aiosqlite arrives as sqlalchemy.dialects.sqlite.aiosqlite.AsyncAdapt_aiosqlite_connection
    and sync sqlite as sqlite3.Connection, so match on "sqlite" in the module path.
    """
    if "sqlite" in type(dbapi_conn).__module__:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, retrying only the initial connection.

    DNS for the Neon hostname is the one step that fails intermittently
    (``socket.gaierror: getaddrinfo failed``), and it happens at connect time, so
    a bounded retry here absorbs it before any request handler can 500. Query
    errors after that are left to the caller — they are real bugs, not retries.
    """
    import asyncio

    last_error: Exception | None = None
    for attempt in range(_CONNECT_ATTEMPTS):
        session = SessionLocal()
        try:
            await _ping(session)
        except Exception as exc:  # noqa: BLE001 - reconnect on any connect failure
            last_error = exc
            await session.close()
            if attempt == _CONNECT_ATTEMPTS - 1:
                raise
            await asyncio.sleep(_CONNECT_PAUSE_SECONDS * (attempt + 1))
        else:
            break

    async with session:
        yield session
