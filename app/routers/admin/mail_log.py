"""Admin: the email outbox log. Shows every queued/sent/failed message and why."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import get_csrf_token, require_roles
from app.models.email import EmailOutbox, EmailStatus
from app.models.user import Role, User

router = APIRouter(include_in_schema=False, prefix="/admin-panel/email/log")


@router.get("/", response_class=HTMLResponse)
async def mail_log(
    request: Request, admin: User = Depends(require_roles(Role.SUPER_ADMIN)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    jobs = (
        await session.scalars(select(EmailOutbox).order_by(EmailOutbox.created_at.desc()).limit(300))
    ).all()
    return templates.TemplateResponse(
        request,
        "admin/mail_log.html",
        {
            "page_title": "Email log",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "jobs": jobs,
            "statuses": [s.value for s in EmailStatus],
        },
    )