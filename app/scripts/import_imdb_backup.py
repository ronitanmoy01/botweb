"""Import IMDb movies and series into the site.

Usage (the scraper must be running at --scraper-url):
    uv run python -m app.scripts.import_imdb https://www.imdb.com/title/tt6751668/

Each URL is queued on the scraper, the script waits until the job completes, then saves the
title as published. plotsummary becomes the description. A title with the same name and
release year that already exists is skipped, so re-running is safe.
"""

import argparse
import asyncio
import html
import json
import re
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal, engine
from app.models.catalog import Country, Genre, Language, Movie, MovieStatus, Series
from app.services.media import slugify, unique_slug

DEFAULT_SCRAPER_URL = "http://localhost:9999"
POLL_SECONDS = 5
# The scraper can wait up to 10 minutes for a manual AWS WAF check.
JOB_TIMEOUT_SECONDS = 900


def _call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # The scraper answers a failed job with HTTP 500 and the error text in "detail".
        raise RuntimeError(f"scraper HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


def scrape(base_url: str, imdb_url: str) -> dict:
    """Queue one IMDb URL and return its scraped data once the job reports completed."""
    job = _call("POST", f"{base_url}/scrape", {"url": imdb_url})
    result_url = f"{base_url}{job['result_url']}"
    deadline = time.monotonic() + JOB_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        # The result endpoint answers "queued" or "running" until the job finishes.
        body = _call("GET", result_url)
        if body.get("status") == "completed":
            return body["data"]
        print(f"  job {job['job_id'][:8]}: {body.get('status')}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"scraper job did not finish within {JOB_TIMEOUT_SECONDS} seconds")


def _year(text: str | None) -> int | None:
    # Series show ranges like "2008–2013"; the first year is the release year.
    match = re.search(r"\d{4}", text or "")
    return int(match.group()) if match else None


def _runtime_minutes(text: str | None) -> int | None:
    """Turn '2h 12m', '45m' or '2h' into minutes."""
    if not text:
        return None
    hours = re.search(r"(\d+)\s*h", text)
    minutes = re.search(r"(\d+)\s*m", text)
    total = (int(hours.group(1)) * 60 if hours else 0) + (int(minutes.group(1)) if minutes else 0)
    return total or None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _rating(value) -> float | None:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if 0 <= score <= 10 else None


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0]


def _download_image(url: str | None) -> str | None:
    """Save an image into the uploads folder, the same place admin uploads go."""
    if not url:
        return None
    settings = get_settings()
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            content = resp.read(settings.max_upload_bytes + 1)
    except OSError as exc:
        print(f"  image download failed, saving without it: {exc}")
        return None
    if len(content) > settings.max_upload_bytes:
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
    kind = data.get("type")
    model = Series if kind == "series" else Movie
    title = (data.get("title") or "").strip()[:200]
    year = _year(data.get("release_year"))
    if not title:
        return "skipped: no title in scraped data"

    duplicate = select(model.id).where(model.title == title)
    if year:
        duplicate = duplicate.where(model.release_year == year)
    if await session.scalar(duplicate) is not None:
        return f"skipped: {kind} '{title}' ({year}) already exists"

    description = html.unescape(data.get("plotsummary") or data.get("plot") or "").strip()
    plot = html.unescape(data.get("plot") or "").strip()
    people = lambda key: ", ".join(data.get(key) or [])
    fields = {
        "title": title,
        "original_title": (data.get("original_title") or "").strip()[:200],
        "slug": await unique_slug(session, model, slugify(title)),
        "release_year": year,
        "rating": _rating(data.get("imdb_rating")),
        "imdb_votes": _int(data.get("imdb_votes")),
        "metascore": _int(data.get("metascore")),
        "awards": (data.get("awards") or "").strip()[:300],
        "directors": people("director"),
        "writers": people("writer"),
        "actors": people("actors"),
        "age_rating": (data.get("content_rating") or "").strip()[:16],
        "short_description": _truncate(plot, 500),
        "description": description,
        "poster_path": _download_image(data.get("poster")),
        "backdrop_path": _download_image(data.get("thumbnail")),
        "status": MovieStatus.PUBLISHED,
        "published_at": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    if model is Movie:
        fields["runtime_minutes"] = _runtime_minutes(data.get("runtime"))
        fields["box_office"] = (data.get("box_office") or "").strip()[:50]
    if model is Series:
        fields["creators"] = people("creator")

    item = model(**fields)
    item.genres = await _tags(session, Genre, data.get("genres") or [], data.get("subgenres") or [])
    item.languages = await _tags(session, Language, data.get("language") or [])
    item.countries = await _tags(session, Country, data.get("country") or [])
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
    parser = argparse.ArgumentParser(description="Scrape IMDb titles and save them to the site.")
    parser.add_argument("urls", nargs="+", help="IMDb title URLs, e.g. https://www.imdb.com/title/tt6751668/")
    parser.add_argument("--scraper-url", default=DEFAULT_SCRAPER_URL, help="base URL of the IMDb scraper")
    args = parser.parse_args(argv)

    scraped: list[dict] = []
    failures = 0
    for url in args.urls:
        print(f"Scraping {url}")
        try:
            data = scrape(args.scraper_url.rstrip("/"), url)
        except (RuntimeError, OSError, TimeoutError, KeyError) as exc:
            print(f"  failed: {exc}")
            failures += 1
            continue
        if data.get("type") not in ("movie", "series"):
            print(f"  skipped: type '{data.get('type')}' is not a movie or series")
            failures += 1
            continue
        print(f"  scraped: {data.get('title')} ({data.get('type')})")
        scraped.append(data)

    if scraped:
        print("Saving to database")
        failures += asyncio.run(_save_all(scraped))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
