"""Admin: review registration applications and invitations.

Approval creates the real account and finalises the inviter's points charge in
one transaction, so an application can never be approved twice or left half-done.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import hash_password
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, require_roles
from app.models.community import (
    ApplicationStatus,
    Contribution,
    Invitation,
    InvitationStatus,
    PointsReason,
    RegistrationApplication,
)
from app.models.user import Role, User
from app.services import invitations, points, tokens
from app.services.admin_log import log_action
from app.services.email import enqueue_email

router = APIRouter(include_in_schema=False, prefix="/admin-panel/applications")
_URL = "/admin-panel/applications/"


@router.get("/", response_class=HTMLResponse)
async def applications_list(
    request: Request, admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    pending = (
        await session.scalars(select(RegistrationApplication).where(RegistrationApplication.status == ApplicationStatus.PENDING).order_by(RegistrationApplication.created_at.asc()))
    ).all()
    decided = (
        await session.scalars(
            select(RegistrationApplication)
            .where(RegistrationApplication.status != ApplicationStatus.PENDING)
            .order_by(RegistrationApplication.created_at.desc())
            .limit(50)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "admin/applications.html",
        {
            "page_title": "Registration applications",
            "actor": admin,
            "csrf_token": get_csrf_token(request),
            "pending": pending,
            "decided": decided,
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{application_id}/approve/", response_model=None)
async def approve_application(
    request: Request,
    application_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    app = await session.get(RegistrationApplication, application_id)
    if app is None or app.status != ApplicationStatus.PENDING:
        request.session["flash_message"] = "That application is no longer pending."
        return RedirectResponse(_URL, status_code=303)

    if await session.scalar(select(User.id).where(User.email == app.email)):
        app.status = ApplicationStatus.REJECTED
        app.decided_by = admin.id
        app.decision_note = "Account already exists for that email."
        app.decided_at = app.decided_at or _now()
        log_action(session, admin, "application.rejected", target=app.email, detail="email already registered")
        await session.commit()
        request.session["flash_message"] = "That email already has an account; the application was closed."
        return RedirectResponse(_URL, status_code=303)

    app.decided_by = admin.id
    app.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)

    user = User(email=app.email, password_hash=app.password_hash, display_name=app.display_name[:100], role=Role.USER)
    session.add(user)
    app.user_id = None  # set after flush so the FK resolves
    await session.flush()
    app.user_id = user.id
    app.status = ApplicationStatus.APPROVED
    app.decision_note = (app.decision_note or "")[:500]

    # If the applicant used an invitation, finalise it: the charge is accepted, the
    # reserve is released, and the row is closed so it can never be refunded twice.
    invite = await session.get(Invitation, app.invite_id) if app.invite_id else None
    if invite is not None and invite.invited_by:
        amount = invite.points_reserved
        invite.status = InvitationStatus.ACCEPTED
        invite.accepted_at = invite.accepted_at or _now()
        invite.points_reserved = 0
        inviter = await session.get(User, invite.invited_by)
        if inviter is not None and amount:
            inviter.points_reserved = max(0, int(inviter.points_reserved) - amount)
            # The inviter earns the cost back once the invitee actually joins, so a
            # successful invite costs nothing and builds reputation — same as the
            # invitation service's own accept path.
            await points.credit(
                session, inviter, amount, PointsReason.INVITEE_JOINED,
                f"invitation:reward:{invite.id}",
                reference_type="invitation", reference_id=invite.id,
            )

    log_action(session, admin, "application.approved", target=app.email)
    await _queue_verification(session, user)
    await session.commit()
    request.session["flash_message"] = f"Approved {app.email}."
    return RedirectResponse(_URL, status_code=303)


@router.post("/{application_id}/reject/", response_model=None)
async def reject_application(
    request: Request,
    application_id: uuid.UUID,
    note: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(Role.SUPER_ADMIN, Role.ADMIN)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    app = await session.get(RegistrationApplication, application_id)
    if app is None or app.status != ApplicationStatus.PENDING:
        request.session["flash_message"] = "That application is no longer pending."
        return RedirectResponse(_URL, status_code=303)

    from datetime import datetime, timezone

    app.status = ApplicationStatus.REJECTED
    app.decided_by = admin.id
    app.decision_note = note.strip()[:500]
    app.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log_action(session, admin, "application.rejected", target=app.email, detail=app.decision_note)
    await session.commit()
    request.session["flash_message"] = f"Rejected {app.email}."
    return RedirectResponse(_URL, status_code=303)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _queue_verification(session: AsyncSession, user: User) -> None:
    """Send the new account its verification link, once, after approval."""
    from datetime import timedelta

    from app.core.config import get_settings
    from app.models.tokens import EmailVerificationToken
    from app.services.email import enqueue_email

    settings = get_settings()
    raw = await tokens.issue_token(
        session, EmailVerificationToken, user.id,
        lifetime=timedelta(hours=settings.email_verification_token_expire_hours),
    )
    await enqueue_email(
        session,
        recipient=user.email,
        subject=f"Verify your {settings.site_name} email",
        template_name="email_verification",
        template_data={
            "display_name": user.display_name,
            "recipient_email": user.email,
            "verify_link": f"{settings.app_base_url.rstrip('/')}/verify-email/{raw}/",
            "expires_at": (_now() + timedelta(hours=settings.email_verification_token_expire_hours)).strftime("%Y-%m-%d %H:%M UTC"),
        },
        dedupe_key=f"email_verification:{user.id}:{raw[:16]}",
    )