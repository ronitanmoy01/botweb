"""Phase A foundation fixes: SQLite FK enforcement and person_links XSS escape."""
import asyncio
import re

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.templating import _person_links
from app.models.catalog import Movie, Review
from app.models.user import User


def test_sqlite_foreign_key_cascade_deletes_reviews():
    """Deleting a user cascades to their reviews (requires PRAGMA foreign_keys=ON)."""

    async def run() -> None:
        async with SessionLocal() as session:
            movie = Movie(title="Test Movie", slug="test-movie", status="published")
            user = User(email="reviewer@example.com", password_hash="x", display_name="Reviewer")
            session.add(movie)
            session.add(user)
            await session.commit()
            await session.refresh(movie)
            await session.refresh(user)
            session.add(
                Review(user_id=user.id, movie_id=movie.id, rating=4.5, title="Great!", content="Loved it", is_approved=True)
            )
            await session.commit()
            review_id = (await session.scalar(select(Review.id).where(Review.user_id == user.id)))

        async with SessionLocal() as session:
            fetched_user = await session.get(User, user.id)  # re-read from DB
            await session.delete(fetched_user)
            await session.commit()
            # The orphaned review must be gone, which only happens if FK enforcement is on.
            assert await session.scalar(select(Review.id).where(Review.id == review_id)) is None

    asyncio.run(run())


def test_person_links_escapes_script_tag_in_names():
    """A stored XSS payload in directors/writers/actors renders as text, not markup."""
    output = _person_links("<script>alert(1)</script>, Bong Joon Ho")
    assert "<script>" not in output
    assert "Bong Joon Ho" in output
    assert 'href="/person/' in output  # links are still built


def test_person_links_escapes_attribute_injection_in_slug():
    """Names that would break out of the href attribute are neutralised."""
    output = _person_links('" onload="alert(1)')
    # The href carries a percent-encoded slug with no quotes at all, so a '"' in a
    # director name cannot start a new attribute. Percent-encoding beats HTML
    # escaping here: escaping leaves the quote, and the browser decodes it first.
    href = re.search(r'href="([^"]*)"', output).group(1)
    assert "onload=" not in href
    assert '"' not in href
    assert "=" not in href
    # The visible name is still HTML-escaped: no raw quote survives anywhere, only
    # the &#34; entity, which browsers render as text and never parse as markup.
    assert "&quot;" not in output  # the escape() call uses numeric entities
    assert "&#34;" in output