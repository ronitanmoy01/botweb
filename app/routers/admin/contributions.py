"""Admin: review member contributions and reward approved ones with points.

Approval is the point of the system: a contribution only becomes reputation
when a human has looked at it, and the reward is granted exactly once via the
points ledger's idempotency key so a retried approve cannot double-credit.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.community import Contribution, ContributionStatus, PointsReason
from app.models.user import Role, User
from app.services import points
from app.services.admin_log import log_action

router = APIRouter(include_in_schema=False, prefix="/admin-panel/contributions")
_URL = "/admin-panel/contributions/"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.get("/", response_class=HTMLResponse)
async def contributions_list(
    request: Request, admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.EDITOR)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    pending = (
        await session.scalars(select(Contribution).where(Contribution.status == ContributionStatus.PENDING).order_by(Contribution.created_at.asc()))
    ).all()
    reviewed = (
        await session.scalars(
            select(Contribution).where(Contribution.status != ContributionStatus.PENDING).order_by(Contribution.created_at.desc()).limit(50)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "admin/contributions.html",
        {
            "page_title": "Contributions",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "pending": pending,
            "reviewed": reviewed,
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{contribution_id}/approve/", response_model=None)
async def approve_contribution(
    request: Request,
    contribution_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    contribution = await session.get(Contribution, contribution_id)
    if contribution is None or contribution.status != ContributionStatus.PENDING:
        request.session["flash_message"] = "That contribution is no longer pending."
        return RedirectResponse(_URL, status_code=303)

    settings = get_settings()
    reward = settings.points_per_approved_contribution
    author = await session.get(User, contribution.user_id)
    credited = False
    if author is not None and reward > 0:
        await points.credit(
            session, author, reward, PointsReason.CONTRIBUTION_REWARD,
            f"contribution:reward:{contribution.id}",
            reference_type="contribution", reference_id=contribution.id,
        )
        credited = True

    contribution.status = ContributionStatus.APPROVED
    contribution.reviewed_by = admin.id
    contribution.reviewed_at = _now()
    contribution.points_reward = reward if credited else 0
    log_action(session, admin, "contribution.approved", target=contribution.title[:255], detail=f"+{reward} pts" if credited else "no reward")
    await session.commit()
    request.session["flash_message"] = f"Approved “{contribution.title}”" + (f" — {reward} points awarded." if credited else ".")
    return RedirectResponse(_URL, status_code=303)


@router.post("/{contribution_id}/reject/", response_model=None)
async def reject_contribution(
    request: Request,
    contribution_id: uuid.UUID,
    note: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN, Role.EDITOR)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    contribution = await session.get(Contribution, contribution_id)
    if contribution is None or contribution.status != ContributionStatus.PENDING:
        request.session["flash_message"] = "That contribution is no longer pending."
        return RedirectResponse(_URL, status_code=303)

    contribution.status = ContributionStatus.REJECTED
    contribution.reviewed_by = admin.id
    contribution.review_note = note.strip()[:500]
    contribution.reviewed_at = _now()
    log_action(session, admin, "contribution.rejected", target=contribution.title[:255], detail=contribution.review_note)
    await session.commit()
    request.session["flash_message"] = f"Rejected “{contribution.title}”."
    return RedirectResponse(_URL, status_code=303)