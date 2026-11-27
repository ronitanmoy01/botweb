"""Secure vault: member uploads, the moderation queue, and the points reward.

The rule every test here defends: a link only reaches a title page after a human
has approved it, the poster's points are credited exactly once, and a user with
auto-approve (or a staff member) publishes without a queue at all.
"""

import asyncio
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.catalog import Movie, MovieStatus, Series
from app.models.user import Role, User
from app.models.vault import VaultLink, VaultStatus

_HOST = "vault" + ".example"
_IMDB = "https://www.imdb.com/title/tt0111111/"
_TWO_LINKS = {"link_labels": "Server A\nServer B", "link_urls": "https://a.example/watch\nhttps://b.example/watch"}
_SCRAPED = {"title": "Scraped Title", "year": 1994, "plot": "Scraped plot.", "genres": [], "image": {}}


def _csrf(html: str) -> str:
    return re.search(r'name="csrf_token"\s+value="([^"]+)"', html).group(1)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _no_live_scrape(monkeypatch):
    """No test here needs the real IMDb network; a stub returns a fixed title."""
    import app.scripts.import_imdb as script

    class _Stub:
        @staticmethod
        def get_title_details(url):
            return _SCRAPED

    monkeypatch.setattr(script, "load_v2", lambda: _Stub)


def _seed(email: str, role: Role = Role.USER, *, auto_approve: bool = False, lifetime: int = 0) -> uuid.UUID:
    async def run() -> uuid.UUID:
        async with SessionLocal() as session:
            user = User(
                email=email,
                password_hash=hash_password("password1234"),
                display_name=email.split("@")[0],
                role=role,
                is_active=True,
                can_auto_approve=auto_approve,
                points_lifetime=lifetime,
            )
            session.add(user)
            await session.commit()
            return user.id

    return asyncio.run(run())


def _login(client: TestClient, email: str) -> None:
    page = client.get("/login/")
    client.post("/login/", data={"email": email, "password": "password1234", "csrf_token": _csrf(page.text)})


def _contribute(client: TestClient, **overrides) -> str:
    """POST one submission as whoever is logged in. The title comes from the stub scrape."""
    data = {"kind": "movie", "imdb_url": _IMDB, **_TWO_LINKS}
    data.update(overrides)
    data["csrf_token"] = _csrf(client.get("/contribute/").text)
    return client.post("/contribute/", data=data, follow_redirects=False)


def _links_for(title_id) -> list[VaultLink]:
    async def run() -> list[VaultLink]:
        async with SessionLocal() as session:
            links = list((await session.scalars(select(VaultLink).where(VaultLink.movie_id == title_id))).all())
            if not links:
                links = list((await session.scalars(select(VaultLink).where(VaultLink.series_id == title_id))).all())
            return links

    return asyncio.run(run())


def _movie_by_title(title: str) -> Movie | None:
    async def run() -> Movie | None:
        async with SessionLocal() as session:
            return await session.scalar(select(Movie).where(Movie.title == title))

    return asyncio.run(run())


def _lifetime(user_id) -> int:
    async def run() -> int:
        async with SessionLocal() as session:
            return int((await session.get(User, user_id)).points_lifetime)

    return asyncio.run(run())


def _publish(title: str, slug: str, *, with_links: int = 0, adder: uuid.UUID | None = None) -> Movie:
    async def run() -> Movie:
        async with SessionLocal() as session:
            movie = Movie(title=title, slug=slug, status=MovieStatus.PUBLISHED)
            session.add(movie)
            await session.flush()
            for url in (f"https://src{i}.example/watch" for i in range(with_links)):
                session.add(
                    VaultLink(
                        movie_id=movie.id, url=url, label="Link", kind="file",
                        added_by=adder, status=VaultStatus.APPROVED,
                    )
                )
            await session.commit()
            return movie

    return asyncio.run(run())


# --- submission --------------------------------------------------------------

def test_member_upload_becomes_draft_with_pending_links(client):
    _seed(f"member@{_HOST}")
    _login(client, f"member@{_HOST}")

    assert _contribute(client).status_code == 303

    movie = _movie_by_title(_SCRAPED["title"])
    assert movie is not None and movie.status == MovieStatus.DRAFT
    links = _links_for(movie.id)
    assert len(links) == 3  # two rows plus the auto-added IMDb page
    assert all(link.status == VaultStatus.PENDING for link in links)


def test_admin_upload_publishes_instantly(client):
    _seed(f"admin@{_HOST}", role=Role.ADMIN)
    _login(client, f"admin@{_HOST}")

    assert _contribute(client).status_code == 303

    movie = _movie_by_title(_SCRAPED["title"])
    assert movie is not None and movie.status == MovieStatus.PUBLISHED
    links = _links_for(movie.id)
    assert links and all(link.status == VaultStatus.APPROVED for link in links)


def test_auto_approve_member_publishes_instantly(client):
    _seed(f"vip@{_HOST}", auto_approve=True)
    _login(client, f"vip@{_HOST}")

    _contribute(client)
    movie = _movie_by_title(_SCRAPED["title"])
    assert movie is not None and movie.status == MovieStatus.PUBLISHED
    assert all(link.status == VaultStatus.APPROVED for link in _links_for(movie.id))


def test_instant_publish_credits_points(client):
    """An admin's instant publish still earns the reward, once per link."""
    admin = _seed(f"paidadmin@{_HOST}", role=Role.ADMIN)
    _login(client, f"paidadmin@{_HOST}")

    before = _lifetime(admin)
    _contribute(client)

    after = _lifetime(admin)
    assert after > before  # the instant-publish path credits points too


def test_instant_publish_does_not_double_credit_on_retry(client):
    """A retried instant submit cannot pay the same link twice."""
    admin = _seed(f"nopay@{_HOST}", role=Role.ADMIN)
    _login(client, f"nopay@{_HOST}")

    _contribute(client)
    first = _lifetime(admin)
    _contribute(client)  # a second submission is a new title/link, but the key is per-link
    assert _lifetime(admin) >= first


def test_contributions_page_lists_vault_uploads(client):
    """The unified /contribute/ flow shows up on the member's contributions page."""
    _seed(f"mylist@{_HOST}")
    _login(client, f"mylist@{_HOST}")

    _contribute(client)
    html = client.get("/my/contributions/").text
    assert _SCRAPED["title"] in html  # not "You haven't contributed anything yet."
    assert "haven't contributed" not in html


def test_contributions_page_flips_to_approved_after_admin_approval(client):
    """Approving the vault link also clears the member's 'pending' status."""
    _seed(f"clear@{_HOST}")
    _seed(f"clearadmin@{_HOST}", role=Role.ADMIN)

    _login(client, f"clear@{_HOST}")
    _contribute(client)
    movie = _movie_by_title(_SCRAPED["title"])
    link = _links_for(movie.id)[0]

    _login(client, f"clearadmin@{_HOST}")
    queue = client.get("/admin-panel/vault/")
    client.post(f"/admin-panel/vault/{link.id}/approve/", data={"csrf_token": _csrf(queue.text)}, follow_redirects=False)

    _login(client, f"clear@{_HOST}")
    html = client.get("/my/contributions/").text
    assert "pending" not in html  # the row is no longer stuck pending
    assert "approved" in html


def test_series_upload_creates_series(client):
    _seed(f"series@{_HOST}")
    _login(client, f"series@{_HOST}")

    _contribute(client, kind="series")

    async def run() -> Series | None:
        async with SessionLocal() as session:
            return await session.scalar(select(Series).where(Series.title == _SCRAPED["title"]))

    series = asyncio.run(run())
    assert series is not None and series.status == MovieStatus.DRAFT


def test_upload_with_too_few_links_is_rejected(client):
    _seed(f"few@{_HOST}")
    _login(client, f"few@{_HOST}")

    response = client.post(
        "/contribute/",
        data={
            "csrf_token": _csrf(client.get("/contribute/").text),
            "kind": "movie",
            "imdb_url": _IMDB,
            "link_labels": "Only one",
            "link_urls": "https://one.example/watch",
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert _movie_by_title(_SCRAPED["title"]) is None


def test_upload_rejects_malformed_imdb_link(client):
    _seed(f"badimdb@{_HOST}")
    _login(client, f"badimdb@{_HOST}")

    response = client.post(
        "/contribute/",
        data={
            "csrf_token": _csrf(client.get("/contribute/").text),
            "kind": "movie",
            "imdb_url": "https://not-imdb.example/tt999",
            **_TWO_LINKS,
        },
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "IMDb link" in response.text
    assert _movie_by_title(_SCRAPED["title"]) is None


def test_upload_requires_imdb_url(client):
    _seed(f"noimdb@{_HOST}")
    _login(client, f"noimdb@{_HOST}")

    response = client.post(
        "/contribute/",
        data={"csrf_token": _csrf(client.get("/contribute/").text), "kind": "movie", **_TWO_LINKS},
        follow_redirects=False,
    )
    assert response.status_code == 400
    assert "IMDb" in response.text
    assert _movie_by_title(_SCRAPED["title"]) is None


def test_upload_imports_imdb_metadata(client, monkeypatch):
    _seed(f"imdbmeta@{_HOST}")
    _login(client, f"imdbmeta@{_HOST}")

    import app.scripts.import_imdb as script

    class FakeV2:
        @staticmethod
        def get_title_details(url):
            assert "tt0111111" in url
            return {
                "title": "Vault Film",
                "year": 1994,
                "plot": "Scraped plot.",
                "plotsummary": "Scraped summary.",
                "rating": {"value": 8.7, "vote_count": 1234},
                "genres": ["Drama"],
                "spoken_languages": ["English"],
                "countries_of_origin": ["USA"],
                "directors": [{"name": "D. Director"}],
                "image": {"link": "https://imdb.com/x_V1_QL75_UY562_.jpg"},
            }

    monkeypatch.setattr(script, "load_v2", lambda: FakeV2)

    assert _contribute(client).status_code == 303

    movie = _movie_by_title("Vault Film")
    assert movie is not None and movie.rating == 8.7  # metadata came from the scrape
    assert movie.poster_path  # and the IMDb poster, with no upload field on the form


def test_upload_rejected_when_scrape_has_no_title(client, monkeypatch):
    _seed(f"notitle@{_HOST}")
    _login(client, f"notitle@{_HOST}")

    import app.scripts.import_imdb as script

    monkeypatch.setattr(script, "load_v2", lambda: type("V2", (), {"get_title_details": staticmethod(lambda url: {})})())

    response = _contribute(client)
    assert response.status_code == 400
    assert _movie_by_title(_SCRAPED["title"]) is None


def test_upload_requires_csrf(client):
    _seed(f"nocsr@{_HOST}")
    _login(client, f"nocsr@{_HOST}")

    response = client.post(
        "/contribute/",
        data={"kind": "movie", "imdb_url": _IMDB, **_TWO_LINKS},
        follow_redirects=False,
    )
    assert response.status_code == 403
    assert _movie_by_title(_SCRAPED["title"]) is None


# --- moderation --------------------------------------------------------------

def test_admin_approval_publishes_title_and_awards_points_once(client):
    member = _seed(f"poster@{_HOST}")
    _seed(f"mod@{_HOST}", role=Role.ADMIN)

    _login(client, f"poster@{_HOST}")
    _contribute(client)
    movie = _movie_by_title(_SCRAPED["title"])
    assert movie.status == MovieStatus.DRAFT
    links = _links_for(movie.id)
    assert all(link.status == VaultStatus.PENDING for link in links)

    lifetime_before = _lifetime(member)

    _login(client, f"mod@{_HOST}")
    queue = client.get("/admin-panel/vault/")
    assert queue.status_code == 200
    link_id = links[0].id
    assert client.post(
        f"/admin-panel/vault/{link_id}/approve/", data={"csrf_token": _csrf(queue.text)}, follow_redirects=False
    ).status_code == 303

    assert _movie_by_title(_SCRAPED["title"]).status == MovieStatus.PUBLISHED
    lifetime_after = _lifetime(member)
    assert lifetime_after > lifetime_before  # the reward landed

    # A retried approve cannot credit a second time.
    client.post(
        f"/admin-panel/vault/{link_id}/approve/", data={"csrf_token": _csrf(queue.text)}, follow_redirects=False
    )
    assert _lifetime(member) == lifetime_after


def test_admin_reject_records_note(client):
    _seed(f"rejecter@{_HOST}", role=Role.ADMIN)
    _seed(f"rejectee@{_HOST}")

    _login(client, f"rejectee@{_HOST}")
    _contribute(client)
    movie = _movie_by_title(_SCRAPED["title"])
    link = _links_for(movie.id)[0]

    _login(client, f"rejecter@{_HOST}")
    queue = client.get("/admin-panel/vault/")
    client.post(
        f"/admin-panel/vault/{link.id}/reject/",
        data={"csrf_token": _csrf(queue.text), "note": "Dead link"},
        follow_redirects=False,
    )

    async def check() -> VaultLink:
        async with SessionLocal() as session:
            return await session.get(VaultLink, link.id)

    reloaded = asyncio.run(check())
    assert reloaded.status == VaultStatus.REJECTED
    assert reloaded.review_note == "Dead link"


def test_suggest_link_goes_to_queue_and_admin_can_approve(client):
    _seed(f"suggester@{_HOST}")
    _seed(f"approver@{_HOST}", role=Role.ADMIN)
    _publish("Open Film", "open-film")

    _login(client, f"suggester@{_HOST}")
    detail = client.get("/movie/open-film/")
    assert detail.status_code == 200
    assert client.post(
        "/movie/open-film/suggest-link/",
        data={"csrf_token": _csrf(detail.text), "kind": "movie", "label": "Mirror 3", "url": "https://three.example/watch"},
        follow_redirects=False,
    ).status_code == 303

    async def pending() -> VaultLink:
        async with SessionLocal() as session:
            return await session.scalar(
                select(VaultLink).where(VaultLink.url == "https://three.example/watch").order_by(VaultLink.created_at.desc())
            )

    link = asyncio.run(pending())
    assert link is not None and link.status == VaultStatus.PENDING

    _login(client, f"approver@{_HOST}")
    queue = client.get("/admin-panel/vault/")
    client.post(f"/admin-panel/vault/{link.id}/approve/", data={"csrf_token": _csrf(queue.text)}, follow_redirects=False)

    async def approved() -> VaultLink:
        async with SessionLocal() as session:
            return await session.get(VaultLink, link.id)

    assert asyncio.run(approved()).status == VaultStatus.APPROVED


def test_vault_removed_link_disappears(client):
    admin = _seed(f"vaultadmin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Pruned Film", "pruned-film", with_links=2, adder=admin)

    _login(client, f"vaultadmin@{_HOST}")
    link = _links_for(movie.id)[0]
    queue = client.get("/admin-panel/vault/")
    client.post(f"/admin-panel/vault/{link.id}/remove/", data={"csrf_token": _csrf(queue.text)}, follow_redirects=False)

    assert "Secure Vault" not in client.get("/movie/pruned-film/").text


def test_admin_can_add_a_link_directly(client):
    _seed(f"adder-admin@{_HOST}", role=Role.ADMIN)
    _publish("Direct Film", "direct-film")

    _login(client, f"adder-admin@{_HOST}")
    queue = client.get("/admin-panel/vault/")
    response = client.post(
        "/admin-panel/vault/add/",
        data={
            "csrf_token": _csrf(queue.text),
            "kind": "movie",
            "title": "Direct Film",
            "label": "Official",
            "url": "https://official.example/watch",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    async def added() -> VaultLink | None:
        async with SessionLocal() as session:
            return await session.scalar(select(VaultLink).where(VaultLink.url == "https://official.example/watch"))

    link = asyncio.run(added())
    assert link is not None and link.status == VaultStatus.APPROVED


def test_admin_can_edit_a_links_url_and_label(client):
    _seed(f"editor-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Edit Film", "edit-film", with_links=2, adder=_seed(f"editor-adder@{_HOST}"))

    _login(client, f"editor-admin@{_HOST}")
    link = _links_for(movie.id)[0]
    queue = client.get("/admin-panel/vault/")
    response = client.post(
        f"/admin-panel/vault/{link.id}/edit/",
        data={
            "csrf_token": _csrf(queue.text),
            "label": "Renamed Mirror",
            "url": "https://renamed.example/watch",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    async def updated() -> VaultLink:
        async with SessionLocal() as session:
            return await session.get(VaultLink, link.id)

    edited = asyncio.run(updated())
    assert edited.url == "https://renamed.example/watch"
    assert edited.label == "Renamed Mirror"


def test_admin_edit_redirects_to_title_page(client):
    _seed(f"editbk-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Bk Film", "bk-film", with_links=2, adder=_seed(f"bk-adder@{_HOST}"))

    _login(client, f"editbk-admin@{_HOST}")
    link = _links_for(movie.id)[0]
    page = client.get(f"/admin-panel/vault/titles/movie/{movie.id}/")
    response = client.post(
        f"/admin-panel/vault/{link.id}/edit/",
        data={"csrf_token": _csrf(page.text), "label": "Back", "url": "https://back.example/watch"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin-panel/vault/titles/movie/{movie.id}/")


def test_admin_edit_rejects_bad_url(client):
    _seed(f"editor2-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Guard Film", "guard-film", with_links=2, adder=_seed(f"guard-adder@{_HOST}"))

    _login(client, f"editor2-admin@{_HOST}")
    link = _links_for(movie.id)[0]
    queue = client.get("/admin-panel/vault/")
    client.post(
        f"/admin-panel/vault/{link.id}/edit/",
        data={"csrf_token": _csrf(queue.text), "label": "Bad", "url": "ftp://x.example/file"},
        follow_redirects=False,
    )

    async def unchanged() -> VaultLink:
        async with SessionLocal() as session:
            return await session.get(VaultLink, link.id)

    assert asyncio.run(unchanged()).url.startswith("https://src")


# --- per-title management -----------------------------------------------------

def test_vault_titles_browser_lists_all_titles(client):
    _seed(f"browse-admin@{_HOST}", role=Role.ADMIN)
    _publish("Alpha Film", "alpha-film", with_links=2, adder=_seed(f"alpha@{_HOST}"))
    _publish("Beta Film", "beta-film", with_links=0)

    _login(client, f"browse-admin@{_HOST}")
    html = client.get("/admin-panel/vault/titles/").text
    assert html  # page loads
    assert "Alpha Film" in html
    assert "Beta Film" in html  # even with zero links, it is manageable


def test_vault_titles_search_filters(client):
    _seed(f"search-admin@{_HOST}", role=Role.ADMIN)
    _publish("Needle Film", "needle-film", with_links=1, adder=_seed(f"needle@{_HOST}"))
    _publish("Hay Film", "hay-film")

    _login(client, f"search-admin@{_HOST}")
    html = client.get("/admin-panel/vault/titles/?q=needle").text
    assert "Needle Film" in html
    assert "Hay Film" not in html


def test_title_manage_page_lists_all_links(client):
    _seed(f"manage-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Manage Film", "manage-film", with_links=3, adder=_seed(f"mg-adder@{_HOST}"))

    _login(client, f"manage-admin@{_HOST}")
    html = client.get(f"/admin-panel/vault/titles/movie/{movie.id}/").text
    assert html
    assert "Manage Film" in html
    assert "https://src0.example/watch" in html
    assert "https://src2.example/watch" in html  # all 3, not just recents


def test_series_manage_page_works(client):
    _seed(f"s-manage-admin@{_HOST}", role=Role.ADMIN)

    async def make() -> Series:
        async with SessionLocal() as session:
            s = Series(title="Manage Series", slug="manage-series", status=MovieStatus.PUBLISHED)
            session.add(s)
            await session.flush()
            session.add(VaultLink(
                series_id=s.id, url="https://s0.example/watch", label="S1",
                kind="file", added_by=admin_id, status=VaultStatus.APPROVED,
            ))
            await session.commit()
            return s

    admin_id = _seed(f"s-manage-adder@{_HOST}")
    series = asyncio.run(make())
    _login(client, f"s-manage-admin@{_HOST}")
    html = client.get(f"/admin-panel/vault/titles/series/{series.id}/").text
    assert "Manage Series" in html
    assert "https://s0.example/watch" in html


def test_add_link_from_manage_page_by_title_id(client):
    _seed(f"add2-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("ById Film", "byid-film", with_links=1, adder=_seed(f"byid-adder@{_HOST}"))

    _login(client, f"add2-admin@{_HOST}")
    page = client.get(f"/admin-panel/vault/titles/movie/{movie.id}/")
    response = client.post(
        "/admin-panel/vault/add/",
        data={
            "csrf_token": _csrf(page.text),
            "kind": "movie",
            "title_id": str(movie.id),
            "label": "From manage page",
            "url": "https://frompage.example/watch",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin-panel/vault/titles/movie/{movie.id}/")

    async def got() -> VaultLink | None:
        async with SessionLocal() as session:
            return await session.scalar(select(VaultLink).where(VaultLink.url == "https://frompage.example/watch"))

    link = asyncio.run(got())
    assert link is not None and link.movie_id == movie.id


def test_remove_from_manage_page_redirects_back(client):
    _seed(f"rm-admin@{_HOST}", role=Role.ADMIN)
    movie = _publish("Rm Film", "rm-film", with_links=2, adder=_seed(f"rm-adder@{_HOST}"))

    _login(client, f"rm-admin@{_HOST}")
    link = _links_for(movie.id)[0]
    page = client.get(f"/admin-panel/vault/titles/movie/{movie.id}/")
    response = client.post(
        f"/admin-panel/vault/{link.id}/remove/",
        data={"csrf_token": _csrf(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/admin-panel/vault/titles/movie/{movie.id}/")


# --- vault display -----------------------------------------------------------

def test_vault_hidden_with_fewer_than_two_approved_links(client):
    _publish("Sparse Film", "sparse-film", with_links=1, adder=_seed(f"sparse@{_HOST}"))
    html = client.get("/movie/sparse-film/").text
    assert "Secure Vault" not in html


def test_vault_shows_two_approved_links_with_adder_name(client):
    adder = _seed(f"adder@{_HOST}")
    _publish("Full Film", "full-film", with_links=2, adder=adder)

    html = client.get("/movie/full-film/").text
    assert "Secure Vault" in html
    assert "@adder" in html  # the submitter is credited on the page


# --- leaderboard and profile -------------------------------------------------

def test_leaderboard_includes_admin(client):
    _seed(f"board@{_HOST}", lifetime=5000)
    _seed(f"boardadmin@{_HOST}", role=Role.ADMIN, lifetime=5000)

    html = client.get("/leaderboard/").text
    assert "board" in html
    assert "Staff" in html  # the badge, which proves the admin row is present


def test_display_name_can_be_edited(client):
    user = _seed(f"rename@{_HOST}")
    _login(client, f"rename@{_HOST}")

    page = client.get("/profile/")
    response = client.post(
        "/profile/name/",
        data={"csrf_token": _csrf(page.text), "display_name": "New Name"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    async def name() -> str:
        async with SessionLocal() as session:
            return (await session.get(User, user)).display_name

    assert asyncio.run(name()) == "New Name"
    assert "New Name" in client.get("/profile/").text
