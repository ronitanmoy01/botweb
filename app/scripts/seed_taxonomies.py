"""Seed common taxonomies: uv run python -m app.scripts.seed_taxonomies"""

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal, engine
from app.models.catalog import Category, Country, Genre, Language
from app.services.media import slugify

_GENRES = [
    "Action", "Adventure", "Animation", "Biography", "Comedy", "Crime", "Documentary",
    "Drama", "Family", "Fantasy", "History", "Horror", "Music", "Musical", "Mystery",
    "Romance", "Sci-Fi", "Sport", "Thriller", "War", "Western"
]

_CATEGORIES = ["HD", "4K", "Exclusive", "Featured", "Trending", "New"]

_LANGUAGES = [
    "English", "Spanish", "French", "German", "Italian", "Japanese", "Korean",
    "Mandarin", "Hindi", "Bengali", "Portuguese", "Russian", "Arabic"
]

_COUNTRIES = [
    "United States", "United Kingdom", "Canada", "Australia", "France", "Germany",
    "Italy", "Spain", "Japan", "South Korea", "India", "Bangladesh", "China",
    "Mexico", "Brazil", "Russia"
]


async def _seed(session: AsyncSession, model, items: list[str]) -> int:
    existing = set(await session.scalars(select(model.name)))
    added = 0
    for name in items:
        if name in existing:
            continue
        slug = slugify(name)
        counter = 1
        base = slug
        while await session.scalar(select(model.id).where(model.slug == slug)):
            slug = f"{base}-{counter}"
            counter += 1
        session.add(model(name=name, slug=slug))
        added += 1
    await session.commit()
    return added


async def _main() -> int:
    async with SessionLocal() as session:
        counts = {
            "genres": await _seed(session, Genre, _GENRES),
            "categories": await _seed(session, Category, _CATEGORIES),
            "languages": await _seed(session, Language, _LANGUAGES),
            "countries": await _seed(session, Country, _COUNTRIES),
        }
    await engine.dispose()

    for kind, count in counts.items():
        print(f"Added {count} {kind}")

    total = sum(counts.values())
    if total == 0:
        print("All taxonomies already exist.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
