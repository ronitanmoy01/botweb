from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import BASE_DIR, get_settings
from app.models.user import ADMIN_ROLES


def _page_context(request: Request) -> dict:
    from app.dependencies.auth import get_csrf_token
    # Set by the global get_current_user dependency in app.main, so every page sees the signed-in user.
    return {
        "current_user": getattr(request.state, "current_user", None),
        "admin_roles": ADMIN_ROLES,
        "csrf_token": get_csrf_token(request),
    }


def _poster_url(path: str | None) -> str:
    """Return the full poster URL (external or local).

    Handles both direct IMDb URLs (https://...) and legacy local uploads
    (uploads/...). Local paths get a leading slash; external URLs pass through.
    """
    if not path:
        return ""
    if path.startswith(("http://", "https://")):
        return path
    return f"/{path}" if not path.startswith("/") else path


templates = Jinja2Templates(directory=BASE_DIR / "templates", context_processors=[_page_context])
templates.env.globals["site_name"] = get_settings().site_name
templates.env.globals["current_year"] = lambda: datetime.now().year
templates.env.filters["poster_url"] = _poster_url
