import os
import tempfile
from pathlib import Path

import pytest

# Must run before any app module is imported: settings and the engine are read at import time.
_test_dir = Path(tempfile.mkdtemp(prefix="moviehub-tests-"))
os.environ["MOVIEHUB_ENVIRONMENT"] = "test"
os.environ["MOVIEHUB_DATABASE_URL"] = f"sqlite+aiosqlite:///{(_test_dir / 'test.db').as_posix()}"
os.environ.pop("MOVIEHUB_SECRET_KEY", None)


@pytest.fixture(scope="session", autouse=True)
def _create_tables():
    import asyncio

    from app.core.database import Base, engine
    import app.models  # noqa: F401  register all models

    async def create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())


@pytest.fixture(autouse=True)
def _isolate_state():
    """Every test starts with no users, no login lockouts, and no catalog rows."""
    import asyncio

    from sqlalchemy import delete

    from app.core import security
    from app.core.database import SessionLocal, engine
    from app.models.catalog import Movie, Series
    from app.models.community import Invitation, RegistrationApplication
    from app.models.email import EmailOutbox
    from app.models.user import User
    from app.models.vault import VaultLink

    async def clear() -> None:
        async with SessionLocal() as session:
            # Foreign keys cascade, so clearing movies/series clears junctions,
            # favorites, reviews, watchlists, comments and seasons/episodes.
            # Deleting a user also cascades their tokens, ledger rows and invitations.
            # Applications point at users with SET NULL, and the outbox has no
            # user FK at all, so both need an explicit delete. Vault links point
            # at both titles and users, so they cascade on either side.
            await session.execute(delete(Movie))
            await session.execute(delete(Series))
            await session.execute(delete(EmailOutbox))
            await session.execute(delete(RegistrationApplication))
            await session.execute(delete(Invitation))
            await session.execute(delete(VaultLink))
            await session.execute(delete(User))
            await session.commit()
        await engine.dispose()

    asyncio.run(clear())
    security._login_failures.clear()
    yield
