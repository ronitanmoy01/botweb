"""Leaderboard: the top members by lifetime points, with their reputation level.

Public — anyone can see who contributes the most. The lifetime figure is the
sort key so a member who earned 1,000 points six months ago still ranks; a
spendable balance would demote long-standing contributors for using what they earned.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.models.user import Role, User
from app.services import reputation

router = APIRouter(include_in_schema=False)
_TOP = 50
_STAFF_ROLES = (Role.SUPER_ADMIN, Role.ADMIN)


@router.get("/leaderboard/", response_class=HTMLResponse)
async def leaderboard(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    top = (
        await session.scalars(select(User).where(User.is_active.is_(True)).order_by(User.points_balance.desc(), User.created_at.asc()).limit(_TOP))
    ).all()
    # Admins and super admins rank here too — their contributions count like anyone else's.
    ranked = [(i + 1, user, reputation.level_for_user(user), user.role in _STAFF_ROLES) for i, user in enumerate(top)]
    return templates.TemplateResponse(
        request, "public/leaderboard.html",
        {"page_title": "Leaderboard", "ranked": ranked, "levels": reputation.all_levels()},
    )