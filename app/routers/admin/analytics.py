import csv
import io
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.templating import templates
from app.dependencies.auth import require_roles
from app.models.analytics import View
from app.models.catalog import Category, Episode, Movie, Series
from app.models.user import ADMIN_ROLES, User

router = APIRouter(include_in_schema=False)

_CELLS_RE = re.compile(r"[\x00-\x1f\x7f]")
# Titles are admin-entered, so stop spreadsheet apps treating "=cmd(...)" as a formula.
_CSV_DANGER = ("=", "+", "-", "@")


def _csv_cell(value) -> str:
    cleaned = _CELLS_RE.sub("", str(value))
    return "'" + cleaned if cleaned.startswith(_CSV_DANGER) else cleaned


async def _top(session: AsyncSession, entity, view_column, limit: int = 10):
    views = func.count(View.id).label("views")
    rows = await session.execute(
        select(entity, views).join(View, view_column == entity.id).group_by(entity.id).order_by(views.desc()).limit(limit)
    )
    return [(row[0], row[1]) for row in rows]


async def _category_views(session: AsyncSession) -> list[tuple[str, int]]:
    """Views by category, merging movie and series traffic in Python."""
    merged: dict[str, int] = {}
    for entity, view_column in ((Movie, View.movie_id), (Series, View.series_id)):
        rows = await session.execute(
            select(Category, func.count(View.id))
            .join(entity.categories)
            .join(View, view_column == entity.id)
            .group_by(Category.id)
        )
        for category, count in rows:
            merged[category.name] = merged.get(category.name, 0) + count
    return sorted(merged.items(), key=lambda kv: -kv[1])


@router.get("/admin-panel/analytics/", response_class=HTMLResponse)
async def analytics_index(
    request: Request,
    days: int = Query(30, ge=1, le=365),
    export_csv: str = Query("", alias="csv"),
    session: AsyncSession = Depends(get_session),
    admin: User = Depends(require_roles(*ADMIN_ROLES)),
) -> HTMLResponse:
    # SQLite CURRENT_TIMESTAMP (View's default) is UTC, so cutoffs must be UTC too.
    end = datetime.utcnow()
    start = end - timedelta(days=days)
    today_start = end.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today_start - timedelta(days=1)

    total_views = await session.scalar(select(func.count(View.id))) or 0
    unique_visitors = await session.scalar(select(func.count(func.distinct(View.visitor_id)))) or 0
    views_today = await session.scalar(select(func.count(View.id)).where(View.created_at >= today_start)) or 0
    views_yesterday = (
        await session.scalar(
            select(func.count(View.id)).where(View.created_at >= yesterday_start, View.created_at < today_start)
        )
        or 0
    )
    views_7d = await session.scalar(select(func.count(View.id)).where(View.created_at >= end - timedelta(days=7))) or 0
    views_30d = await session.scalar(select(func.count(View.id)).where(View.created_at >= end - timedelta(days=30))) or 0

    top_movies = await _top(session, Movie, View.movie_id)
    top_series = await _top(session, Series, View.series_id)
    top_episodes = await _top(session, Episode, View.episode_id)

    date_rows = await session.execute(
        select(func.date(View.created_at), func.count(View.id))
        .where(View.created_at >= start)
        .group_by(func.date(View.created_at))
        .order_by(func.date(View.created_at))
    )
    views_by_date = [(str(row[0]), row[1]) for row in date_rows]
    max_day_views = max((count for _, count in views_by_date), default=0)

    views_by_category = await _category_views(session)

    # CSV export with full analytics details
    if export_csv:
        buffer = io.StringIO()
        writer = csv.writer(buffer)

        # Summary stats
        writer.writerow(["Analytics Summary"])
        writer.writerow(["Total Views", total_views])
        writer.writerow(["Unique Visitors", unique_visitors])
        writer.writerow(["Views Today", views_today])
        writer.writerow(["Views Yesterday", views_yesterday])
        writer.writerow(["Views (7 days)", views_7d])
        writer.writerow(["Views (30 days)", views_30d])
        writer.writerow([])

        # Views by date
        writer.writerow(["Date", "Views"])
        for day, count in views_by_date:
            writer.writerow([_csv_cell(day), count])
        writer.writerow([])

        # Top movies
        writer.writerow(["Top Movies", "Views"])
        for movie, count in top_movies:
            writer.writerow([_csv_cell(movie.title), count])
        writer.writerow([])

        # Top series
        writer.writerow(["Top Series", "Views"])
        for series, count in top_series:
            writer.writerow([_csv_cell(series.title), count])
        writer.writerow([])

        # Top episodes
        if top_episodes:
            writer.writerow(["Top Episodes", "Views"])
            for episode, count in top_episodes:
                writer.writerow([_csv_cell(episode.title), count])
            writer.writerow([])

        # Views by category
        writer.writerow(["Category", "Views"])
        for category_name, count in views_by_category:
            writer.writerow([_csv_cell(category_name), count])

        return Response(
            content=buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="analytics-{days}d.csv"'},
        )

    return templates.TemplateResponse(
        request,
        "admin/analytics.html",
        {
            "page_title": "Analytics",
            "admin": admin,
            "days": days,
            "total_views": total_views,
            "unique_visitors": unique_visitors,
            "views_today": views_today,
            "views_yesterday": views_yesterday,
            "views_7d": views_7d,
            "views_30d": views_30d,
            "views_by_date": views_by_date,
            "max_day_views": max_day_views,
            "top_movies": top_movies,
            "top_series": top_series,
            "top_episodes": top_episodes,
            "views_by_category": views_by_category,
        },
    )
