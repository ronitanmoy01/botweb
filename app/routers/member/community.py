"""Member community pages: invitations and contributions.

Invitations cost points up front (see ``app.services.invitations``), so the guard
here is a *spendable* balance, not a total: a member with 1,000 points already
spoken for on a pending invite cannot create another one.
"""

import asyncio
import json
import re
import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_session
from app.core.security import hash_password
from app.core.templating import templates
from app.dependencies.auth import check_csrf, get_csrf_token, get_current_user
from app.models.catalog import Movie, MovieStatus, Series
from app.models.community import Contribution, ContributionStatus, Invitation, InvitationStatus
from app.models.user import Role, User
from app.models.vault import VaultLink, VaultStatus
from app.services import invitations, points, vault
from app.services.admin_log import log_action
from app.services.media import slugify, unique_slug
from app.services.validation import safe_url

router = APIRouter(include_in_schema=False)


def _render(request: Request, name: str, title: str, status_code: int = 200, **ctx) -> HTMLResponse:
    context = {"page_title": title, "csrf_token": get_csrf_token(request), **ctx}
    return templates.TemplateResponse(request, name, context, status_code=status_code)


async def _render_invites(request: Request, user: User, session: AsyncSession, **extra) -> HTMLResponse:
    settings = get_settings()
    invites = (
        await session.scalars(
            select(Invitation)
            .where(Invitation.invited_by == user.id)
            .order_by(Invitation.created_at.desc())
        )
    ).all()
    return _render(
        request, "member/invites.html", "My invitations",
        user_points=points.available(user),
        points_total=int(user.points_balance),
        points_reserved=int(user.points_reserved),
        cost=settings.points_per_invited_member,
        invite_cost=settings.points_per_invited_member,
        base_url=settings.app_base_url.rstrip("/"),
        invites=invites,
        can_invite=points.can_invite(user, settings.points_per_invited_member),
        **extra,
    )


@router.get("/my/invites/", response_class=HTMLResponse)
async def my_invites(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    if user is None:
        return RedirectResponse("/login/", status_code=303)
    return await _render_invites(request, user, session)


@router.post("/my/invites/create/", response_class=HTMLResponse)
async def create_invite(
    request: Request,
    email: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    check_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login/", status_code=303)

    settings = get_settings()
    email = email.strip().lower()
    errors: list[str] = []
    if not email or "@" not in email:
        errors.append("Enter the email address you want to invite.")
    elif not points.can_invite(user, settings.points_per_invited_member):
        errors.append(
            f"You need at least {settings.points_per_invited_member:,} available points to create an invitation."
        )

    code = ""
    if not errors:
        created = await invitations.create_invitation(session, user, email, cost=settings.points_per_invited_member)
        if created is None:
            errors.append("You cannot afford an invitation right now.")
        else:
            _, code = created

    if errors:
        return await _render_invites(request, user, session, errors=errors, invite_email=email)

    log_action(session, user, "invitation.created", target=email)

    # Send the invitation email directly instead of showing a page with the link
    from app.services.email import enqueue_email

    invite_url = f"{settings.app_base_url.rstrip('/')}/register/?invite={code}"
    await enqueue_email(
        session,
        recipient=email,
        subject=f"You're invited to {settings.site_name}",
        template_name="invitation",
        template_data={
            "inviter_name": user.display_name,
            "invite_url": invite_url,
        },
        dedupe_key=f"invitation:{email}:{code}",
    )
    await session.commit()
    request.session["flash_message"] = f"Invitation sent to {email}."
    return RedirectResponse("/my/invites/", status_code=303)


@router.post("/my/invites/{invite_id}/revoke/", response_model=None)
async def revoke_invite(
    request: Request,
    invite_id: uuid.UUID,
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    if user is not None:
        invite = await session.get(Invitation, invite_id)
        if invite is not None and invite.invited_by == user.id and invite.status == InvitationStatus.PENDING:
            await invitations.release_invitation(session, invite, revoked=True)
            log_action(session, user, "invitation.revoked", target=invite.invitee_email)
            await session.commit()
    return RedirectResponse("/my/invites/", status_code=303)


@router.get("/my/contributions/", response_class=HTMLResponse)
async def my_contributions(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    if user is None:
        return RedirectResponse("/login/", status_code=303)
    contributions = (
        await session.scalars(select(Contribution).where(Contribution.user_id == user.id).order_by(Contribution.created_at.desc()))
    ).all()
    return _render(request, "member/contributions.html", "My contributions", contributions=contributions)


_IMDB_URL = re.compile(r"(?:imdb\.com/title/)?(tt\d{6,})", re.IGNORECASE)
# A vault needs at least two sources: the IMDb page plus one real file link.
_MIN_LINKS = 2
_MAX_LINKS = 10


def _imdb_id(url: str) -> str:
    """Extract a tt-id from an IMDb URL, or '' when it is not one."""
    match = _IMDB_URL.search(url or "")
    return match.group(1) if match else ""


def _link_rows(raw_labels: str, raw_urls: str) -> list[tuple[str, str]]:
    """Read the two textareas as one-per-line label/url columns."""
    labels = [line.strip() for line in (raw_labels or "").splitlines()]
    urls = [line.strip() for line in (raw_urls or "").splitlines()]
    return vault.parse_links(labels, urls)


@router.get("/contribute/", response_class=HTMLResponse)
async def contribute_form(
    request: Request, user: User | None = Depends(get_current_user), session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """The one upload path for movies and series, with their downloads links."""
    if user is None:
        return RedirectResponse("/login/", status_code=303)
    return _render(
        request, "member/contribute.html", "Contribute",
        instant=vault.publishes_instantly(user),
        min_links=_MIN_LINKS,
        form={},
    )


@router.post("/contribute/", response_class=HTMLResponse)
async def submit_contribution(
    request: Request,
    kind: str = Form("movie"),
    imdb_url: str = Form(""),
    link_labels: str = Form(""),
    link_urls: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Submit a movie or series with its downloads links.

    Staff and auto-approve users publish straight away; everyone else creates a
    draft that an admin publishes from the vault queue. The title, year, poster,
    and description all come from the IMDb page — nothing else is typed.
    """
    check_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login/", status_code=303)

    if kind not in ("movie", "series"):
        kind = "movie"
    imdb_url = imdb_url.strip()
    imdb_id = _imdb_id(imdb_url)
    errors: list[str] = []

    if not imdb_url or not imdb_id:
        errors.append("Enter an IMDb link. Use the form https://www.imdb.com/title/tt1234567/.")

    links = _link_rows(link_labels, link_urls)
    if len(links) < _MIN_LINKS:
        errors.append(f"Add at least {_MIN_LINKS} links: the IMDb page and a source link.")
    if len(links) > _MAX_LINKS:
        errors.append(f"Add at most {_MAX_LINKS} links.")
    for _label, url in links:
        if not safe_url(url):
            errors.append(f"'{url}' is not a valid http(s) link.")
            break

    if errors:
        return _render(
            request, "member/contribute.html", "Contribute", status_code=400,
            errors=errors, form={"kind": kind, "imdb_url": imdb_url,
                                 "link_labels": link_labels, "link_urls": link_urls},
            instant=vault.publishes_instantly(user), min_links=_MIN_LINKS,
        )

    # The IMDb page is the source of truth for the title. A scrape failure cannot be
    # submitted either — the form exists to import IMDb, not to type a title by hand.
    data: dict | None = None
    if not errors:
        from app.scripts.import_imdb import load_v2

        try:
            data = await asyncio.to_thread(load_v2().get_title_details, imdb_url)
        except Exception:
            errors.append("Could not read that IMDb page. Check the link and try again.")
            return _render(
                request, "member/contribute.html", "Contribute", status_code=400,
                errors=errors, form={"kind": kind, "imdb_url": imdb_url,
                                     "link_labels": link_labels, "link_urls": link_urls},
                instant=vault.publishes_instantly(user), min_links=_MIN_LINKS,
            )
        if not (data or {}).get("title"):
            errors.append("That IMDb page has no title. Check the link and try again.")
            return _render(
                request, "member/contribute.html", "Contribute", status_code=400,
                errors=errors, form={"kind": kind, "imdb_url": imdb_url,
                                     "link_labels": link_labels, "link_urls": link_urls},
                instant=vault.publishes_instantly(user), min_links=_MIN_LINKS,
            )

    instant = vault.publishes_instantly(user)
    status = MovieStatus.PUBLISHED if instant else MovieStatus.DRAFT
    model = Series if kind == "series" else Movie
    item = model(
        title=(data.get("title") or "").strip()[:200],
        slug=await unique_slug(session, model, slugify(data.get("title") or "")),
        status=status,
    )
    session.add(item)

    # Fill title/year/poster/description/genres from IMDb while the row is still pending.
    from app.scripts.import_imdb import enrich_title

    await enrich_title(session, item, data)
    await session.flush()

    # The IMDb page is part of the vault, so the queue always has the identity link.
    all_links = list(links)
    if not any("imdb.com" in url.lower() for _label, url in all_links):
        all_links.insert(0, ("IMDb", f"https://www.imdb.com/title/{imdb_id}/"))

    settings = get_settings()
    for label, url in all_links:
        link = VaultLink(
            movie_id=item.id if model is Movie else None,
            series_id=item.id if model is Series else None,
            url=url,
            label=label or "Link",
            kind=vault.kind_for(url),
            added_by=user.id,
            status=VaultStatus.PENDING,
        )
        session.add(link)
        if instant:
            # Staff/auto-approve submissions skip the queue, but still earn the reward
            # once per link. approve_link credits via the idempotency key, so a retry
            # (or a later admin approve) cannot double-pay.
            await session.flush()
            await vault.approve_link(session, user, link, settings.points_per_approved_contribution)

    log_action(session, user, "contribution.submitted", target=str(item.title)[:255], detail=kind)
    session.add(
        Contribution(
            user_id=user.id,
            title=item.title[:200],
            payload=json.dumps({"kind": kind, "movie_id": str(item.id) if model is Movie else None, "series_id": str(item.id) if model is Series else None}),
            status=ContributionStatus.APPROVED if instant else ContributionStatus.PENDING,
            points_reward=0,
        )
    )
    await session.commit()
    request.session["flash_message"] = (
        "Published. Your links are live." if instant
        else "Submitted. An admin will review and publish it."
    )
    return RedirectResponse("/my/contributions/", status_code=303)


@router.post("/movie/{slug}/suggest-link/", response_model=None)
@router.post("/series/{slug}/suggest-link/", response_model=None)
async def suggest_link(
    request: Request,
    slug: str,
    kind: str = Form("movie"),
    url: str = Form(""),
    label: str = Form(""),
    csrf_token: str = Form(""),
    user: User | None = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """A signed-in visitor adds one link to an existing title's vault."""
    check_csrf(request, csrf_token)
    if user is None:
        return RedirectResponse("/login/", status_code=303)

    is_series = request.url.path.startswith("/series/")
    kind = "series" if is_series else "movie"
    model = Series if kind == "series" else Movie
    item = await session.scalar(select(model).where(model.slug == slug))
    if item is None:
        return RedirectResponse("/", status_code=303)

    url = vault.normalise_url(url)
    label = label.strip()[:100] or "Link"
    if not url or not safe_url(url):
        request.session["flash_message"] = "That is not a valid link."
    else:
        session.add(
            VaultLink(
                movie_id=item.id if model is Movie else None,
                series_id=item.id if model is Series else None,
                url=url[:500],
                label=label,
                kind=vault.kind_for(url),
                added_by=user.id,
                status=VaultStatus.APPROVED if vault.publishes_instantly(user) else VaultStatus.PENDING,
                reviewed_by=user.id if vault.publishes_instantly(user) else None,
                reviewed_at=_now() if vault.publishes_instantly(user) else None,
            )
        )
        log_action(session, user, "vault.suggested", target=str(item.title)[:255], detail=url)
        request.session["flash_message"] = "Link submitted for review."
    await session.commit()
    back = "/series/" if model is Series else "/movie/"
    return RedirectResponse(f"{back}{item.slug}/", status_code=303)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(tzinfo=None)
