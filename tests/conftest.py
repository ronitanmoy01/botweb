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
    """Every test starts with no users and no login lockouts."""
    import asyncio

    from sqlalchemy import delete

    from app.core import security
    from app.core.database import SessionLocal, engine
    from app.models.user import User

    async def clear() -> None:
        async with SessionLocal() as session:
            await session.execute(delete(User))
            await session.commit()
        await engine.dispose()

    asyncio.run(clear())
    security._login_failures.clear()
    yield
