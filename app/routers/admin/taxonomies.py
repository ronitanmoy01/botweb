import uuid

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import check_csrf, require_roles
from app.models.catalog import Category, Country, Genre, Language
from app.models.user import ADMIN_ROLES, User
from app.services.admin_log import log_action
from app.services.media import slugify, unique_slug

router = APIRouter(include_in_schema=False, prefix="/admin-panel/taxonomies")

_KINDS = {"genre": Genre, "category": Category, "language": Language, "country": Country}
_TAXONOMY_URL = "/admin-panel/taxonomies/"


async def _all(session: AsyncSession, kind: str):
    model = _KINDS[kind]
    return (await session.scalars(select(model).order_by(model.sort_order, model.name))).all()


@router.get("/", response_class=HTMLResponse)
async def taxonomies_dashboard(
    request: Request,
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "admin/taxonomies.html",
        {
            "page_title": "Taxonomies",
            "items_by_kind": {kind: await _all(session, kind) for kind in _KINDS},
            "flash_message": request.session.pop("flash_message", None),
        },
    )


@router.post("/{kind}/add/", response_model=None)
async def item_add(
    request: Request,
    kind: str,
    name: str = Form(""),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    model = _KINDS.get(kind)
    if model and name.strip():
        clean = name.strip()
        if await session.scalar(select(model.id).where(model.name == clean)):
            request.session["flash_message"] = f"“{clean}” already exists."
            return RedirectResponse(_TAXONOMY_URL, status_code=303)
        slug = await unique_slug(session, model, slugify(clean))
        session.add(model(name=clean, slug=slug))
        log_action(session, admin, f"taxonomy.add", target=f"{kind}/{clean}")
        await session.commit()
        request.session["flash_message"] = "Added"
    return RedirectResponse(_TAXONOMY_URL, status_code=303)


@router.post("/{kind}/{item_id}/update/", response_model=None)
async def item_update(
    request: Request,
    kind: str,
    item_id: uuid.UUID,
    name: str = Form(""),
    is_active: bool = Form(False),
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    model = _KINDS.get(kind)
    item = await session.get(model, item_id) if model else None
    if item:
        new_name = name.strip()
        if new_name and new_name != item.name:
            clash = await session.scalar(select(model).where(model.slug == slugify(new_name), model.id != item.id))
            if clash:
                request.session["flash_message"] = f"Cannot rename to “{new_name}”: that name is already used."
                return RedirectResponse(_TAXONOMY_URL, status_code=303)
            item.name = new_name
            item.slug = await unique_slug(session, model, slugify(new_name), exclude_id=item.id)
        item.is_active = is_active
        log_action(session, admin, f"taxonomy.update", target=f"{kind}/{item.name}")
        await session.commit()
        request.session["flash_message"] = "Saved"
    return RedirectResponse(_TAXONOMY_URL, status_code=303)


@router.post("/{kind}/{item_id}/delete/", response_model=None)
async def item_delete(
    request: Request,
    kind: str,
    item_id: uuid.UUID,
    csrf_token: str = Form(""),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
    session: AsyncSession = Depends(get_session),
):
    check_csrf(request, csrf_token)
    model = _KINDS.get(kind)
    item = await session.get(model, item_id) if model else None
    if item:
        log_action(session, admin, f"taxonomy.delete", target=f"{kind}/{item.name}")
        await session.delete(item)
        await session.commit()
        request.session["flash_message"] = "Deleted"
    return RedirectResponse(_TAXONOMY_URL, status_code=303)
