"""Public member profile: work a member has actually contributed, plus rank.

This is the reputation face of the site — anyone can view it, only no-indexed
accounts are hidden. It shows lifetime points, the current level, and the leaderboard
standing so a visitor sees the member's standing without signing in.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.models.community import Contribution, ContributionStatus
from app.models.user import User
from app.services import reputation

router = APIRouter(include_in_schema=False)


@router.get("/member/{username}/", response_class=HTMLResponse)
async def member_profile(
    request: Request, username: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    user = await session.scalar(select(User).where(User.display_name == username))
    if user is None or not user.is_active:
        raise HTTPException(status_code=404, detail="Member not found.")

    rank = await session.scalar(select(func.count(User.id)).where(User.points_lifetime > user.points_lifetime)) + 1
    contributions = (
        await session.scalars(select(Contribution).where(Contribution.user_id == user.id, Contribution.status == ContributionStatus.APPROVED))
    ).all()

    return templates.TemplateResponse(
        request,
        "public/member_profile.html",
        {
            "page_title": user.display_name,
            "member": user,
            "level": reputation.level_for_user(user),
            "rank": rank,
            "approved_contributions": contributions,
            "no_index": user.role == "user" and user.points_lifetime == 0,
        },
    )