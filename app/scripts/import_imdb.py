"""Import IMDb movies and series into the site using the imdb-v2 scraper.

Usage:
    uv run python -m app.scripts.import_imdb https://www.imdb.com/title/tt6751668/
    uv run python -m app.scripts.import_imdb tt6751668 tt0944947

Each title is fetched straight from IMDb by the v2 GraphQL scraper
(imdb-v2.py at the repo root, loaded via importlib because of the hyphen),
then saved as published. A title with the same name and release year that
already exists is skipped, so re-running is safe.
"""

import argparse
import asyncio
import html
import importlib.util
import re
import secrets
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.models.catalog import Country, Genre, Language, Movie, MovieStatus, Series
from app.services.media import slugify, unique_slug

V2_PATH = Path(__file__).resolve().parent.parent.parent / "imdb-v2.py"
# Scraped posters come from IMDb, not from a user upload — cap them independently of
# max_upload_bytes (that limit is for form uploads, and IMDb posters routinely exceed it).
MAX_POSTER_BYTES = 10 * 1024 * 1024


def load_v2():
    """Load the standalone imdb-v2 scraper (hyphenated filename => importlib)."""
    spec = importlib.util.spec_from_file_location("imdb_v2", V2_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["imdb_v2"] = mod
    spec.loader.exec_module(mod)
    return mod


def _names(people) -> str:
    """[{id, name, link}, ...] -> 'Name, Name' ('' when absent)."""
    return ", ".join(
        (p.get("name") or "").strip()
        for p in (people or [])
        if isinstance(p, dict) and p.get("name")
    )


def _awards_text(awards) -> str:
    """{'wins', 'nominations', 'name'} -> '4 wins, 12 nominations (Oscars)'."""
    if not isinstance(awards, dict):
        return (awards or "").strip()[:300]
    parts = []
    if awards.get("wins"):
        parts.append(f"{awards['wins']} wins")
    if awards.get("nominations"):
        parts.append(f"{awards['nominations']} nominations")
    text = ", ".join(parts)
    name = awards.get("name")
    return (f"{text} ({name})" if name and text else (name or text))[:300]


def _box_office_text(box_office) -> str:
    """{'budget', 'opening_weekend', 'gross_worldwide'} -> compact string."""
    if not isinstance(box_office, dict):
        return (box_office or "").strip()[:300]
    parts = []
    for label, key in (
        ("Budget", "budget"),
        ("Opening weekend", "opening_weekend"),
        ("Worldwide gross", "gross_worldwide"),
    ):
        money = box_office.get(key)
        if not isinstance(money, dict) or money.get("amount") is None:
            continue
        amount = money["amount"]
        currency = money.get("currency") or ""
        try:
            amount = f"{float(amount):,.0f}"
        except (TypeError, ValueError):
            amount = str(amount)
        parts.append(f"{label}: {currency}{amount}".strip())
    return "; ".join(parts)[:300]


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def _derive_sized_poster(poster_url):
    """Convert a full-size IMDb poster URL to the small 380x562 crop for low-res thumbnails."""
    if not poster_url:
        return None

    if re.search(r"_V1_\.jpg$", poster_url):
        return re.sub(
            r"_V1_\.jpg$",
            "_V1_QL75_UY562_CR2,0,380,562_.jpg",
            poster_url,
        )

    if re.search(r"_V1_[^/]*\.jpg$", poster_url):
        return re.sub(
            r"_V1_[^/]*\.jpg$",
            "_V1_QL75_UY562_CR2,0,380,562_.jpg",
            poster_url,
        )

    return poster_url


def _download_image(url: str | None) -> str | None:
    """Save an image into the uploads folder, the same place admin uploads go."""
    url = _derive_sized_poster(url)
    if not url:
        return None
    settings = get_settings()
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            content = resp.read(MAX_POSTER_BYTES + 1)
    except OSError as exc:
        print(f"  image download failed, saving without it: {exc}")
        return None
    if len(content) > MAX_POSTER_BYTES:
        print("  image is too large, saving without it")
        return None
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    # shortcut: the extension is always .jpg, no content check, upgrade when non-JPEG images appear
    name = f"{secrets.token_urlsafe(16)}.jpg"
    (settings.uploads_dir / name).write_bytes(content)
    return f"uploads/{name}"


async def _tags(session, model, names: list[str], extras: list[str] = []) -> list:
    """Find each named genre, language or country, creating the ones that do not exist yet."""
    tags = []
    for name in list(names) + list(extras):
        name = (name or "").strip()
        if not name:
            continue
        tag = await session.scalar(select(model).where(model.name == name))
        if tag is None:
            tag = model(name=name, slug=await unique_slug(session, model, slugify(name)))
            session.add(tag)
        if tag not in tags:
            tags.append(tag)
    return tags


async def save_title(session, data: dict) -> str:
    """Save one v2 title dict as a published Movie or Series."""
    if data.get("is_episode"):
        return "skipped: is an episode, import its series instead"
    kind = "series" if data.get("is_series") else "movie"
    model = Series if kind == "series" else Movie
    title = (data.get("title") or "").strip()[:200]
    year = data.get("year")
    if not title:
        return "skipped: no title in scraped data"

    duplicate = select(model.id).where(model.title == title)
    if year:
        duplicate = duplicate.where(model.release_year == year)
    if await session.scalar(duplicate) is not None:
        return f"skipped: {kind} '{title}' ({year}) already exists"

    description = html.unescape(data.get("plotsummary") or data.get("plot") or "").strip()
    plot = html.unescape(data.get("plot") or "").strip()
    rating = data.get("rating") or {}
    metascore = data.get("metascore")
    metascore = metascore.get("score") if isinstance(metascore, dict) else None
    fields = {
        "title": title,
        "original_title": (data.get("original_title") or "").strip()[:200],
        "slug": await unique_slug(session, model, slugify(title)),
        "release_year": year,
        "rating": rating.get("value") if isinstance(rating, dict) else None,
        "imdb_votes": rating.get("vote_count") if isinstance(rating, dict) else None,
        "metascore": metascore,
        "awards": _awards_text(data.get("awards")),
        "directors": _names(data.get("directors")),
        "writers": _names(data.get("writers")),
        "actors": _names(data.get("stars")),
        "age_rating": (data.get("certificate") or "").strip()[:16],
        "short_description": _truncate(plot, 500),
        "description": description,
        "poster_path": _download_image((data.get("image") or {}).get("link")),
        "status": MovieStatus.PUBLISHED,
        "published_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if model is Movie:
        fields["runtime_minutes"] = data.get("runtime_minutes")
        fields["box_office"] = _box_office_text(data.get("box_office"))
    if model is Series:
        fields["creators"] = _names(data.get("creators"))

    item = model(**fields)
    item.genres = await _tags(session, Genre, data.get("genres") or [])
    item.languages = await _tags(session, Language, data.get("spoken_languages") or [])
    item.countries = await _tags(session, Country, data.get("countries_of_origin") or [])
    session.add(item)
    await session.commit()
    return f"saved {kind}: '{title}' ({year}), {len(description)} chars description"


async def _save_all(items: list[dict]) -> int:
    failures = 0
    try:
        async with SessionLocal() as session:
            for data in items:
                try:
                    message = await save_title(session, data)
                except Exception as exc:  # report and continue with the next title
                    await session.rollback()
                    message = f"failed: {exc}"
                    failures += 1
                print(f"  {message}")
    finally:
        await engine.dispose()
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import IMDb movies and series via the v2 scraper.")
    parser.add_argument("urls", nargs="+", help="IMDb title URLs or tt-ids, e.g. https://www.imdb.com/title/tt6751668/")
    args = parser.parse_args(argv)

    v2 = load_v2()
    scraped: list[dict] = []
    failures = 0
    for url in args.urls:
        print(f"Fetching {url}")
        try:
            data = v2.get_title_details(url)
        except Exception as exc:
            print(f"  failed: {exc}")
            failures += 1
            continue
        kind = "series" if data.get("is_series") else "movie"
        print(f"  fetched: {data.get('title')} ({kind})")
        scraped.append(data)

    if scraped:
        print("Saving to database")
        failures += asyncio.run(_save_all(scraped))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
