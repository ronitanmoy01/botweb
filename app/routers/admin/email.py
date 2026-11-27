"""Admin: email configuration and a live send-test, plus the outbox log.

Credentials are never written back to a settings file — the admin edits them in
the running process only. They reset on restart, which is deliberate: a persistent
SMTP password in a browser session is worse than re-entering it.
"""

import asyncio

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.email import EmailOutbox, EmailStatus
from app.models.user import Role, User
from app.services.email import render_email, send_message

router = APIRouter(include_in_schema=False, prefix="/admin-panel/email")
_URL = "/admin-panel/email/"


@router.get("/", response_class=HTMLResponse)
async def email_settings(
    request: Request, admin: User = Depends(require_roles(Role.SUPER_ADMIN)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    settings = get_settings()
    queued = await session.scalar(select(func.count(EmailOutbox.id)).where(EmailOutbox.status == EmailStatus.PENDING))
    failed = await session.scalar(
        select(func.count(EmailOutbox.id)).where(EmailOutbox.status == EmailStatus.FAILED)
    )
    recent = (
        await session.scalars(select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(50))
    ).all()

    return templates.TemplateResponse(
        request,
        "admin/email_settings.html",
        {
            "page_title": "Email settings",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "current": settings,
            "configured": settings.email_configured,
            "queued": queued or 0,
            "failed": failed or 0,
            "recent": recent,
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/settings/", response_model=None)
async def save_settings(
    request: Request,
    mail_enabled: bool = Form(False),
    smtp_host: str = Form(""),
    smtp_port: int = Form(587),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    mail_from_address: str = Form(""),
    mail_from_name: str = Form("MovieHub"),
    use_ssl: bool = Form(False),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN)),
):
    """Apply SMTP settings to the running process only."""
    check_csrf(request, csrf_token)
    current = get_settings()  # the lru_cache'd singleton the app actually reads
    current.mail_enabled = mail_enabled
    current.smtp_host = smtp_host.strip()
    current.smtp_port = smtp_port
    current.smtp_username = smtp_username.strip()
    if smtp_password.strip():
        current.smtp_password = smtp_password.strip()
    current.mail_from_address = mail_from_address.strip()
    current.mail_from_name = mail_from_name.strip() or "MovieHub"
    current.smtp_use_ssl = use_ssl
    current.smtp_use_tls = not use_ssl

    request.session["flash_message"] = "Email settings saved for this session."
    return RedirectResponse(_URL, status_code=303)


@router.post("/test/", response_model=None)
async def send_test(
    request: Request,
    recipient: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN)),
):
    """Send a real test email using the current settings."""
    check_csrf(request, csrf_token)
    settings = get_settings()
    recipient = recipient.strip().lower()
    html, text = render_email(
        "test_email",
        {"display_name": admin.display_name or "there", "from_address": settings.mail_from_address},
    )
    try:
        await send_message(
            recipient, f"{settings.site_name} — email test", html, text
        )
    except Exception as exc:  # noqa: BLE001 - surface the failure, don't kill the panel
        request.session["flash_message"] = f"Test email failed: {type(exc).__name__}: {exc}"
        return RedirectResponse(_URL, status_code=303)
    request.session["flash_message"] = f"Test email sent to {recipient}."
    return RedirectResponse(_URL, status_code=303)