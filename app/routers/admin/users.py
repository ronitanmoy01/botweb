import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.user import ADMIN_ROLES, Role, User
from app.services.admin_log import log_action

router = APIRouter(include_in_schema=False, prefix="/admin-panel/users")
_URL = "/admin-panel/users/"
_ROLE_VALUES = {r.value for r in Role}


def _may_manage(actor: User, target: User) -> bool:
    # Admins manage regular accounts only; super admins manage everyone.
    return actor.role == Role.SUPER_ADMIN or target.role not in ADMIN_ROLES


async def _is_last_active_super_admin(session: AsyncSession, target: User) -> bool:
    """True when target is the only active super admin, so removing it would lock everyone out."""
    if target.role != Role.SUPER_ADMIN or not target.is_active:
        return False
    others = await session.scalar(
        select(func.count(User.id)).where(User.role == Role.SUPER_ADMIN, User.is_active.is_(True), User.id != target.id)
    )
    return others == 0


@router.get("/", response_class=HTMLResponse)
async def users_list(
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    users = (await session.scalars(select(User).order_by(User.created_at.desc()))).all()
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "page_title": "Users",
            "users": users,
            "roles": [r.value for r in Role],
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{user_id}/role/", response_model=None)
async def change_role(
    request: Request,
    user_id: uuid.UUID,
    role: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    target = await session.get(User, user_id)
    if target is None or role not in _ROLE_VALUES:
        request.session["flash_message"] = "Invalid request"
    elif target.id == admin.id:
        request.session["flash_message"] = "You cannot change your own role"
    elif role != Role.SUPER_ADMIN and await _is_last_active_super_admin(session, target):
        request.session["flash_message"] = "The last active super admin cannot be demoted"
    else:
        old = target.role
        target.role = role
        log_action(session, admin, "user.role", target=target.email, detail=f"{old} -> {role}")
        await session.commit()
        request.session["flash_message"] = f"Role updated to {role.replace('_', ' ')}"
    return RedirectResponse(_URL, status_code=303)


@router.post("/{user_id}/status/", response_model=None)
async def change_status(
    request: Request,
    user_id: uuid.UUID,
    is_active: bool = Form(...),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    target = await session.get(User, user_id)
    if target is None:
        request.session["flash_message"] = "Invalid request"
    elif target.id == admin.id:
        request.session["flash_message"] = "You cannot change your own status"
    elif not _may_manage(admin, target):
        request.session["flash_message"] = "Only a super admin can change an admin account"
    elif not is_active and await _is_last_active_super_admin(session, target):
        request.session["flash_message"] = "The last active super admin cannot be banned"
    else:
        target.is_active = is_active
        log_action(session, admin, "user.unban" if is_active else "user.ban", target=target.email)
        await session.commit()
        request.session["flash_message"] = "User reactivated" if is_active else "User banned"
    return RedirectResponse(_URL, status_code=303)
