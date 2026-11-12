from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.models.catalog import Movie, MovieStatus, Series

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    featured_movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.is_featured == True)
            .order_by(Movie.published_at.desc())
            .limit(5)
        )
    ).all()
    featured_series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, Series.is_featured == True)
            .order_by(Series.published_at.desc())
            .limit(5)
        )
    ).all()
    featured = featured_movies + featured_series
    exclusive_movies = (
        await session.scalars(
            select(Movie)
            .where(Movie.status == MovieStatus.PUBLISHED, Movie.is_exclusive == True)
            .order_by(Movie.published_at.desc())
            .limit(10)
        )
    ).all()
    exclusive_series = (
        await session.scalars(
            select(Series)
            .where(Series.status == MovieStatus.PUBLISHED, Series.is_exclusive == True)
            .order_by(Series.published_at.desc())
            .limit(10)
        )
    ).all()
    latest_movies = (
        await session.scalars(
            select(Movie).where(Movie.status == MovieStatus.PUBLISHED).order_by(Movie.published_at.desc()).limit(12)
        )
    ).all()
    latest_series = (
        await session.scalars(
            select(Series).where(Series.status == MovieStatus.PUBLISHED).order_by(Series.published_at.desc()).limit(12)
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "public/home.html",
        {
            "page_title": "Home",
            "featured": featured,
            "exclusives": exclusive_movies + exclusive_series,
            "latest_movies": latest_movies,
            "latest_series": latest_series,
        },
    )
