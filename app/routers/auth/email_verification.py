"""Email verification: confirm a user owns the address on their account.

``GET /verify-email/{token}`` marks the account verified and consumes the token.
``POST /resend-verification`` re-issues a link, throttled by the existing in-memory
lockout store so a mailbox cannot be spammed by repeated submits.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import login_locked, record_login_failure
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, get_current_user
from app.models.tokens import EmailVerificationToken
from app.models.user import User
from app.services import tokens
from app.services.admin_log import log_action
from app.services.email import enqueue_email

router = APIRouter(include_in_schema=False)

_KEY = "verification:"
_DONE = "auth/verification_done.html"
_PENDING = "auth/verification_pending.html"


def _render(request: Request, name: str, title: str, status_code: int = 200, **ctx) -> HTMLResponse:
    context = {"page_title": title, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _is_verified(user: User) -> bool:
    return user.email_verified_at is not None


@router.get("/verify-email/{token}/", response_class=HTMLResponse)
async def verify_email(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    row = await tokens.resolve_token(session, EmailVerificationToken, token)
    if row is None:
        return _render(request, _PENDING, "Verify email", status_code=400, errors=["This verification link is invalid or has expired."])

    user = await session.get(User, row.user_id)
    if user is None or not user.is_active:
        return _render(request, _PENDING, "Verify email", status_code=400, errors=["This account is no longer available."])

    user.email_verified_at = _utcnow()
    await tokens.consume_token(session, EmailVerificationToken, row)
    # Only one outstanding verification link should survive the click.
    await tokens.invalidate_except(session, EmailVerificationToken, user.id, row.id)
    log_action(session, user, "email.verified", target=user.email)
    await session.commit()
    return _render(request, _DONE, "Email verified")


@router.post("/resend-verification/", response_class=HTMLResponse)
async def resend_verification(
    request: Request,
    csrf_token: str = Form(""),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    check_csrf(request, csrf_token)
    if _is_verified(user):
        return _render(request, _DONE, "Email verified")
    if login_locked(_KEY + user.email):
        return _render(
            request, _PENDING, "Verify email", status_code=429,
            errors=["You just requested a new link. Please wait a minute before asking again."],
        )
    record_login_failure(_KEY + user.email)

    settings = get_settings()
    raw = await tokens.issue_token(
        session, EmailVerificationToken, user.id,
        lifetime=timedelta(hours=settings.email_verification_token_expire_hours),
    )
    link = f"{settings.app_base_url.rstrip('/')}/verify-email/{raw}/"
    await enqueue_email(
        session,
        recipient=user.email,
        subject=f"Verify your {settings.site_name} email",
        template_name="email_verification",
        template_data={
            "display_name": user.display_name,
            "recipient_email": user.email,
            "verify_link": link,
            "expires_at": (_utcnow() + timedelta(hours=settings.email_verification_token_expire_hours)).strftime("%Y-%m-%d %H:%M UTC"),
        },
        dedupe_key=f"email_verification:{user.id}:{raw[:16]}",
    )
    await session.commit()
    return _render(request, _PENDING, "Verify email", status_code=200, success="We sent a new verification link to your email.")


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)
