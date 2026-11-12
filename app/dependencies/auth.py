import uuid
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import csrf_matches, new_csrf_token
from app.models.user import User

SESSION_USER_KEY = "user_id"
CSRF_KEY = "csrf_token"


def get_csrf_token(request: Request) -> str:
    if CSRF_KEY not in request.session:
        request.session[CSRF_KEY] = new_csrf_token()
    return request.session[CSRF_KEY]


def check_csrf(request: Request, sent: str) -> None:
    if not csrf_matches(sent, request.session.get(CSRF_KEY, "")):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")


async def get_current_user(request: Request, session: AsyncSession = Depends(get_session)) -> User | None:
    raw_id = request.session.get(SESSION_USER_KEY)
    if not raw_id:
        return None
    user = await session.get(User, uuid.UUID(raw_id))
    user = user if user and user.is_active else None
    request.state.current_user = user  # read by the template context processor
    return user


def require_roles(*roles: str) -> Callable[..., Awaitable[User]]:
    """Read the role from the database on every request, so demotions take effect immediately."""

    async def dependency(user: User | None = Depends(get_current_user)) -> User:
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in required.")
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions.")
        return user

    return dependency
