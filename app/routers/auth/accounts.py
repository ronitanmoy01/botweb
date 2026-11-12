from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from app.core.database import get_session
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    clear_login_failures,
    hash_password,
    login_locked,
    password_problem,
    record_login_failure,
    verify_password,
)
from app.core.templating import templates
from app.dependencies.auth import SESSION_USER_KEY, check_csrf, get_csrf_token
from app.models.user import Role, User

router = APIRouter(include_in_schema=False)
_EMAIL = TypeAdapter(EmailStr)
_REGISTER_FAILED = "We couldn't create an account with that email. If you already have one, sign in instead."


def _render(request: Request, name: str, title: str, status_code: int = 200, **ctx) -> Response:
    context = {"page_title": title, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _log_in(request: Request, user: User) -> None:
    request.session.clear()  # drop any pre-login session data so it can't carry over
    request.session[SESSION_USER_KEY] = str(user.id)


@router.get("/login/")
async def login_form(request: Request) -> Response:
    return _render(request, "auth/login.html", "Sign in")


@router.post("/login/")
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    check_csrf(request, csrf_token)
    email = email.strip().lower()
    if login_locked(email):
        return _render(
            request,
            "auth/login.html",
            "Sign in",
            status_code=429,
            errors=["Too many failed attempts. Try again in 15 minutes."],
            email=email,
        )

    user = await session.scalar(select(User).where(User.email == email))
    password_ok = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok or not user.is_active:
        record_login_failure(email)
        return _render(
            request, "auth/login.html", "Sign in", status_code=400,
            errors=["Invalid email or password."], email=email,
        )

    clear_login_failures(email)
    _log_in(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/register/")
async def register_form(request: Request) -> Response:
    return _render(request, "auth/register.html", "Create account")


@router.post("/register/")
async def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    check_csrf(request, csrf_token)
    # Role is never read from the form: self-registration always creates a plain user.
    email = email.strip().lower()
    errors: list[str] = []
    try:
        email = _EMAIL.validate_python(email)
    except ValidationError:
        errors.append("Enter a valid email address.")
    if problem := password_problem(password):
        errors.append(problem)

    # Same wording for "taken" and other failures, so the form can't be used to find which emails have accounts.
    if not errors and await session.scalar(select(User.id).where(User.email == email)):
        errors.append(_REGISTER_FAILED)

    if errors:
        return _render(
            request, "auth/register.html", "Create account", status_code=400,
            errors=errors, email=email, display_name=display_name,
        )

    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=display_name.strip()[:100],
        role=Role.USER,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError:  # two registrations with the same email raced past the check above
        await session.rollback()
        return _render(
            request, "auth/register.html", "Create account", status_code=400,
            errors=[_REGISTER_FAILED], email=email,
        )
    _log_in(request, user)
    return RedirectResponse("/", status_code=303)


@router.post("/logout/")
async def logout(request: Request, csrf_token: str = Form("")) -> Response:
    check_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)
