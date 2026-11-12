"""Create the first super admin: uv run python -m app.scripts.create_admin"""

import asyncio
import getpass
import sys

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal, engine
from app.core.security import hash_password, password_problem
from app.models.user import Role, User

_EMAIL = TypeAdapter(EmailStr)


class AdminCreationError(ValueError):
    pass


async def create_admin_user(session: AsyncSession, email: str, password: str) -> User:
    try:
        email = _EMAIL.validate_python(email.strip().lower())
    except ValidationError as exc:
        raise AdminCreationError("Enter a valid email address.") from exc
    if problem := password_problem(password):
        raise AdminCreationError(problem)
    if await session.scalar(select(User.id).where(User.email == email)) is not None:
        raise AdminCreationError("A user with this email already exists; refusing to change it.")
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name="Super Admin",
        role=Role.SUPER_ADMIN,
    )
    session.add(user)
    await session.commit()
    return user


async def _main() -> int:
    email = input("Admin email: ")
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Confirm password: "):
        print("Error: passwords do not match.")
        return 1
    try:
        async with SessionLocal() as session:
            user = await create_admin_user(session, email, password)
    except AdminCreationError as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        await engine.dispose()
    print(f"Created super admin: {user.email}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
