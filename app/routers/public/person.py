from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.models.catalog import Movie, MovieStatus, Series
from app.services.media import slugify

router = APIRouter(include_in_schema=False)


def _contains_person(fields: list[str], slug: str) -> bool:
    """True if any comma-separated field holds a person whose slug matches."""
    for field in fields:
        if not field:
            continue
        for name in field.split(","):
            if slugify(name.strip()) == slug:
                return True
    return False


def _matching_name(fields: list[str], slug: str) -> str | None:
    """Return the exact stored name for the slug, if present."""
    for field in fields:
        if not field:
            continue
        for name in field.split(","):
            name = name.strip()
            if slugify(name) == slug:
                return name
    return None


@router.get("/person/{slug}/", response_class=HTMLResponse)
async def person_detail(request: Request, slug: str, session: AsyncSession = Depends(get_session)):
    # People are stored as comma-separated names; match by slugifying each name so
    # hyphenated names ("Cho Yeo-jeong" -> cho-yeo-jeong) resolve exactly.
    movies = (
        await session.scalars(
            select(Movie).where(Movie.status == MovieStatus.PUBLISHED).order_by(Movie.release_year.desc())
        )
    ).all()
    series = (
        await session.scalars(
            select(Series).where(Series.status == MovieStatus.PUBLISHED).order_by(Series.release_year.desc())
        )
    ).all()

    movies = [m for m in movies if _contains_person([m.directors, m.writers, m.actors], slug)]
    series = [s for s in series if _contains_person([s.creators, s.directors, s.writers, s.actors], slug)]

    person_name = slug.title()
    for item in movies + series:
        name = _matching_name(
            [item.directors, item.writers, item.actors, getattr(item, "creators", "")], slug
        )
        if name:
            person_name = name
            break

    return templates.TemplateResponse(
        request,
        "public/person_detail.html",
        {
            "page_title": person_name,
            "person_name": person_name,
            "movies": movies,
            "series": series,
        },
    )
