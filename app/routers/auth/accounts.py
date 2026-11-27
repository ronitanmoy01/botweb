from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

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
from app.dependencies.auth import SESSION_USER_KEY, check_csrf, get_csrf_token
from app.models.user import User
from app.services import invitations, tokens

router = APIRouter(include_in_schema=False)
_EMAIL = TypeAdapter(EmailStr)
_REGISTER_FAILED = "We couldn't create an account with that email. If you already have one, sign in instead."
INVITE_SESSION_KEY = "pending_invite"


def _render(request: Request, name: str, title: str, status_code: int = 200, **ctx) -> Response:
    context = {"page_title": title, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _log_in(request: Request, user: User) -> None:
    request.session.clear()  # drop any pre-login session data so it can't carry over
    request.session[SESSION_USER_KEY] = str(user.id)


@router.get("/login/")
async def login_form(request: Request) -> Response:
    return _render(request, "auth/login.html", "Sign in")


@router.post("/login/")
async def login_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    check_csrf(request, csrf_token)
    email = email.strip().lower()
    if login_locked(email):
        return _render(
            request,
            "auth/login.html",
            "Sign in",
            status_code=429,
            errors=["Too many failed attempts. Try again in 15 minutes."],
            email=email,
        )

    user = await session.scalar(select(User).where(User.email == email))
    password_ok = verify_password(password, user.password_hash if user else DUMMY_PASSWORD_HASH)
    if user is None or not password_ok or not user.is_active:
        record_login_failure(email)
        return _render(
            request, "auth/login.html", "Sign in", status_code=400,
            errors=["Invalid email or password."], email=email,
        )

    clear_login_failures(email)
    _log_in(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/register/")
async def register_form(request: Request, invite: str = Query(""), session: AsyncSession = Depends(get_session)) -> Response:
    # Registration is invite-only. Without a valid invitation, show the closed page;
    # with one, pre-fill the form and remember the code in the session for the POST.
    invite = invite.strip()
    if not invite:
        return _render(request, "auth/register_closed.html", "Create account")
    row = await invitations.find_pending_invitation(session, invite)
    if row is None:
        return _render(
            request, "auth/register_closed.html", "Create account", status_code=400,
            errors=["That invitation is invalid or has already been used."],
        )
    request.session[INVITE_SESSION_KEY] = invite
    return _render(
        request, "auth/register.html", "Create account",
        invite=row.invitee_email, invite_code=invite,
    )


@router.post("/register/")
async def register_submit(
    request: Request,
    email: str = Form(""),
    password: str = Form(""),
    display_name: str = Form(""),
    invite: str = Form(""),
    csrf_token: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    check_csrf(request, csrf_token)
    # Role is never read from the form: self-registration always creates a plain user.
    email = email.strip().lower()
    errors: list[str] = []
    try:
        email = _EMAIL.validate_python(email)
    except ValidationError:
        errors.append("Enter a valid email address.")
    if problem := password_problem(password):
        errors.append(problem)

    # An invitation is mandatory; the code comes from the form or from the session
    # the GET handler stored when the invite link was opened.
    code = invite.strip() or request.session.get(INVITE_SESSION_KEY, "")
    invite_row = await invitations.find_pending_invitation(session, code, email)
    if invite_row is None:
        errors.append("A valid invitation is required to create an account.")

    # Same wording for "taken" and other failures, so the form can't be used to find which emails have accounts.
    if not errors and await session.scalar(select(User.id).where(User.email == email)):
        errors.append(_REGISTER_FAILED)

    if errors:
        return _render(
            request, "auth/register.html" if code else "auth/register_closed.html",
            "Create account", status_code=400,
            errors=errors, email=email, display_name=display_name,
        )

    # Registration does NOT create the account. A RegistrationApplication goes to
    # the admin queue; an admin approving it creates the user and sends the
    # verification link. This keeps membership gated behind a human decision.
    from app.models.community import RegistrationApplication

    app = RegistrationApplication(
        email=email,
        display_name=display_name.strip()[:100],
        password_hash=hash_password(password),
        invite_id=invite_row.id,
    )
    session.add(app)
    try:
        await session.commit()
    except IntegrityError:  # two registrations with the same email raced past the check above
        await session.rollback()
        return _render(
            request, "auth/register.html", "Create account", status_code=400,
            errors=[_REGISTER_FAILED], email=email,
        )
    request.session.pop(INVITE_SESSION_KEY, None)
    return _render(
        request, "auth/application_pending.html", "Application submitted",
        email=email, display_name=display_name,
    )


async def _queue_verification(session: AsyncSession, user: User) -> None:
    """New accounts must confirm their email; the worker delivers the link."""
    from datetime import timedelta

    from app.core.config import get_settings
    from app.models.tokens import EmailVerificationToken
    from app.services.email import enqueue_email

    settings = get_settings()
    raw = await tokens.issue_token(session, EmailVerificationToken, user.id, lifetime=timedelta(hours=settings.email_verification_token_expire_hours))
    await enqueue_email(
        session,
        recipient=user.email,
        subject=f"Verify your {settings.site_name} email",
        template_name="email_verification",
        template_data={
            "display_name": user.display_name,
            "recipient_email": user.email,
            "verify_link": f"{settings.app_base_url.rstrip('/')}/verify-email/{raw}/",
            "expires_at": (_utcnow() + timedelta(hours=settings.email_verification_token_expire_hours)).strftime("%Y-%m-%d %H:%M UTC"),
        },
        dedupe_key=f"email_verification:{user.id}:{raw[:16]}",
    )


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)


@router.post("/logout/")
async def logout(request: Request, csrf_token: str = Form("")) -> Response:
    check_csrf(request, csrf_token)
    request.session.clear()
    return RedirectResponse("/", status_code=303)
