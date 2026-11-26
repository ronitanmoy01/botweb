from datetime import datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import escape

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


def _truncate(text: str, limit: int) -> str:
    """Cut to a word boundary and add an ellipsis, so meta descriptions stay unique and readable."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.:;") + "..."


def _meta_description(page_title: str = "", movie=None, series=None, heading: str = "") -> str:
    """Build a unique meta description per page so no two URLs share the same snippet.

    Uses the page's own content (title, short_description, listing heading) and falls
    back to a generic line for utility pages. Keeps descriptions 50-155 characters.
    """
    site = get_settings().site_name
    title = page_title or ""
    item = movie or series
    if item is not None and not hasattr(item, "short_description"):
        item = None  # a list (e.g. search results) is not a single title

    if item:
        bits = []
        if item.short_description:
            bits.append(item.short_description)
        elif item.description:
            bits.append(item.description)
        if item.release_year:
            bits.append(f"Released {item.release_year}.")
        if item.rating:
            bits.append(f"Rated {item.rating:.1f} on IMDb.")
        desc = " ".join(bits)
        return _truncate(desc or f"{title} — watch, reviews and cast on {site}.", 155)

    heading = heading or title
    # Utility pages without a listing heading get a page-specific line.
    if title == "Search":
        return f"Search movies and series on {site} by title, genre, language or country."
    if title == "Home":
        return f"Watch movies and TV series on {site} — latest releases, exclusive content, reviews and ratings."
    if title == "My Favorites":
        return f"Your saved favorites on {site} — the movies and series you marked to rewatch."
    if title == "My Watchlist":
        return f"Your watchlist on {site} — titles queued up to watch later."
    if heading:
        return _truncate(f"{heading} — browse the latest titles and series on {site}.", 155)
    return f"{title} on {site} — movies and series, reviews, ratings and cast."


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


def _person_links(names: str) -> str:
    """Convert comma-separated names to clickable links.

    Input: "Bong Joon Ho, Han Jin-won"
    Output: '<a href="/person/bong-joon-ho/">Bong Joon Ho</a>, <a href="/person/han-jin-won/">Han Jin-won</a>'

    Escapes HTML in names to prevent stored XSS from DB-sourced director/writer/actor fields.
    """
    from app.services.media import slugify
    from urllib.parse import quote
    if not names or not names.strip():
        return ""
    parts = [n.strip() for n in names.split(",") if n.strip()]
    # The href is percent-encoded, not HTML-escaped: a '"' in a slug decodes back to
    # a real quote before attribute parsing, so escaping alone cannot keep it inert.
    links = [f'<a href="/person/{quote(slugify(n), safe="")}/" class="text-accent hover:underline">{escape(n)}</a>' for n in parts]
    return ", ".join(links)


templates = Jinja2Templates(directory=BASE_DIR / "templates", context_processors=[_page_context])
templates.env.globals["site_name"] = get_settings().site_name
templates.env.globals["current_year"] = lambda: datetime.now().year
templates.env.globals["meta_description"] = _meta_description
templates.env.filters["poster_url"] = _poster_url
templates.env.filters["person_links"] = _person_links
