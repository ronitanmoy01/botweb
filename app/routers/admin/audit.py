from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import require_roles
from app.models.audit import AuditLog
from app.models.user import Role, User

router = APIRouter(include_in_schema=False, prefix="/admin-panel/audit")


@router.get("/", response_class=HTMLResponse)
async def audit_list(
    request: Request,
    admin: User = Depends(require_roles(Role.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    rows = (await session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(200))).all()
    return templates.TemplateResponse(request, "admin/audit.html", {"page_title": "Audit log", "rows": rows})
