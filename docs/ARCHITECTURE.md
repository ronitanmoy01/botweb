# MovieHub Architecture

## Stack

- Python 3.12, FastAPI, Starlette, Uvicorn, Pydantic v2 and pydantic-settings
- SQLAlchemy 2.x async sessions (`asyncpg` for PostgreSQL, `aiosqlite` for the local SQLite fallback), Alembic
- Jinja2 server-rendered HTML, HTMX for partial updates, Tailwind for styling
- pwdlib (Argon2) and PyJWT for auth (wired in Phase 3)
- Pillow for image processing (Phase 4 onward)
- pytest with FastAPI `TestClient` (httpx transport)
- Dependency management: uv

No Node, React, Django, or separate frontend build is used.

## Layout

```
app/
  main.py              app factory (create_app), static mount, router registration
  core/
    config.py          Settings (MOVIEHUB_* env vars), production secret check
    database.py        async engine, session factory, get_session dependency, Base
    templating.py      Jinja2Templates instance and template globals
  routers/
    health.py          GET /healthz (runs SELECT 1, 503 on failure)
    public/home.py     GET /
templates/
  base.html            shared layout (head, header, main block, footer)
  components/          header.html, footer.html (more partials in later phases)
  public/home.html
static/css/app.css
migrations/            Alembic (async env, metadata = app.core.database.Base.metadata)
tests/                 conftest points the DB at a temp file before app import
docs/ARCHITECTURE.md   this file
```

Planned packages from the spec (`models/`, `schemas/`, `repositories/`, `services/`, `dependencies/`, `routers/admin|auth|member|api/`, `utils/`, `tasks/`) are added in the phase that first needs them, not created empty.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `MOVIEHUB_ENVIRONMENT` | `development` | `development`, `test`, `production` |
| `MOVIEHUB_SECRET_KEY` | dev placeholder | Startup fails in `production` if unchanged |
| `MOVIEHUB_DATABASE_URL` | `sqlite+aiosqlite:///moviehub.db` | Use `postgresql+asyncpg://...` in production |
| `MOVIEHUB_SITE_NAME` | `MovieHub` | |
| `MOVIEHUB_MAX_UPLOAD_BYTES` | 10 MiB | Enforced by upload handling (Phase 4) |

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Inspection and planning | Done (empty repo at start; this document) |
| 2 | Foundation: settings, DB, Alembic, Jinja2, base layout | Done. 6 tests pass; live server checked |
| 3 | Authentication: users, sessions, roles, permissions, admin bootstrap | Done. 18 tests pass. Register/login/logout with CSRF, Argon2, login lockout, role-gated `/admin-panel/` placeholder, `python -m app.scripts.create_admin`. Permissions are role checks only, with no permission table yet |
| 4 | Movie CMS: models, categories, genres, languages, CRUD, uploads, public pages | |
| 5 | TV series: series, seasons, episodes | |
| 6 | Publishing: posts, rich text, drafts, scheduling, moderation | |
| 7 | Admin dashboard: stats, analytics, users, media, homepage builder, settings | |
| 8 | Member features: favorites, watchlists, reviews, comments, membership foundation | |
| 9 | SEO (metadata, sitemap, robots), caching, query optimization, image thumbnails | |
| 10 | Security audit, Docker, deployment docs, README | |

## Decisions and deviations

- **Project location.** Built in place in the existing `botweb` folder rather than a nested `moviehub/` directory.
- **Packaging.** uv project with `package = false`; no `src/` layout. The app is run as `uvicorn app.main:app`.
- **Tailwind and HTMX are loaded from CDNs** in `base.html`. This is fine for development. Production should compile Tailwind (standalone CLI, no Node) and vendor HTMX under `static/` with a pinned SRI hash. Tracked for Phase 10.
- **Header links to unbuilt pages** (`/movies/`, `/series/`, and so on) return 404 until their phases land.
- **No `create_all` at startup.** Schema changes go through Alembic only.
- **Database health** is a real query, so an orchestrator sees a DB outage as unhealthy.

## Open decisions for the owner

1. Content scope: the spec limits publishing to authorized, official, trailer, public-domain, or permitted media. Confirm the operator has rights for any real content before import is enabled.
2. Hosting target for the first deploy (Render, PythonAnywhere, Docker on a VPS) decides whether media goes to local disk or Cloudflare R2.
3. Whether Redis is needed for caching or can wait until traffic justifies it.
4. TMDB: whether an API key will be provided. The importer stays optional either way.
