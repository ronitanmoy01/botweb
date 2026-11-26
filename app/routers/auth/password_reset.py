"""Forgot-password and password-reset flow.

The public contract is deliberately indistinguishable: whether or not an account
exists, the response is the same sentence and the same status code. Timing is
flattened too — an unknown email still costs one Argon2 verification, so an
attacker cannot use response latency to enumerate addresses.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.dependencies.auth import check_csrf, get_csrf_token
from app.models.tokens import PasswordResetToken
from app.models.user import User
from app.services import tokens
from app.services.admin_log import log_action
from app.services.email import enqueue_email

router = APIRouter(include_in_schema=False)

_TEMPLATE = "auth/forgot_password.html"
_GENERIC = "If an account exists with that email address, you will receive a password reset link shortly."
_RESET_URL = "/reset-password/"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _render(request: Request, name: str, title: str, status_code: int = 200, **ctx) -> HTMLResponse:
    context = {"page_title": title, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _locked(email: str) -> bool:
    return login_locked(f"reset:{email}")


@router.get("/forgot-password/", response_class=HTMLResponse)
async def forgot_password_form(request: Request) -> HTMLResponse:
    return _render(request, _TEMPLATE, "Forgot password")


@router.post("/forgot-password/", response_class=HTMLResponse)
async def forgot_password_submit(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    check_csrf(request, csrf_token)
    email = email.strip().lower()
    if _locked(email):
        return _render(request, _TEMPLATE, "Forgot password", status_code=429, errors=["Too many requests. Try again in a few minutes."], email=email)
    if email:
        record_login_failure(f"reset:{email}")

    user = await session.scalar(select(User).where(User.email == email))
    # Always verify against *something* so both paths cost the same time.
    verify_password("0x-not-a-real-password", user.password_hash if user else DUMMY_PASSWORD_HASH)

    if user is None or not user.is_active:
        return _render(request, _TEMPLATE, "Forgot password", status_code=200, success=_GENERIC, email=email)

    settings = get_settings()
    raw = await tokens.issue_token(
        session, PasswordResetToken, user.id,
        lifetime=timedelta(minutes=settings.password_reset_token_expire_minutes),
    )
    link = f"{settings.app_base_url.rstrip('/')}{_RESET_URL}{raw}"
    await enqueue_email(
        session,
        recipient=user.email,
        subject=f"Reset your {settings.site_name} password",
        template_name="password_reset",
        template_data={
            "display_name": user.display_name,
            "reset_link": link,
            "expires_at": (_now() + timedelta(minutes=settings.password_reset_token_expire_minutes)).strftime("%Y-%m-%d %H:%M UTC"),
        },
        dedupe_key=f"password_reset:{user.id}:{raw[:16]}",
    )
    log_action(session, user, "password.reset_requested", target=user.email)
    await session.commit()
    return _render(request, _TEMPLATE, "Forgot password", status_code=200, success=_GENERIC, email=email)


@router.get("/reset-password/{token}/", response_class=HTMLResponse)
async def reset_password_form(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    row = await tokens.resolve_token(session, PasswordResetToken, token)
    if row is None:
        return _render(request, _TEMPLATE, "Reset password", status_code=400, errors=["This reset link is invalid or has expired."])
    return _render(request, "auth/reset_password.html", "Reset password", token=token)


@router.post("/reset-password/{token}/", response_class=HTMLResponse)
async def reset_password_submit(
    request: Request,
    token: str,
    password: str = Form(""),
    confirm: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    check_csrf(request, csrf_token)
    errors: list[str] = []
    if problem := password_problem(password):
        errors.append(problem)
    if password != confirm:
        errors.append("Passwords do not match.")

    row = await tokens.resolve_token(session, PasswordResetToken, token)
    if row is None:
        # Same generic failure whether the token is unknown, used or expired.
        errors.append("This reset link is invalid or has expired.")
        return _render(request, _TEMPLATE, "Reset password", status_code=400, errors=errors)
    if errors:
        return _render(request, "auth/reset_password.html", "Reset password", status_code=400, errors=errors, token=token)

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return _render(request, _TEMPLATE, "Reset password", status_code=400, errors=["This account is no longer available."])

    settings = get_settings()
    user.password_hash = hash_password(password)
    await tokens.consume_token(session, PasswordResetToken, row)
    # The just-consumed token is the only one that survives; every other link dies.
    await tokens.invalidate_except(session, PasswordResetToken, user.id, row.id)
    clear_login_failures(user.email)
    log_action(session, user, "password.reset", target=user.email)
    await enqueue_email(
        session,
        recipient=user.email,
        subject=f"Your {settings.site_name} password was changed",
        template_name="password_changed",
        template_data={"display_name": user.display_name, "changed_at": _now().strftime("%Y-%m-%d %H:%M UTC"), "not_is_you": False},
        dedupe_key=f"password_changed:{user.id}:{row.id}",
    )
    await session.commit()
    # Not logged in automatically: the user proves they own the new password next.
    return _render(request, _TEMPLATE, "Reset password", status_code=200, success="Your password has been changed. You can now sign in.")
