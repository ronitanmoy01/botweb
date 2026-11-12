from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.models.catalog import Movie, MovieStatus, Series

router = APIRouter(include_in_schema=False)


@router.get("/robots.txt")
async def robots(request: Request) -> Response:
    base = str(request.base_url).rstrip("/")
    lines = [
        "User-agent: *",
        "Allow: /",
        f"Sitemap: {base}/sitemap.xml",
        "",
        "Disallow: /admin-panel/",
        "Disallow: /my/",
        "Disallow: /api/",
        "Disallow: /login/",
        "Disallow: /register/",
        "Disallow: /profile/",
    ]
    return Response("\n".join(lines), media_type="text/plain")


@router.get("/sitemap.xml")
async def sitemap(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    base = str(request.base_url).rstrip("/")
    movies = (await session.scalars(select(Movie).where(Movie.status == MovieStatus.PUBLISHED))).all()
    series_list = (await session.scalars(select(Series).where(Series.status == MovieStatus.PUBLISHED))).all()

    def _url(loc: str, lastmod: datetime | None) -> str:
        mod = f"<lastmod>{lastmod.date().isoformat()}</lastmod>" if lastmod else ""
        return f"<url><loc>{base}{loc}</loc>{mod}</url>"

    urls = [
        _url("/", None),
        _url("/movies/", None),
        _url("/series/", None),
        *[_url(f"/movie/{m.slug}/", m.updated_at) for m in movies],
        *[_url(f"/series/{s.slug}/", s.updated_at) for s in series_list],
    ]
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>"
    return Response(xml, media_type="application/xml")
